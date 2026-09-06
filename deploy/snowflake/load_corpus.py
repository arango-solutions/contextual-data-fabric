"""Load the usage-telemetry corpus into Snowflake (WP-S2, Sprint 2 / PRD §7.7).

Sibling of ``deploy/ontop/load_corpus.py``. Reads the data-gen Snowflake corpus
(``customer-context/data_gen/output/structured/*/snowflake/*usage_metrics*.json``)
and creates ``USAGE_METRICS`` in the trial account so the demo becomes a genuine
three-source federation (CRM→Postgres, telemetry→Snowflake, docs→ArangoDB) joined
on ``account_id``.

Two Snowflake-specific choices vs. the Postgres loader:

- **Unquoted → UPPERCASE physical names** (``ACCOUNT_ID``, ``QUERY_VOLUME_M``): this
  is deliberate (PRD §7.7). Snowflake folds unquoted identifiers to uppercase, and
  CC-12's naming layer maps them back to the conceptual vocabulary
  (``USAGE_METRICS``→``UsageMetric``, ``QUERY_VOLUME_M``→``queryVolumeM``).
- **A synthetic ``ID`` PK** (``AUTOINCREMENT``): r2g builds the R2RML subject-IRI
  template from the primary key; without one it falls back to an all-columns
  template and R2RML suppresses any row with a NULL template column (the P1.2
  lesson that silently emptied the corpus).

Loading needs write privileges, so it runs as ``SNOWFLAKE_LOADER_ROLE`` (default
``ACCOUNTADMIN``) — NOT the read-only ``CDF_RO`` the engine uses. The
``GRANT SELECT ON FUTURE TABLES`` in ``setup.sql`` gives ``CDF_RO`` read access to
this new table automatically.

Run (after ``setup.sql`` + credentials in ``.env``)::

    set -a; . ./.env; set +a
    .venv/bin/python deploy/snowflake/load_corpus.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from cdf.adapters.snowflake import build_snowflake_connect_args

TABLE = "USAGE_METRICS"
SUBDIR = "snowflake"
PATTERN = "*_snowflake_usage_metrics.json"


def _upper_snake(name: str) -> str:
    """CamelCase/snake → UPPER_SNAKE (``AccountId``→``ACCOUNT_ID``,
    ``query_volume_m``→``QUERY_VOLUME_M``). Matches Snowflake's uppercase folding
    of unquoted identifiers; CC-12 maps these back to the conceptual layer."""
    out: list[str] = []
    for i, ch in enumerate(name):
        boundary = not name[i - 1].isupper() or (i + 1 < len(name) and name[i + 1].islower())
        if ch.isupper() and i and boundary:
            out.append("_")
        out.append(ch)
    return "".join(out).upper()


def _sql_type(values: list[Any]) -> str:
    """Infer a Snowflake column type from every non-null observed value."""
    kinds = {type(v) for v in values}
    if kinds <= {bool}:
        return "BOOLEAN"
    if kinds <= {int}:
        return "NUMBER"
    if kinds <= {int, float}:
        return "FLOAT"
    if kinds & {list, dict}:
        return "VARIANT"
    return "VARCHAR"


def _collect(corpus_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for account_dir in sorted(p for p in corpus_dir.iterdir() if p.is_dir()):
        for f in sorted((account_dir / SUBDIR).glob(PATTERN)):
            rows.extend(json.loads(f.read_text()))
    # Drop the data-gen _origin provenance blob; uppercase the physical names.
    return [{_upper_snake(k): v for k, v in r.items() if k != "_origin"} for r in rows]


def load(corpus_dir: Path, connect_args: dict[str, Any]) -> None:
    import snowflake.connector

    from cdf.eval.scale import scale_factor_from_env, scale_rows

    rows = scale_rows(_collect(corpus_dir), scale_factor_from_env())
    # Scale knob (S1): account_id rides every copy verbatim (the spine);
    # the synthetic ID column is AUTOINCREMENT, so duplicates are unique rows.
    if not rows:
        sys.exit(f"no rows found under {corpus_dir}/*/{SUBDIR}/{PATTERN}")

    columns: dict[str, list[Any]] = {}
    for r in rows:
        for k, v in r.items():
            if v is not None:
                columns.setdefault(k, []).append(v)
    # Deterministic order: ACCOUNT_ID first (the join spine), then sorted.
    names = sorted(columns, key=lambda c: (c != "ACCOUNT_ID", c))
    types = {c: _sql_type(columns[c]) for c in names}

    conn = snowflake.connector.connect(**{k: v for k, v in connect_args.items() if v})
    try:
        cur = conn.cursor()
        cols_ddl = ", ".join(f"{c} {types[c]}" for c in names)
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        cur.execute(f"CREATE TABLE {TABLE} (ID NUMBER AUTOINCREMENT, {cols_ddl})")

        placeholders = ", ".join(["%s"] * len(names))
        collist = ", ".join(names)
        cur.executemany(
            f"INSERT INTO {TABLE} ({collist}) VALUES ({placeholders})",
            [tuple(r.get(c) for c in names) for r in rows],
        )
        n = cur.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
        by_acct = cur.execute(
            f"SELECT count(DISTINCT ACCOUNT_ID) FROM {TABLE}"
        ).fetchone()[0]
        print(f"loaded {TABLE}: {n} rows, {len(names)} cols, {by_acct} distinct ACCOUNT_ID")
        print(f"columns: {', '.join(names)}")
    finally:
        conn.close()


if __name__ == "__main__":
    corpus = Path(
        os.environ.get(
            "CDF_CORPUS_DIR",
            str(Path.home() / "code/customer-context/data_gen/output/structured"),
        )
    ).expanduser()
    if not os.environ.get("SNOWFLAKE_ACCOUNT"):
        sys.exit("set the SNOWFLAKE_* env (e.g. `set -a; . ./.env; set +a`) first")
    loader_env = dict(os.environ)
    # WRITE role for the load — never the read-only CDF_RO the engine uses.
    loader_env.setdefault("SNOWFLAKE_LOADER_ROLE", "ACCOUNTADMIN")
    try:
        connect_args = build_snowflake_connect_args(
            loader_env, role_env="SNOWFLAKE_LOADER_ROLE"
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    load(corpus, connect_args)
