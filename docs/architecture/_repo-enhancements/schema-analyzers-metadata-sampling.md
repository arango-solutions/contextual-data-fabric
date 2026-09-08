---
title: "Repo Enhancement — schema-analyzers — Metadata Sampling API"
repo: schema-analyzers (relational-schema-analyzer + arango-schema-analyzer)
type:
  - internal
  - repo-enhancement-spec
status: draft
version: 0.1
owner: Arthur Keen
serves_modules: ["01-connectors", "02-ontology-extraction", "04-mapping-layer"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Repo Enhancement — schema-analyzers: metadata sampling API

> **Requirement (one line):** expose a clean **metadata-sampling API** (schema, keys, catalog, sample rows) that the Connectors (M1) call and that feeds Ontology Extraction (M2) and the Mapping Layer (M4) — without pulling bulk data.

## 1. Current state (verified against `~/code/relational-schema-analyzer` + `~/code/arango-schema-analyzer`, v0.2)
Both analyzers exist as versioned pip libraries and are further along than v0.1 assumed:
- **RSA (`relational-schema-analyzer`, PyPI v0.4.0):** introspects **7 live/file sources** (PostgreSQL, MySQL, SQL Server, Snowflake, DuckDB, Databricks, CSV) plus **dbt-manifest and OSI-catalog** sources, and emits a canonical `{conceptualSchema, physicalMapping, metadata}` bundle — deterministic baseline, optional additive LLM refinement, OWL Turtle/JSON-LD export, CLI, and an MCP server. Extracted from r2g's core; carries the production bar.
- **`arangodb-schema-analyzer`** (repo `~/code/arango-schema-analyzer`): the ArangoDB-side analogue — conceptual schema + conceptual→physical mapping + metadata, whole-database or per-named-graph scope, MCP server (token-gated remote transports).
- **The bundle shape is already standardized across the two** — RSA's README states it emits "the same tool-contract bundle shape" as the ArangoDB analyzer, so downstream consumers treat relational and Arango sources interchangeably. RE-1 is largely met; the remaining work is contract freezing and the sampling/incremental features below.

## 2. Why the change
The fabric needs a **source-agnostic metadata bundle** so M2 can extract ontologies uniformly across sources and M1 can offer a "metadata sampling connector" (raised in the roadmap) that hydrates our mappings/ontology without moving the underlying data.

## 3. Required enhancements *(re-scoped v0.2 against verified state)*
- **RE-1 (P1, mostly done):** ~~Build~~ **Freeze + document** the metadata-bundle contract — the source-agnostic `{conceptualSchema, physicalMapping, metadata}` shape exists and is shared across both analyzers; the fabric pins a contract version so M1/M2/M4 consume it stably.
- **RE-2 (P1, verify):** **Sampling controls** (row-sample size/limits) — bounded sampling exists in the r2g/RSA lineage (value samplers with row limits); verify the limits are exposed on the RSA public API and default to cheap.
- **RE-2a (P1, verified largely done):** **Planner statistics in the bundle** (fabric CC-11) — `arangodb-schema-analyzer` already emits collection counts (+ counts fingerprint), **`sample_field_value_counts`** (per-field value distributions), and observed relationship cardinality; RSA emits FK cardinality hints (1:1 vs 1:N) and bounded value samples. Remaining work: make these fields part of the **frozen bundle contract** (RE-1) so M4 can pass them through CSI to the M5 planner, and confirm the redaction options (`strip_samples`/`mask_field_values`) apply on every LLM-egress path.
- **RE-3 (P2, mostly done):** ~~Add~~ Snowflake/Databricks are **already live RSA sources**; remaining work is parity-testing their bundles against the Postgres bundle shape.
- **RE-4 (P2):** Incremental re-analysis on schema change (feeds M3 belief management / the AOE source-change cascade) — the one genuinely new build in this spec. **Also the statistics-refresh mechanism for CC-11:** planner statistics are snapshot-time and drift; re-analysis keeps join-ordering/admission decisions honest (runtime caps remain the backstop against stale stats).
- **RE-6 (P2, new 2026-07-22 — from the data-platform lead's "thousands of tables" ask):** **Purpose-scoped table relevance.** At enterprise scale (thousands of Snowflake tables), introspect-everything is wrong. Given an integration **purpose** (ORSD purpose statement + competency questions — the fabric's existing scoping contract, AOE FR-19.1/19.4), rank tables by relevance and extract only the confirmed set: (a) **semantic signals** — CQ/purpose-term embedding similarity against table/column names *and Snowflake `COMMENT`s*; (b) **structural expansion** — FK-neighborhood growth from seed tables (declared or inferred keys); (c) **usage signals** — `ACCOUNT_USAGE.ACCESS_HISTORY` / `QUERY_HISTORY`: the tables the org actually queries and joins are the tables that matter ("your own query history tells us what's relevant"); (d) **governance tags** (`TAG_REFERENCES`) as include/exclude policy. Output: a ranked candidate set the curator confirms (the ~2% pattern applied to *table selection*), then scoped introspection. r2g's Phase-8 catalog-provider abstraction is the natural slot for the catalog-side signals.
- **RE-7 (P2, new 2026-07-22):** **Snowflake value-overlap sampler for FK inference.** Declared constraints are read today (`SHOW PRIMARY KEYS`, declared FKs/uniques — present in the catalog even though Snowflake doesn't enforce them), and name-heuristic FK inference works everywhere — but the **value-overlap sampling** stage (the distinct-value containment statistic that confirms a candidate FK) is wired for Postgres/MySQL/SQL Server/CSV and **falls back to name-only on Snowflake**. Add a `SnowflakeValueSampler` (bounded, warehouse-cheap, classification-gated) so key inference on undeclared Snowflake schemas reaches the same confidence as Postgres.
- **RE-5 (P1, new 2026-07-18):** **OWL naming convention at baseline (CC-12)** — conceptual entity names emitted as **singular PascalCase**, property names as **lowerCamel** (`Account.accountId`, not `accounts.account_id`), in both analyzers; `physicalMapping` keeps raw physical names. Make the rule **normative in the CSI v1 contract** and enforced by `validate_csi` (this repo owns the schema; r2g vendors it). Singularization via a shared helper + per-source override map (RSA's "singularization is unreliable" note is mitigated, not ignored — the M3 confirm step is the backstop). Supersedes RSA's current "pascal_case without singularizing" decision.

## 4. Interface contract (with M1 / M2 / M4)
- **Input:** a source connection (from M1).
- **Output:** the metadata bundle (RE-1) consumed by M2 (extraction) and M4 (mappings).

## 5. Phase mapping
- **P1:** RE-1, RE-2 for Postgres + Arango.
- **P2:** RE-3, RE-4.

## 6. Acceptance criteria (P1)
- The Postgres connector calls the analyzer and gets a complete metadata bundle (schema + keys + sampled rows) that M2 turns into a source ontology — with no bulk table read.

## 7. Open questions / for Arthur
- ~~Is the metadata-bundle shape already standardized across the two analyzers?~~ **Answered (v0.2): yes** — same tool-contract bundle shape, by design.
- ~~Which repo copy is canonical?~~ **Answered (v0.2):** `~/code/relational-schema-analyzer` (PyPI `relational-schema-analyzer`) and `~/code/arango-schema-analyzer` (package `arangodb-schema-analyzer`). The empty `~/code/relational_schema_analyzer` (underscore) directory is stale — delete it.
- Remaining: does the fabric consume the analyzers via pip API, via their MCP servers, or both (ties into PRD §10.2 agent-interface decision)?
