---
name: add-source-instance
description: Onboard a new data-source INSTANCE (a new database/warehouse of an already-supported engine kind) into the federation — introspect its schema, emit its CSI + R2RML via r2g, enforce single-owner concept ownership, wire it into from_env, and add a golden. Use when adding a source such as a second Snowflake/Postgres database, a Databricks/MySQL instance, or any new relational/warehouse source whose engine already has an executor. NOT for adding a new engine kind (no executor exists yet) — that is `add-source-kind`.
---

# Add a Source Instance (CSI + mapping + concept ownership + golden)

Onboard a **new source of an already-supported kind** — data and configuration, **not code**.
The worked reference is Sprint 2 / WP-S3 (Snowflake telemetry): see `deploy/snowflake/`,
`deploy/csi/snowflake-telemetry.json`, `deploy/r2rml/snowflake_telemetry.ttl`, and the
`snowflake` branch in `src/cdf/service/app.py::FederationService.from_env`.

> If the engine has **no executor yet** (no `cdf.adapters.<engine>` and no `from_env`
> branch for its `kind`), stop — run **`add-source-kind`** first, then return here.

## Purpose

A source instance is legible to the fabric only when: its schema is mapped to the shared
conceptual vocabulary (`urn:arango-sparql:concept#`), **every concept it contributes is
owned by exactly one source**, its executor is wired from the environment, and a golden
proves the join. This skill makes that repeatable and encodes the traps that cost real time.

## Preconditions

- The engine kind already has an executor + a `from_env` branch (else `add-source-kind`).
- r2g is runnable (`~/code/r2g/.venv`, v0.2.0 pinned — matches the committed artifacts'
  `producerVersion`). For a non-Postgres kind, `r2g ingest-schema` does NOT work (PG-only);
  introspect via the Python API instead (Phase 2).
- You know the **join spine** (almost always `account_id`) and can confirm its *values*
  match the sources it will join against — a mismatch yields an empty join that still
  "passes" as grounded-but-empty. Verify values, not just presence.

## Protocol

### Phase 1 — Provision + load the corpus  *(the instance's data)*
- Provision the instance with the **CC-7 floor**: a **read-only role for the query path**,
  a statement timeout, a resource monitor / row cap where the engine supports it. Credentials
  live in the gitignored `.env` (or the secret store) **only** — never committed, never in the Makefile.
- Load the corpus with the **join-key column preserved** and a **synthetic primary key**
  (r2g builds the R2RML subject template from the PK). Load unquoted if the engine folds case
  (e.g. Snowflake → UPPERCASE physical names; CC-12 maps them back).
- Use a **separate write-capable role** for the one-time load; the engine only ever uses the
  read-only role. (Snowflake: `deploy/snowflake/setup.sql` + `load_corpus.py`.)
- **Gate:** the read-only role can `SELECT` the loaded table; row count matches the manifest.

### Phase 2 — r2g emits the CSI + R2RML
- Introspect the live schema → `schema.json`. Postgres: `r2g ingest-schema`. Other kinds:
  `create_source_connector("<kind>", "<url>", schema_name=...).get_schema().save_to_file(...)`
  (run as the read-only role — introspection is read-only).
- `r2g generate-config --schema schema.json --output mapping.yaml`
- `r2g export-csi --config mapping.yaml --schema schema.json --source-type <kind> --source-ref <ref> --output deploy/csi/<kind>-<ref>.json`
- `r2g export-r2rml --config mapping.yaml --schema schema.json --source-type <kind> --output <r2rml-dest>`
  - **native executor** (ClickHouse/Snowflake): `deploy/r2rml/<source_id>.ttl` where
    `source_id = "<kind>:<ref>"` with `:`→`_` (from_env reads `CDF_R2RML_DIR/<source_id>.ttl`).
  - **Ontop-driven**: the endpoint's `input/mapping.ttl`.
- **Gate:** `validate_csi` green; CC-12 naming correct (classes singular PascalCase, properties
  lowerCamel); the executor's `parse_r2rml` loads it and maps concepts → physical names.

### Phase 3 — Concept ownership (THE NEVER-CUT STEP)
- Every concept the new source contributes must be owned by **exactly one** source. If it
  duplicates a concept another source already owns (e.g. `UsageMetric` moving Postgres→Snowflake),
  **remove it from the other source's r2g `mapping.yaml` and regenerate that source's CSI + R2RML.**
  r2g 0.2.0 reproduces unchanged entities byte-identically, so regeneration is safe — diff to confirm.
- **Gate:** `SourceCatalog.from_csi_documents(deploy/csi/*.json)` → **zero concept overlap**
  across sources; `partition_query` routes a join query with one leg per owning source.

### Phase 4 — Wire from_env
- Confirm `from_env` dispatches this `kind` (env vars for creds/endpoint). Add `CDF_R2RML_DIR`
  and any `SNOWFLAKE_*`-style vars to the Makefile `DEMO_ENV` / a `.env`-sourcing `LOAD_ENV` so
  `gate` and `seed` see them (`gate.py`/loaders read `os.environ`; `server.py` loads `.env` itself).
- **Gate:** `FederationService.from_env()` builds an executor for `<kind>:<ref>` with creds absent
  until `execute` (no connection at construction).

### Phase 5 — Golden + seed/gate wiring
- Add a prepared question to `deploy/questions.json` and a live golden to `deploy/golden/`
  asserting `status: grounded`, `reconciliation: true` (≥1 arango + ≥1 non-arango citation),
  and `sources_touched` listing every leg. Add a **fixture-backed** twin under
  `src/cdf/eval/goldens/` so CI covers the join without live stacks.
- Grow `make seed` (load the corpus) and `make gate` (source `.env`). Demo needs no code change —
  lanes render per `retrieval_path` leg.
- **Gate:** `make gate` green live (the new golden included); the join returns rows.

## Acceptance (all must hold)
1. Read-only role `SELECT`s the corpus; row count matches manifest.
2. `validate_csi` green; CC-12 naming correct.
3. **Zero concept overlap**; `partition_query` routes one leg per owning source.
4. `make gate` 5/5 (or N/N) green live; the join returns non-empty rows.
5. Credentials only in `.env`/secret store; committed artifacts carry none.

## Gotchas (hard-won — do not rediscover)
- **r2g 0.2.0 `singularize()` is case-sensitive.** An UPPERCASE table (`USAGE_METRICS`) yields a
  plural, convention-violating class (`UsageMetrics`). Fix WITHOUT touching physical names: set
  `target_collection` to lowercase in `mapping.yaml` (class derives from `target_collection`;
  `rr:tableName` derives from `source_table`, which stays uppercase). See [[owl-naming-convention]].
- **Join-key VALUE match.** Confirm the join column's values are identical across sources before
  trusting a grounded result — a mismatch is silently empty. (Snowflake `ACCOUNT_ID` = CRM `AccountId`.)
- **Native executors ignore the R2RML subject template**, so a missing/weak PK does NOT cause
  Ontop's NULL-row suppression here — but it does for an Ontop-driven source. Know which path you're on.
- **CC-7:** read-only role for queries, separate write role for the one-time load, creds in `.env` only.
- **CC-9 / owned-lib pins:** if this instance needs a bumped owned-lib pin (e.g. r2g, arango-sparql-py),
  **first `git ls-remote` both org mirrors and scan recent commits for in-flight work** before
  integrating — mirrors drift and a teammate may already have done it (see [[repo-mirror-topology]]).
- **PJ's runbook rule:** `make gate` before any demo, no exceptions.

## References (point, don't duplicate)
- Worked example: `docs/archive/p1-closeout-plan.md (archived)` "Sprint 2" (WP-S1…S6).
- Native-vs-Ontop decision: `ADR-0002` (Snowflake takes SQL directly).
- Concept vocabulary + CC-12 naming: PRD §7.7, `deploy/csi/*.json`.
- Executor dispatch: `src/cdf/service/app.py::FederationService.from_env`.
