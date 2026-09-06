"""Structural unstructured-corpus build into ArangoDB (WP-P1.3, v0 — no LLM).

Loads ``customer-context/data_gen/output/unstructured/*`` into the demo
ArangoDB as two collections satisfying the **locked join design** (P1 close-out
plan WP-P1.3/P1.4):

- ``documents`` — one per source file: ``account_id`` (the cross-graph join
  spine, derived from the ``{account}_{source}`` module prefix), ``source``
  (slack/email/docs/gong), ``citable_url``, ``role``, ``questions_served`` and
  ``event_date`` from the data-gen manifest.
- ``chunks`` — paragraph-bounded text chunks, each carrying ``document_id``
  **and the denormalized ``account_id`` stamp** (the locked post-build-UPSERT
  equivalent, applied at load).

This is the *structural* v0 build: documents are citable, filterable evidence.
PJ's full pipeline (LLM extraction, embeddings, entities) replaces it without
changing the join contract — the acceptance bar is ``Chunk →
Document.account_id ↔ Account``, not any particular pipeline.

Run (defaults match ``deploy/arango/docker-compose.yml``)::

    CDF_CORPUS_DIR=~/code/customer-context/data_gen/output \
    ARANGO_URL=http://127.0.0.1:8530 ARANGO_PASSWORD=cdf \
        .venv/bin/python deploy/arango/load_corpus.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from arango import ArangoClient

URL = os.getenv("ARANGO_URL", "http://localhost:8529")
DB = os.getenv("ARANGO_DB", "cmf")
USER = os.getenv("ARANGO_USER", "root")
PASSWORD = os.getenv("ARANGO_PASSWORD", "cdf")
CORPUS = Path(
    os.getenv("CDF_CORPUS_DIR", str(Path.home() / "code/customer-context/data_gen/output"))
).expanduser()

CHUNK_TARGET = 1200  # chars; split on paragraph boundaries


def _account_ids(corpus: Path) -> dict[str, str]:
    """{account_key -> account_id} from the structured corpus (the same ids
    loaded into Postgres — the join spine is shared by construction)."""
    out: dict[str, str] = {}
    for d in sorted(p for p in (corpus / "structured").iterdir() if p.is_dir()):
        for f in (d / "crm").glob("*_crm_accounts.json"):
            rows = json.loads(f.read_text())
            if rows:
                out[d.name] = rows[0]["AccountId"]
    return out


def _chunk(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) > CHUNK_TARGET:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks or [text.strip()]


def main() -> None:
    accounts = _account_ids(CORPUS)
    manifest = json.loads((CORPUS / "manifest.json").read_text())

    client = ArangoClient(hosts=URL)
    system = client.db("_system", username=USER, password=PASSWORD)
    if not system.has_database(DB):
        system.create_database(DB)
    db = client.db(DB, username=USER, password=PASSWORD)

    for name in ("documents", "chunks"):
        if db.has_collection(name):
            db.collection(name).truncate()
        else:
            db.create_collection(name)

    # Scale knob (S1): copy 0 is the verbatim corpus; copies 1..N-1 suffix the
    # document key (-s<i>), and every derived id (chunk keys, _uri, document_id)
    # cascades from it — account_id, the join spine, rides verbatim.
    from cdf.eval.scale import scale_factor_from_env

    factor = scale_factor_from_env()

    n_docs = n_chunks = 0
    for path in sorted((CORPUS / "unstructured").rglob("*.txt")):
        meta = manifest.get(path.name, {})
        module = meta.get("module", path.parent.name)  # "{account}_{source}"
        account_key, _, source = module.partition("_")
        account_id = accounts.get(account_key)
        if account_id is None:
            raise SystemExit(f"unknown account key {account_key!r} for {path.name}")

        text = path.read_text(encoding="utf-8")
        # Strip the data-gen header comment; keep the body.
        body = re.sub(r"^<!--.*?-->\s*", "", text, flags=re.S)

        for copy in range(factor):
            doc_key = path.stem if copy == 0 else f"{path.stem}-s{copy}"
            db.collection("documents").insert(
                {
                    "_key": doc_key,
                    "_uri": f"documents/{doc_key}",
                    "account_id": account_id,
                    "source": source,
                    "filename": path.name,
                    "citable_url": meta.get("citable_url"),
                    "role": meta.get("role"),
                    "questions_served": meta.get("questions_served", []),
                    "event_date": meta.get("event_date"),
                },
                overwrite=True,
            )
            n_docs += 1

            for i, chunk in enumerate(_chunk(body)):
                db.collection("chunks").insert(
                    {
                        "_key": f"{doc_key}-{i}",
                        "_uri": f"chunks/{doc_key}-{i}",
                        "document_id": doc_key,
                        "account_id": account_id,  # the locked post-build stamp
                        "seq": i,
                        "text": chunk,
                    },
                    overwrite=True,
                )
                n_chunks += 1

    # Acceptance check (WP-P1.3): every chunk resolves to a document that
    # carries the account_id — and the stamps agree.
    bad = list(
        db.aql.execute(
            """
            FOR c IN chunks
              LET d = DOCUMENT("documents", c.document_id)
              FILTER d == null OR d.account_id != c.account_id
              RETURN c._key
            """
        )
    )
    if bad:
        raise SystemExit(f"account_id stamp check FAILED for {len(bad)} chunks: {bad[:5]}")

    print(
        f"loaded {n_docs} documents / {n_chunks} chunks into {DB} at {URL} "
        f"({len(accounts)} accounts); account_id stamp check: 100%"
    )


if __name__ == "__main__":
    main()
