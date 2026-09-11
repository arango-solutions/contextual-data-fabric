---
title: "Module 01 — Connectors — Specification"
module: 01-connectors
type:
  - internal
  - module-spec
status: draft
version: 0.2
owner: TBD
building_block: Both
depends_on_modules: []
depends_on_repos: ["schema-analyzers", "r2g", "customer-context"]
requires_repo_enhancements: ["schema-analyzers-metadata-sampling"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 01 — Connectors

> Source adapters that provide two things: **metadata/schema** (for ontology extraction) and **live query access** (for federated query) — **without bulk-copying data** into Arango.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
Own the boundary to each external source. Two distinct jobs: (a) a **metadata-sampling** path that hands schema/catalog/sample-rows to Ontology Extraction (M2), and (b) a **live query** path that lets the Federated Query Engine (M5) push a query down and stream results back. Credentials, dialects, and connection lifecycle live here so no other module deals with source specifics.

## 2. Scope
**In scope:** connection + auth; metadata sampling (schema, keys, catalog, sample rows); live query execution handles; per-source dialect concerns. Sources: **Postgres (P1)**, the **Arango unstructured graph (P1, in-process)**, **Snowflake (P2)**, **Databricks (P3)**.
**Out of scope:** mapping concepts→tables (M4); generating the queries (M5/r2g); ontology extraction itself (M2).

## 3. Interfaces (inputs / outputs)
- **Produces to M2:** a normalized **metadata bundle** (tables, columns, types, keys, sample rows, catalog/semantic-layer defs where available).
- **Produces to M5:** a **source handle** + an `execute(query) -> rows` surface; declares the source's query dialect.
- **Contract:** a small connector interface (`sample_metadata()`, `open()`, `execute()`, `close()`), one implementation per source.

## 4. Functional requirements
- **FR-1 (P1):** Postgres connector — metadata sampling (`information_schema`, keys, sample rows) + live parameterized query execution.
- **FR-2 (P1):** Arango unstructured-graph connector — expose the already-ingested graph (from `customer-context`) for AQL execution as a first-class federation source.
- **FR-3 (P1):** **Metadata-sampling** mode that returns schema without pulling bulk data (requires the [[contextual-data-fabric/docs/architecture/_repo-enhancements/schema-analyzers-metadata-sampling|schema-analyzers enhancement]]).
- **FR-4 (P2 — pulled forward, due 2026-07-24):** Snowflake connector — metadata + pushdown query. ~~Gated on the free-tier check~~ **Gate resolved (2026-07-21):** Snowflake's 30-day trial ($400 credits, no credit card, auto-suspends) is sufficient; see PRD §7.7 and the P1 close-out plan Sprint 2. Pushdown rides the second Ontop endpoint over `snowflake-jdbc`; metadata via r2g's Phase-6 connector (RSA fallback).
- **FR-5 (P3):** Databricks connector.
- **FR-6 (P1 floor / P2 hardened):** P1 security floor (PRD §10.7 / CC-7): every source connection uses a **read-only DB role**; credentials come from environment/secret store, never code or mapping artifacts; no raw-credential logging. P2: full credential management + per-source read-only enforcement.
- **FR-7 (P1):** **Logical source registry + SecretResolver seam.** Connectors are addressed by logical source name; M1 resolves name → credential at `open()` time. P1 backend: `.env`; P2 backend: a secret store (Vault / cloud manager) behind the same seam. Reuse r2g Phase 8's credential pattern (encrypted registry, `$ENV_VAR` resolution at use time, token redaction on read, DSN-scrubbed errors). Nothing outside M1 ever holds a raw credential; all read-back surfaces (incl. MCP tools) redact.
- **FR-8 (P2):** **Per-source auth hardening:** Snowflake **key-pair auth** (not passwords), Databricks **service principal + OAuth M2M**; rotation via the secret store with no code change; Snowflake service users must be `TYPE=SERVICE` on key-pair or workload identity federation (passwords blocked Oct 2026); Databricks PATs are not offered. Per-user identity at the source is **M16's** concern (trust levels, brokers, provisioning); M1 supplies the service credential and `BaseSourceIdentity` and consumes the resulting `SourceExecutionContext` unchanged.
- **FR-9 (P3 / WP-17 baseline):** Every query source declares
  `service|delegated`. `service` preserves the existing least-privilege
  connector. `delegated` requires a `DelegationBroker` exchange from the
  immutable query principal + logical source + operator-owned base-identity
  reference into a short-lived, repr-safe `SourceIdentity`, passed through
  `SourceExecutionContext`. A missing broker or context-aware adapter fails
  closed and must never fall back to service credentials. RFC 8693 is the
  preferred exchange where supported; source-specific adapters are explicit.
  CDF does not provision an STS, Snowflake external OAuth, Postgres role
  mappings, or any source-native policy. **Ownership (2026-09-09):** the
  broker implementations, the `asserted` level, per-source provisioning
  checklists and certification goldens live in **M16 (Identity Delegation &
  Source Trust)**; M1 keeps the `SecretResolver`, the service credential, and
  the executor-side consumption of `SourceExecutionContext`.

### P2.3 SecretResolver and rotation contract (WP-8)

`cdf.connectors` is the M1 runtime boundary. CSI and R2RML contain only the
logical `source_id`, engine `kind`, and non-secret `ref`; the resolver is called
when an executor is opened, never while mappings are parsed. A resolved
connector carries immutable fields plus an operator-supplied opaque generation
alias. Its repr and normal dataclass/JSON paths do not expose fields.

Two backends implement the same `SecretResolver` protocol:

- `env` keeps the legacy flat `ARANGO_*`, `CLICKHOUSE_DSN`,
  `SNOWFLAKE_*`, and `ONTOP_SPARQL_ENDPOINT` contract. For multiple sources of
  one kind, `CDF_SECRET_REGISTRY_JSON` provides entries keyed by exact
  `source_id`.
- `file` reads a names-only registry at `CDF_SECRET_REGISTRY_PATH`; each entry
  points to one JSON document directly under `CDF_SECRET_MOUNT_PATH`. This is
  the production Docker/Kubernetes mounted-secret path. Registry and secret
  files must be regular files owned by root or the service user and, on POSIX,
  must not grant group/other access (mount with mode `0400`/`0600`).

Each mounted document has exactly `generation` and `fields`. `generation` is an
opaque alias such as `rotation-2026-08`; it is not a hash of credential
material. Operators must change it whenever any field changes. The
generation-aware executor proxy checks before execution (or at
`CDF_SECRET_POLL_INTERVAL_SECONDS`), builds the replacement first, swaps it
atomically, and retains the last known-good executor if reload fails. Calls
already using the old generation finish before its lifecycle is drained.
Snowflake drains every idle pooled session; other adapters close their client
when supported.

The assembly Arango backend uses logical source `cdf:assembly` and follows the
same resolver contract. Ontop's endpoint is connector configuration, while the
Postgres credentials used by Ontop remain in that separate Ontop process and
must be mounted/rotated there independently. LLM provider keys are deliberately
outside this source-connector resolver; they remain owned by the NL provider
client until a provider-specific injection path can avoid broadening access.

Health surfaces expose only `configured`, backend name, generation alias, and
last reload status/time. Central redaction scrubs URL/DSN userinfo,
bearer/basic authorization, credential key/value pairs, and exact resolved
credential values from source and assembly failures before retrieval, HTTP,
MCP, or logs. Exact-value registration is limited to credential-bearing fields
and embedded URL/DSN credentials; active generations hold ref-counted leases,
and obsolete values are released only after their old executor drains.

The generation-aware registry preserves the same execution-context seam during
rotation: context-aware adapters receive `SourceExecutionContext`; legacy
adapters remain valid only in service mode. Short-lived delegated material is
not put in CSI/R2RML, connector health, request context, retrieval paths, or
logs.

## 5. Non-functional requirements
No bulk data movement (sampling + pushdown only); read-only by default; connection reuse for latency; source errors surfaced (never silently swallowed).

## 6. Dependencies
- **Repos:** `schema-analyzers` (relational + arango) — see [[contextual-data-fabric/docs/architecture/_repo-enhancements/schema-analyzers-metadata-sampling|enhancement]]; `r2g` (schema→ontology consumes the metadata bundle); `customer-context` (the Arango unstructured graph).

## 7. Phase mapping
- **P1:** Postgres + Arango-unstructured connectors, metadata sampling.
- **P2:** Snowflake; credential/read-only hardening.
- **P3:** Databricks.

## 8. Acceptance criteria / demo (P1)
- The Postgres connector returns a metadata bundle that M2 turns into a source ontology, **and** executes a parameterized pushdown query for M5 — with no bulk table copy into Arango. The Arango connector serves AQL against the unstructured graph.

## 9. Open questions
- Standard metadata-bundle schema across sources (so M2 is source-agnostic).
- Do we need a "metadata sampling connector vs query connector" split, or one connector with two modes? (Roadmap transcript raised the sampling-connector idea.)
