"""Deterministic build, validation, and export operations for catalog v1."""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from cdf.query.catalog import DEFAULT_CONCEPT_BASE, parse_csi_statistics, source_ref_from_csi

from .model import (
    LoadedCatalog,
    canonical_content_hash,
    canonical_generation,
    file_sha256,
    load_manifest,
    parse_manifest,
)

_R2RML_CLASS = re.compile(r"rr:class\s+<([^>]+)>")
_GOVERNANCE_FIELDS = frozenset(
    {"joinKeys", "entitlements", "runtimeResolution", "auth"}
)


def _root_for(path: Path) -> Path:
    resolved = path.resolve()
    for candidate in (resolved.parent, *resolved.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd().resolve()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"artifact {path} is outside catalog root {root}") from exc


def _concept_names(csi: Mapping[str, Any]) -> tuple[str, ...]:
    conceptual = csi.get("conceptualModel")
    if not isinstance(conceptual, dict):
        raise ValueError("CSI conceptualModel must be an object")
    entities = conceptual.get("entities")
    if not isinstance(entities, list) or not entities:
        raise ValueError("CSI conceptualModel.entities must be a non-empty array")
    concepts: list[str] = []
    for entity in entities:
        if not isinstance(entity, dict):
            raise ValueError("CSI conceptualModel.entities items must be objects")
        name = entity.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("CSI conceptual entity names must be non-empty strings")
        concepts.append(name)
    if len(set(concepts)) != len(concepts):
        raise ValueError("CSI conceptual entity names must be unique")
    return tuple(sorted(concepts))


def _property_names(csi: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    conceptual = csi.get("conceptualModel")
    if not isinstance(conceptual, dict):
        return result
    for entity in conceptual.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        for prop in entity.get("properties") or []:
            if isinstance(prop, dict) and isinstance(prop.get("name"), str):
                result.add(prop["name"])
    return result


def _mapping_concepts(path: Path, concept_base: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return {
        iri[len(concept_base) :]
        for iri in _R2RML_CLASS.findall(text)
        if iri.startswith(concept_base)
    }


def _select_mapping(
    source_id: str,
    concepts: tuple[str, ...],
    candidates: list[Path],
    concept_base: str,
) -> Path:
    normalized = source_id.replace(":", "_").replace("-", "_")
    stem_matches = [
        path
        for path in candidates
        if path.stem.replace("-", "_") in {normalized, normalized.split("_", 1)[-1]}
    ]
    if len(stem_matches) == 1:
        return stem_matches[0]
    concept_set = set(concepts)
    content_matches = [
        path
        for path in candidates
        if concept_set and concept_set.issubset(_mapping_concepts(path, concept_base))
    ]
    if len(content_matches) != 1:
        raise ValueError(
            f"cannot select one R2RML mapping for {source_id!r}; "
            f"found {len(content_matches)} content matches"
        )
    return content_matches[0]


def _artifact(
    path: Path,
    root: Path,
    *,
    producer: str,
    direction: str,
    generation: str | None = None,
) -> dict[str, str]:
    digest = file_sha256(path)
    return {
        "path": _relative(path, root),
        "sha256": digest,
        "generation": generation or f"sha256:{digest}",
        "producer": producer,
        "direction": direction,
    }


def _overlay_sources(overlay: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if overlay is None:
        return {}
    unexpected = set(overlay) - {"sources"}
    if unexpected:
        raise ValueError(f"overlay has unknown fields: {', '.join(sorted(unexpected))}")
    sources = overlay.get("sources", {})
    if not isinstance(sources, dict):
        raise ValueError("overlay.sources must be an object keyed by sourceId")
    return sources


def build_manifest_document(
    *,
    csi_paths: Iterable[Path],
    r2rml_paths: Iterable[Path],
    root: Path,
    overlay: Mapping[str, Any] | None = None,
    concept_base: str = DEFAULT_CONCEPT_BASE,
) -> dict[str, Any]:
    """Build one deterministic manifest document from current artifacts."""
    csi_files = sorted((Path(path) for path in csi_paths), key=lambda item: item.as_posix())
    if not csi_files:
        raise ValueError("build requires at least one CSI document")
    mappings = sorted((Path(path) for path in r2rml_paths), key=lambda item: item.as_posix())
    overlay_by_source = _overlay_sources(overlay)

    records: list[tuple[dict[str, Any], dict[str, Any], set[str]]] = []
    property_counts: Counter[str] = Counter()
    for path in csi_files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must contain a CSI JSON object")
        if raw.get("csiVersion") != "1":
            raise ValueError(f"{path} csiVersion must be '1'")
        source = source_ref_from_csi(raw)
        if source.kind == "unknown" or not source.ref:
            raise ValueError(f"{path} requires provenance.source kind and ref")
        concepts = _concept_names(raw)
        props = _property_names(raw)
        property_counts.update(props)
        provenance = raw.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError(f"{path} requires CSI provenance")
        producer = provenance.get("producer")
        direction = provenance.get("direction")
        if not isinstance(producer, str) or not producer:
            raise ValueError(f"{path} requires provenance.producer")
        if direction not in {"forward", "reverse"}:
            raise ValueError(f"{path} requires forward/reverse provenance.direction")
        csi_generation = provenance.get("generatedAt")
        if not isinstance(csi_generation, str) or not csi_generation:
            csi_generation = None
        artifact = _artifact(
            path,
            root,
            producer=producer,
            direction=direction,
            generation=csi_generation,
        )
        record: dict[str, Any] = {
            "sourceId": source.source_id,
            "kind": source.kind,
            "ref": source.ref,
            "concepts": list(concepts),
            "csi": artifact,
            "r2rml": None,
            "statisticsSnapshot": raw.get("statistics"),
        }
        if raw.get("statistics") is not None:
            parse_csi_statistics(raw)
        if source.kind != "arango":
            mapping = _select_mapping(source.source_id, concepts, mappings, concept_base)
            record["r2rml"] = _artifact(
                mapping,
                root,
                producer="r2g",
                direction="forward",
            )
        records.append((record, raw, props))

    known_sources = {record["sourceId"] for record, _, _ in records}
    unknown_overlay = set(overlay_by_source) - known_sources
    if unknown_overlay:
        raise ValueError(
            f"overlay references unknown sources: {', '.join(sorted(unknown_overlay))}"
        )
    for record, _, props in records:
        defaults: dict[str, Any] = {
            "joinKeys": sorted(
                name for name in props if property_counts[name] > 1 and name.endswith("Id")
            ),
            "entitlements": {
                "classification": "internal",
                "allowedRoles": [],
                "mask": "none",
            },
            "runtimeResolution": {
                "mode": "none",
            },
            "auth": {"mode": "service", "delegation": "none"},
        }
        explicit = overlay_by_source.get(record["sourceId"], {})
        if not isinstance(explicit, dict):
            raise ValueError(f"overlay source {record['sourceId']!r} must be an object")
        unexpected = set(explicit) - _GOVERNANCE_FIELDS
        if unexpected:
            raise ValueError(
                f"overlay source {record['sourceId']!r} has unknown fields: "
                f"{', '.join(sorted(unexpected))}"
            )
        record.update(defaults)
        record.update(explicit)

    document: dict[str, Any] = {
        "catalogManifestVersion": "1",
        "conceptBase": concept_base,
        "sources": sorted((item[0] for item in records), key=lambda item: item["sourceId"]),
    }
    document["generation"] = canonical_generation(document)
    document["contentHash"] = canonical_content_hash(document)
    parse_manifest(document)
    return document


def build_manifest(
    *,
    csi_dirs: Iterable[Path],
    r2rml_dirs: Iterable[Path],
    output: Path,
    overlay_path: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    artifact_root = root.resolve() if root is not None else _root_for(output)
    csi_paths = [
        path
        for directory in csi_dirs
        for path in Path(directory).rglob("*.json")
        if path.is_file()
    ]
    r2rml_paths = [
        path
        for directory in r2rml_dirs
        for path in Path(directory).rglob("*.ttl")
        if path.is_file()
    ]
    overlay = None
    if overlay_path is not None:
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        if not isinstance(overlay, dict):
            raise ValueError("overlay must contain a JSON object")
    document = build_manifest_document(
        csi_paths=csi_paths,
        r2rml_paths=r2rml_paths,
        root=artifact_root,
        overlay=overlay,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def validate_manifest(path: Path, *, root: Path | None = None) -> LoadedCatalog:
    """Validate a manifest + its artifacts, returning the loaded catalog so
    callers (the CLI) can run the cross-source label analysis on it."""
    return load_manifest(path, root=root)


def export_catalog(path: Path, target: Path, *, root: Path | None = None) -> list[Path]:
    """Copy validated bytes into ``target/{csi,r2rml}`` without rewriting."""
    loaded = load_manifest(path, root=root)
    outputs: list[Path] = []
    seen: set[Path] = set()
    for source in loaded.manifest.sources:
        pairs = [("csi", loaded.csi_paths[source.source_id])]
        mapping = loaded.r2rml_paths.get(source.source_id)
        if mapping is not None:
            pairs.append(("r2rml", mapping))
        for category, source_path in pairs:
            destination = target / category / source_path.name
            if destination in seen:
                raise ValueError(f"export filename collision: {destination.name}")
            seen.add(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            if file_sha256(destination) != file_sha256(source_path):
                raise ValueError(f"export hash mismatch for {destination}")
            outputs.append(destination)
    return outputs
