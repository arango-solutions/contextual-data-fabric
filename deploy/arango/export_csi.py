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
from pathlib import Path

from schema_analyzer.csi import to_csi, validate_csi
from schema_analyzer.tool import run_tool

OUT = Path(__file__).resolve().parents[1] / "csi" / "arango-cmf.json"


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
