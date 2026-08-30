"""Ontology-map payload for the demo page (M9; the P3.6 catalog-browser idea
at demo scale).

Shapes the live catalog into the JSON the page's ontology panel renders: every
source's concepts (classes + properties + declared relationships) plus the
cross-source **overlap** a user debugging a federated question needs to see —
the join hubs (``accountId``), label collisions (one word, two meanings), and
synonym clusters (one quantity, several names). Overlap analysis is
:mod:`cdf.catalog.collisions` — the same code `catalog-integrity` runs in CI —
so the panel and the gate can never disagree about what "overlap" means.

Pure over its inputs (:func:`build_ontology_payload`); the thin
:func:`payload_from_repo` wrapper reads the manifest's ``joinKeys`` and the
curator allowlist from ``deploy/catalog/`` with graceful, logged fallbacks.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from cdf.catalog.collisions import Attribute, analyze, humanize, load_allowlist

logger = logging.getLogger(__name__)

#: The locked cross-source business key — the fallback when no manifest exists.
DEFAULT_JOIN_KEYS = ("accountId",)


def _hub_entity_for_key(key: str) -> str:
    """The entity a join key names under CC-12: ``accountId`` → ``Account``.

    Convention-derived, then verified against the catalog by the caller — a
    key with no matching entity yields no spine edges rather than a guess.
    """
    stem = key[:-2] if key.endswith("Id") else key
    return stem[:1].upper() + stem[1:]


def _spine_edges(
    sources: list[dict[str, Any]], join_keys: tuple[str, ...]
) -> list[dict[str, str]]:
    """Dashed diagram edges: join-key carriers → the key's hub entity."""
    entity_source = {
        cls["name"]: src["source_id"] for src in sources for cls in src["classes"]
    }
    # A declared relationship (an actual FK) between a pair outranks the
    # inferred spine edge — draw one edge per pair, the stronger one.
    declared_pairs = {
        frozenset((rel["from"], rel["to"]))
        for src in sources
        for rel in src.get("relationshipDetails", [])
    }
    edges: list[dict[str, str]] = []
    for key in join_keys:
        hub = _hub_entity_for_key(key)
        if hub not in entity_source:
            logger.info(
                "ontology map: join key %r names no catalog entity (%r) — "
                "no spine edges drawn for it", key, hub,
            )
            continue
        for src in sources:
            for cls in src["classes"]:
                if (
                    cls["name"] != hub
                    and key in cls["properties"]
                    and frozenset((cls["name"], hub)) not in declared_pairs
                ):
                    edges.append(
                        {
                            "key": key,
                            "from": cls["name"],
                            "fromSource": src["source_id"],
                            "to": hub,
                            "toSource": entity_source[hub],
                        }
                    )
    return edges


def build_ontology_payload(
    vocabulary: list[dict[str, Any]],
    *,
    join_keys: tuple[str, ...] = DEFAULT_JOIN_KEYS,
    allowed: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Shape a :meth:`SourceCatalog.vocabulary` result + overlap report for the page.

    Args:
        vocabulary: ``SourceCatalog.vocabulary()`` output (per-source classes).
        join_keys: declared cross-source join keys (conceptual names).
        allowed: curator allowlist — collision label → reason it is tolerated.
    """
    sources: list[dict[str, Any]] = []
    attributes: list[Attribute] = []
    for src in vocabulary:
        sources.append(
            {
                "source_id": src["source_id"],
                "kind": src["kind"],
                "ref": src["ref"],
                "classes": src["classes"],
                "relationships": src["relationships"],
                # Structured edges — a diagram is unreadable without them.
                "relationshipDetails": src.get("relationshipDetails", []),
            }
        )
        for cls in src["classes"]:
            for prop in cls["properties"]:
                attributes.append(
                    Attribute(
                        entity=cls["name"],
                        source_id=src["source_id"],
                        producer=src["kind"],
                        name=prop,
                        label=humanize(prop),
                    )
                )

    report = analyze(
        attributes,
        frozenset(humanize(k) for k in join_keys),
        allowed=allowed or {},
    )

    def _attr(a: Attribute) -> dict[str, str]:
        return {"entity": a.entity, "property": a.name, "source_id": a.source_id}

    return {
        "sources": sources,
        "joinKeys": list(join_keys),
        # Cross-source join-SPINE edges for the diagram: every entity carrying a
        # declared join key connects (dashed) to that key's hub entity. The hub
        # is inferred by the CC-12 naming convention — `accountId` names the
        # `Account` entity's key — and verified to exist; keys whose hub isn't
        # in the catalog draw no spine (declared elsewhere, e.g. a future
        # cross-fabric key), never a guessed edge.
        "spine": _spine_edges(sources, join_keys),
        "overlap": {
            "hubs": [
                {
                    "label": h.label,
                    "entityCount": h.entity_count,
                    "totalEntities": h.total_entities,
                }
                for h in report.hubs
            ],
            "collisions": [
                {
                    "label": c.label,
                    "accepted": c.accepted,
                    "reason": c.accepted_reason,
                    "crossSource": c.cross_source,
                    "attributes": [_attr(a) for a in c.attributes],
                }
                for c in report.collisions
            ],
            "synonyms": [
                {"token": s.token, "attributes": [_attr(a) for a in s.attributes]}
                for s in report.synonyms
            ],
        },
    }


def payload_from_repo(catalog: Any, repo_root: Path) -> dict[str, Any]:
    """Build the payload from the live catalog + the repo's curator artifacts.

    Reads ``deploy/catalog/manifest.json`` for the declared ``joinKeys`` and
    ``deploy/catalog/label-collisions-allow.json`` for accepted collisions.
    Either file may be absent (fresh checkout, partial deploy): the payload
    degrades to defaults and says so in the log — never a hard failure, the
    demo page must render even when curation artifacts lag.
    """
    catalog_dir = repo_root / "deploy" / "catalog"

    join_keys: tuple[str, ...] = DEFAULT_JOIN_KEYS
    manifest_path = catalog_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            declared = sorted(
                {k for s in manifest.get("sources", []) for k in s.get("joinKeys", [])}
            )
            if declared:
                join_keys = tuple(declared)
        except (OSError, ValueError) as exc:
            logger.warning(
                "ontology map: unreadable manifest %s (%s) — falling back to %s",
                manifest_path, exc, DEFAULT_JOIN_KEYS,
            )
    else:
        logger.info(
            "ontology map: no catalog manifest at %s — using default join keys %s",
            manifest_path, DEFAULT_JOIN_KEYS,
        )

    allowed: dict[str, str] = {}
    allow_path = catalog_dir / "label-collisions-allow.json"
    if allow_path.is_file():
        try:
            allowed = load_allowlist(allow_path)
        except (OSError, ValueError) as exc:
            logger.warning(
                "ontology map: unreadable allowlist %s (%s) — all collisions "
                "will show as unexpected", allow_path, exc,
            )

    return build_ontology_payload(
        catalog.vocabulary(), join_keys=join_keys, allowed=allowed
    )
