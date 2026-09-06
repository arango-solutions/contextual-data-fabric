"""Reseed + scale the ClickHouse analytics corpus (scale knob v0, S1).

Compose's initdb only runs ``seed.sql`` on an EMPTY data volume (the named
volume deliberately persists), so this loader makes ClickHouse seeding an
explicit, idempotent step like the other three legs: truncate, re-execute the
in-repo ``seed.sql`` (single source of truth — DDL changes now reapply on
every ``make seed``), then multiply rows per ``CDF_SCALE_FACTOR`` with the
shared seam: ``account_id`` (the join spine) rides every copy verbatim; the
numeric ids are stride-offset so MergeTree rows stay distinct.

    CLICKHOUSE_DSN=clickhouse://cdf:cdf@127.0.0.1:8123/analytics \
      python deploy/clickhouse/scale_corpus.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cdf.eval.scale import scale_factor_from_env, scale_rows

SEED = Path(__file__).with_name("seed.sql")
TABLES = {  # table -> the per-row unique id the copies must perturb
    "analytics.usage_metrics": "id",
    "analytics.query_events": "event_id",
}


def main() -> int:
    import clickhouse_connect  # lazy: engine driver, not a core dep

    dsn = os.environ.get("CLICKHOUSE_DSN")
    if not dsn:
        print("scale_corpus: CLICKHOUSE_DSN is not set", file=sys.stderr)
        return 2
    factor = scale_factor_from_env()
    client = clickhouse_connect.get_client(dsn=dsn)

    # Reseed from the canonical file (idempotent at any prior factor).
    for table in TABLES:
        client.command(f"TRUNCATE TABLE IF EXISTS {table}")
    for statement in SEED.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            client.command(statement)

    for table, id_key in TABLES.items():
        result = client.query(f"SELECT * FROM {table}")
        rows = [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
        scaled = scale_rows(rows, factor, perturb_keys=(id_key,))
        extra = scaled[len(rows):]
        if extra:
            client.insert(
                table,
                [[row[c] for c in result.column_names] for row in extra],
                column_names=list(result.column_names),
            )
        total = client.query(f"SELECT count() FROM {table}").result_rows[0][0]
        print(f"{table}: {len(rows)} x {factor} = {total} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
