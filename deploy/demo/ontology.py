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
