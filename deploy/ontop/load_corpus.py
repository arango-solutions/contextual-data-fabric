"""Load the customer-context structured corpus into the demo Postgres (WP-P1.1).

Replaces the toy ``seed.sql`` data with the real synthetic corpus:
``customer-context/data_gen/output/structured/{helio,meridian,northwind}`` —
six tables across three source systems (CRM, DocuSign, Snowflake), every row
carrying the ``AccountId`` business key that is the **locked cross-graph join
spine** (``Chunk → Document.account_id ↔ Account`` — see the P1 close-out plan
WP-P1.4). The ``_origin`` per-field provenance blobs are data-gen metadata, not
business data, and are dropped.

Schema is inferred from the JSON (scalars → text/numeric/boolean/date-ish text;
lists/objects → jsonb) so corpus regeneration doesn't require editing this file.
Idempotent: tables are dropped and recreated per run.

Also applies the CC-7 / CC-11 floor: a read-only ``cdf_demo`` role with
``statement_timeout`` for the query path (Ontop connects as this role once
``ontop.properties`` is pointed at it).

Run (defaults match ``deploy/ontop/docker-compose.yml``)::

    CDF_CORPUS_DIR=~/code/customer-context/data_gen/output/structured \
    PG_DSN=postgresql://cdf:cdf@127.0.0.1:5433/crm \
        .venv/bin/python deploy/ontop/load_corpus.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

TABLES = {
    "accounts": ("crm", "*_crm_accounts.json"),
    "contacts": ("crm", "*_crm_contacts.json"),
    "nps_surveys": ("crm", "*_crm_nps.json"),
    "opportunities": ("crm", "*_crm_opportunities.json"),
    "contracts": ("docusign", "*_docusign_contracts.json"),
    "usage_metrics": ("snowflake", "*_snowflake_usage_metrics.json"),
}

READONLY_ROLE = "cdf_demo"
STATEMENT_TIMEOUT_MS = 15_000


def _snake(name: str) -> str:
    """CamelCase → snake_case (``AccountId`` → ``account_id``).

    Postgres folds unquoted identifiers to lowercase, and Ontop's R2RML
    validation is case-exact — mixed-case columns produced the
    ``placeholder does not occur in source query`` startup failure. Snake_case
    also matches the locked join-key vocabulary (``account_id``).
    """
    out = []
    for i, ch in enumerate(name):
        boundary = not name[i - 1].isupper() or (i + 1 < len(name) and name[i + 1].islower())
        if ch.isupper() and i and boundary:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _sql_type(values: list[Any]) -> str:
    """Infer a column type from every non-null observed value."""
    kinds = {type(v) for v in values}
    if kinds <= {bool}:
        return "boolean"
    if kinds <= {int}:
        return "bigint"
    if kinds <= {int, float}:
        return "double precision"
    if kinds & {list, dict}:
        return "jsonb"
    return "text"


def _collect(corpus_dir: Path, subdir: str, pattern: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for account_dir in sorted(p for p in corpus_dir.iterdir() if p.is_dir()):
        for f in sorted((account_dir / subdir).glob(pattern)):
            rows.extend(json.loads(f.read_text()))
    return [{_snake(k): v for k, v in r.items() if k != "_origin"} for r in rows]


def load(corpus_dir: Path, dsn: str) -> None:
    import psycopg

    from cdf.eval.scale import scale_factor_from_env, scale_rows

    factor = scale_factor_from_env()

    with psycopg.connect(dsn, autocommit=True) as conn:
        for table, (subdir, pattern) in TABLES.items():
            rows = _collect(corpus_dir, subdir, pattern)
            # Scale knob (S1): dependent tables multiply; the accounts table
            # IS the join spine and stays 1x. Verbatim duplicates are fine —
            # every table gets a synthetic bigserial PK below.
            if table != "accounts":
                rows = scale_rows(rows, factor)
            if not rows:
                sys.exit(f"no rows found for {table} under {corpus_dir}/*/{subdir}/{pattern}")

            columns: dict[str, list[Any]] = {}
            for r in rows:
                for k, v in r.items():
                    if v is not None:
                        columns.setdefault(k, []).append(v)
            # Deterministic order: account_id first (the join spine), then sorted.
            names = sorted(columns, key=lambda c: (c != "account_id", c))
            types = {c: _sql_type(columns[c]) for c in names}

            # A synthetic PK on every table: r2g builds the R2RML subject-IRI
            # template from the primary key; without one it falls back to an
            # all-columns template, and R2RML suppresses any row where a
            # template column is NULL (which silently emptied the corpus).
            cols_ddl = ", ".join(f'"{c}" {types[c]}' for c in names)
            conn.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
            conn.execute(f'CREATE TABLE "{table}" (id bigserial PRIMARY KEY, {cols_ddl})')

            placeholders = ", ".join(["%s"] * len(names))
            collist = ", ".join(f'"{c}"' for c in names)
            with conn.cursor() as cur:
                for r in rows:
                    vals = [
                        json.dumps(v) if types[c] == "jsonb" and (v := r.get(c)) is not None
                        else r.get(c)
                        for c in names
                    ]
                    cur.execute(
                        f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders})', vals
                    )
            n = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            print(f"loaded {table:<14} {n:>4} rows  ({len(names)} cols)")

        # CC-7 / CC-11 floor: read-only role with a statement timeout.
        conn.execute(
            f"DO $$ BEGIN CREATE ROLE {READONLY_ROLE} LOGIN PASSWORD 'cdf_demo'; "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )
        conn.execute(f"GRANT CONNECT ON DATABASE crm TO {READONLY_ROLE}")
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {READONLY_ROLE}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READONLY_ROLE}")
        conn.execute(
            f"ALTER ROLE {READONLY_ROLE} SET statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"
        )
        print(f"role {READONLY_ROLE}: SELECT-only, statement_timeout={STATEMENT_TIMEOUT_MS}ms")


if __name__ == "__main__":
    corpus = Path(
        os.environ.get(
            "CDF_CORPUS_DIR",
            str(Path.home() / "code/customer-context/data_gen/output/structured"),
        )
    ).expanduser()
    dsn = os.environ.get("PG_DSN", "postgresql://cdf:cdf@127.0.0.1:5433/crm")
    load(corpus, dsn)
