"""Emit the reverse CSI for the demo ArangoDB (WP-P1.3b).

The graph-leg analogue of r2g's ``export-csi``: runs ``arango-schema-analyzer``
against the live ``cmf`` database and writes a **reverse** ``CSI v1`` document
(``arango-schema-analyzer`` is the reverse producer; r2g is the forward one —
ADR-0001). The M5 catalog + the ``arango-sparql-py`` leg consume it exactly like
the r2g-emitted Postgres CSI — same contract, opposite direction.

Retires the hand-authored ``deploy/csi/arango-tickets.json``: the concept names
(``Document``/``Chunk``/``Ticket``) and their properties (incl. the
``account_id`` join spine) are now *derived from the live collections*, not
declared by hand.

    ARANGO_URL=http://127.0.0.1:8530 ARANGO_PASSWORD=cdf \
        .venv/bin/python deploy/arango/export_csi.py
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from schema_analyzer.csi import to_csi, validate_csi
from schema_analyzer.tool import run_tool

OUT = Path(__file__).resolve().parents[1] / "csi" / "arango-cmf.json"
#: Curator-declared attribute references (ASA only derives relationships from
#: edge collections; this corpus links documents by attribute — see the file).
REFS_OVERLAY = Path(__file__).with_name("cmf-refs.overlay.json")


def _camel_join(from_coll: str, to_coll: str) -> str:
    """Relationship type per the r2g CC-12 convention: ``chunksToDocuments``."""
    return f"{from_coll}To{to_coll[:1].upper()}{to_coll[1:]}"


def merge_declared_references(
    csi: dict[str, Any],
    overlay: dict[str, Any],
    containment: Callable[[dict[str, Any]], tuple[int, int]],
) -> list[str]:
    """Merge overlay-declared attribute references into the CSI's relationships.

    Each reference is admitted only when *containment* reports every non-null
    ``attribute`` value resolving in the target collection — a curator
    declaration that stops being true must fail the export loudly, never
    silently drop (or keep) an edge. Returns the admitted relationship types.
    Pure over its inputs (containment is injected) so it is unit-testable
    without a database.
    """
    entity_by_collection = {
        spec.get("collectionName"): name
        for name, spec in (csi.get("arangoPhysicalMapping", {}).get("entities") or {}).items()
    }
    admitted: list[str] = []
    relationships = csi["conceptualModel"].setdefault("relationships", [])
    for ref in overlay.get("references", []):
        total, resolved = containment(ref)
        if total == 0 or resolved != total:
            raise SystemExit(
                f"declared reference {ref['fromCollection']}.{ref['attribute']} -> "
                f"{ref['toCollection']}.{ref.get('toAttribute', '_key')} does not hold "
                f"({resolved}/{total} resolve) — fix the data or remove it from "
                f"{REFS_OVERLAY.name}"
            )
        from_entity = entity_by_collection.get(ref["fromCollection"])
        to_entity = entity_by_collection.get(ref["toCollection"])
        if not from_entity or not to_entity:
            raise SystemExit(
                f"declared reference names a collection the analyzer did not map: "
                f"{ref['fromCollection']} -> {ref['toCollection']}"
            )
        rtype = _camel_join(ref["fromCollection"], ref["toCollection"])
        relationships.append(
            {"type": rtype, "fromEntity": from_entity, "toEntity": to_entity}
        )
        admitted.append(rtype)
    return admitted


def main() -> None:
    conn = {
        "url": os.getenv("ARANGO_URL", "http://127.0.0.1:8530"),
        "database": os.getenv("ARANGO_DB", "cmf"),
        "username": os.getenv("ARANGO_USER", "root"),
        "password": os.getenv("ARANGO_PASSWORD", "cdf"),
    }
    # entityStrategy pinned explicitly: the fabric's conceptual model is one
    # class per collection (documents -> Document). The analyzer's "auto"
    # strategy may discover per-value subtypes (Email/Gong/Slack from
    # Document.source) — right for exploration, wrong for this contract, and
    # a default we must not ride (it changed once and broke the seed).
    resp = run_tool(
        {
            "contractVersion": "1",
            "operation": "analyze",
            "connection": conn,
            "analysisOptions": {"entityStrategy": "collection"},
        }
    )
    if not resp.get("ok"):
        raise SystemExit(f"analyzer failed: {resp.get('error')}")

    csi = to_csi(
        resp["result"]["analysis"],
        direction="reverse",
        source={"kind": "arango", "ref": conn["database"]},
    )

    if REFS_OVERLAY.is_file():
        import arango  # lazy: only the overlay's containment check needs a client

        db = arango.ArangoClient(hosts=conn["url"]).db(
            conn["database"], username=conn["username"], password=conn["password"]
        )

        def _containment(ref: dict) -> tuple[int, int]:
            row = next(
                db.aql.execute(
                    """RETURN {
                         total: LENGTH(FOR c IN @@f FILTER c.@attr != null RETURN 1),
                         resolved: LENGTH(FOR c IN @@f FILTER c.@attr != null
                           FILTER LENGTH(FOR d IN @@t FILTER d.@tattr == c.@attr
                                         LIMIT 1 RETURN 1) > 0 RETURN 1)
                       }""",
                    bind_vars={
                        "@f": ref["fromCollection"],
                        "@t": ref["toCollection"],
                        "attr": ref["attribute"],
                        "tattr": ref.get("toAttribute", "_key"),
                    },
                )
            )
            return row["total"], row["resolved"]

        admitted = merge_declared_references(
            csi, json.loads(REFS_OVERLAY.read_text(encoding="utf-8")), _containment
        )
        print(f"declared references admitted (live-verified): {admitted}")

    errs = validate_csi(csi)
    if errs:
        raise SystemExit(f"CSI validation failed: {errs}")

    OUT.write_text(json.dumps(csi, indent=2) + "\n", encoding="utf-8")
    ents = {
        e["name"]: [p["name"] for p in e.get("properties", [])]
        for e in csi["conceptualModel"]["entities"]
    }
    print(f"wrote {OUT.relative_to(Path.cwd())} — entities: {sorted(ents)}")
    for name in ("Document", "Chunk"):
        if "accountId" not in ents.get(name, []):  # CC-12 conceptual name
            raise SystemExit(f"{name} is missing the accountId join key")


if __name__ == "__main__":
    main()
