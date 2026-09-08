---
title: "Repo Enhancement — r2g — Federated Query Support"
repo: r2g
type:
  - internal
  - repo-enhancement-spec
status: draft
version: 0.1
owner: Arthur Keen
serves_modules: ["04-mapping-layer", "05-federated-query-engine"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Repo Enhancement — r2g must support Federated Query

> **Requirement (one line):** r2g must move from *batch-load* relational→graph to also **emitting runtime mappings and generating per-source queries**, so the [[contextual-data-fabric/docs/architecture/module-05-federated-query-engine/specification|Federated Query Engine (M5)]] can push queries down to the source **without materializing the data**.
>
> This is the concrete example Arthur gave ("tell r2g it needs to support federated query"). It is the highest-leverage enhancement for Phase 1.

## 1. Current state (verified against `~/code/r2g`, v0.2)
r2g takes a relational schema and produces mappings **already decoupled from the load**: `ingest-schema → schema.json → generate-config → mapping.yaml`, then batch ETL / CDC / Kafka / temporal-mode loading as separate steps. Its introspection/analysis core has been extracted into **RSA (`relational-schema-analyzer`, PyPI v0.4.0)**, which carries the production bar — downstream systems (including this fabric) depend on RSA + named r2g modules, not r2g wholesale. Also relevant and already shipped: **Snowflake** source support (Phase 6, done), a **field-expression engine** (Phase 5c expression mapping; reused by Phase 9b masking) — the "general-purpose expression compiled per dialect" this spec's open question asked about, **LLM-assisted ontology derivation with human review** (Phase 10), and **MCP tools** (Phase 8). What does **not** exist is any federated/pushdown query path: no per-source query generation, no query fragmentation. r2g's operational envelope is single-node, no scale/HA.

> These enhancements are now specified on the r2g side as **r2g PRD Phase 12** (`r2g/docs/PRD.md`).

## 2. Why the change
The customer constraint is **do not move the data** ([[design-partner]]: "the brain has to be on this side"; no bulk materialization into Arango). The mapping r2g already produces *is effectively the query* — instead of running it once to hydrate, we need to run it **on demand, per question, pushed down to the source**, and to **break a query across multiple sources** when needed. This is the same transpile pattern as the Cypher/SPARQL translator (English → query), generalized and federated.

## 3. Required enhancements *(reconciled v0.3 with ADR-0001 + r2g PRD Phase 12 as definitized)*
- **RE-1 (P1) — the durable contract:** Expose the r2g mappings as a **runtime artifact** — **forward `CSI v1` + R2RML export** (per [[contextual-data-fabric/docs/architecture/module-05-federated-query-engine/adr/ADR-0001-conceptual-query-language|ADR-0001]] decision #3; r2g P12.1). `CSI v1` (defined in `arangodb-schema-analyzer`, which names r2g as the forward producer) is the cross-tool mapping hub; R2RML is what VKG engines (Ontop) consume for the SQL leg; a CSI→MappingBundle shim feeds the AQL transpilers. Versioned with a content hash (M4 FR-7). **Credential-free (fabric CC-7):** the exported CSI/R2RML references sources by logical name only — no connection strings/JDBC URLs (R2RML can syntactically carry them; r2g must not emit them). **Convention-conforming (fabric CC-12, new 2026-07-18):** `export-csi`/`export-r2rml` apply r2g's existing Phase-5f `apply_naming_convention` to the *conceptual* names — classes singular PascalCase, properties lowerCamel (`Account`/`accountId`, not `accounts`/`account_name`) — while logical tables/columns stay physical. This is load-bearing **regardless** of the RE-2 buy-vs-build outcome.
- **RE-2 (P1 fallback / buy-vs-build):** **Per-source query generation** — given a relational query-graph *partition* (resolved concepts + predicate) from the SPARQL IR, generate a **pushdown SQL query**. **Reframed by ADR-0001 #2:** Ontop already does SPARQL→SQL pushdown over R2RML with no materialization across every relational source r2g supports, so native generation (r2g P12.2) is a **stopgap/fallback** — the M5 implementation plan uses it as the fastest P1 path (WP B1-alt) while Ontop (WP B1) is the P2 target unless the team rejects running a VKG engine.
- **RE-3 (P1):** A **no-materialization mode** — r2g can produce mappings + queries without hydrating the graph (batch-load remains available as an option, not the default).
- **RE-4 (P2):** **Query fragmentation** — when a request spans >1 source, emit the per-source fragments + the join keys (the engine reassembles). Support the "break the query up, run it, reassemble" flow.
- **RE-5 (P2):** Emit mappings/queries via both a **deterministic** path and an **LLM** path (LLM as safety net), consistent with the composable-blueprint stance.
- **RE-6 (P2 — pulled forward, due 2026-07-24):** Snowflake support for RE-1 (Databricks stays P3). Re-scoped by ADR-0001's Ontop adoption: **CSI + R2RML export over a Snowflake-introspected schema** (r2g PRD P12.7 as amended) — no bespoke Snowflake SQL generation; Ontop's native Snowflake connector (≥5.0.0) does the rewriting. Consumer: fabric Sprint-2 WP-S3 (P1 close-out plan).

## 4. Interface contract (with M4 / M5)
- **Input:** relational schema/metadata (from the schema analyzer / M1) + the master-ontology concept being queried + predicate.
- **Output:** (a) OSI/YAML mapping artifact; (b) a **generated source query** (e.g. parameterized SQL) with declared bind params + the source objects it reads.
- **Guarantee:** the generated query is inspectable and returned alongside its results so M7 can cite the **actual SQL** and source object.

## 5. Phase mapping
- **P1:** RE-1, RE-2, RE-3 for **Postgres** — enough for the 1-week federated-query demo.
- **P2:** RE-4, RE-5; RE-6 Snowflake.
- **P3:** RE-6 Databricks; join optimization.

## 6. Acceptance criteria (P1)
- Given the Phase-1 Postgres schema, r2g emits an **OSI mapping** and, for a seed-question predicate, generates a **parameterized SQL pushdown query** that returns the right rows **without** hydrating them into Arango — and returns the SQL text + source objects for citation.

## 7. Open questions / for Arthur
- ~~Does r2g already emit mappings independently of the batch load?~~ **Answered (v0.2): yes** — `generate-config` emits `mapping.yaml` from `schema.json` with no load step. RE-1/RE-3 are exposure + packaging, not refactors; RE-2 (query generation) is the real build.
- ~~Is the mapping expressed generally or directly in SQL?~~ **Answered (v0.2): generally** — r2g's field-expression engine (Phase 5c) already expresses value transforms source-agnostically; RE-2 compiles mapping + predicate to dialect SQL at query time.
- ~~Which repo copy to build against?~~ **Answered (v0.2):** Arthur's `~/code/r2g` (GitHub `ArthurKeen/r2g-arango`), with RSA pinned from PyPI.
- ~~Does pushdown generation live in r2g, in RSA, or a new module?~~ **Superseded (v0.3) by ADR-0001 #2:** the primary question is now **Ontop (buy) vs r2g P12.2 (build)** — recommendation is Ontop for the relational legs with r2g P12.1 (CSI v1 + R2RML) as the contract; P12.2 remains the P1 stopgap (M5 plan WP B1-alt) and the fallback if the team rejects VKG infra.
