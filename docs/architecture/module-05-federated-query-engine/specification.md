---
title: "Module 05 — Federated Query Engine — Specification"
module: 05-federated-query-engine
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: PJ
building_block: Query
depends_on_modules: ["04-mapping-layer", "01-connectors", "06-entity-resolution", "07-grounding-provenance"]
depends_on_repos: ["r2g", "arango-sparql-py", "arango-cypher-py", "arangodb-schema-analyzer", "relational-schema-analyzer", "customer-context"]
requires_repo_enhancements: ["r2g-federated-query"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
  - "[[adr/ADR-0001-conceptual-query-language|ADR-0001 — Conceptual-query language]]"
  - "[[implementation-plan|M5 Implementation Plan]]"
---

# Module 05 — Federated Query Engine

> Turn an English question into a **decomposed, multi-source query plan**, execute the parts against the sources that hold the data (SQL pushdown, AQL, agent calls), and **reassemble** a single grounded, cited answer — **without moving the data**.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
This is the runtime heart of the Query building block. Given a natural-language question and the master ontology + its functional mappings (from [[contextual-data-fabric/docs/architecture/module-04-mapping-layer/specification|M4]]), it decides **which sources** must be touched, **generates the per-source queries**, executes them, and **stitches** the results into one answer. It is the module that operationalizes the North Star line "ask anything, in English, across everything — without copying the data in."

## 2. Scope
**In scope:**
- Concept resolution: map question → ontology concepts/properties.
- **Query decomposition / planning:** split into per-source sub-queries using the mappings.
- **Per-source query generation:** SQL (pushdown) for relational sources, AQL for the Arango unstructured graph, agent calls where a source only exposes an agent.
- **Execution + reassembly:** run sub-queries (parallel where independent), join/reconcile results via the canonical entity hub ([[contextual-data-fabric/docs/architecture/module-06-entity-resolution/specification|M6]]).
- Two execution patterns: **loosely-coupled** (fetch on demand via pointers) and **assembled** (temporarily materialize a subgraph into Arango for analytics).
- Both a **deterministic** planner and an **LLM** planner, selectable per deployment.

**Out of scope:**
- Producing the mappings themselves → [[contextual-data-fabric/docs/architecture/module-04-mapping-layer/specification|M4 Mapping Layer]].
- The citation/answer-envelope format and refuse-if-uncited gate → [[contextual-data-fabric/docs/architecture/module-07-grounding-provenance/specification|M7 Grounding & Provenance]] (this module *feeds* it the retrieval path).
- Source connection/credentials/metadata → [[contextual-data-fabric/docs/architecture/module-01-connectors/specification|M1 Connectors]].

## 3. Interfaces (inputs / outputs)
- **Consumes:**
  - Question (string) + optional context (persona, account scope).
  - Master ontology + functional mappings (OSI/YAML) from M4.
  - Live source handles from M1 (Postgres cursor, Arango DB handle, …).
  - Canonical-entity resolution from M6 for cross-source joins.
- **Produces:**
  - An **answer payload** + a **retrieval path** object listing every sub-query executed: `{source, query_text (SQL/AQL), source_objects, rows/ids}`. M7 wraps this into the validated cited envelope.
- **Contract (proposed):** a `federate(question, ontology, mappings, sources) -> {answer, retrieval_path[]}` library call; the query plan is an inspectable intermediate object (for debugging and for the deterministic/LLM swap). **Agent-facing surface:** PRD §10.2 proposes wrapping this call as an **MCP tool** (consistent with the five constituent repos that already ship MCP servers) — decide via PRD §9.8 before P2 so the contract isn't retrofitted.

## 4. Functional requirements
- **FR-1 (P1):** Resolve a question to ontology concepts and produce a **query plan** naming the sources to hit and the join keys. The plan's conceptual query is expressed in a **small typed graph-pattern IR over the ontology that serializes to SPARQL** ([[adr/ADR-0001-conceptual-query-language|ADR-0001]]); **decomposition = partition this query graph by the source each concept/property maps to.**
- **FR-2 (P1):** Generate and execute **SQL pushdown** against one relational DB (Postgres) using M4 mappings — filters pushed down; no bulk pull into Arango. **The relational leg SHOULD use a Virtual Knowledge Graph engine (Ontop) driven by R2RML mappings (r2g P12.1) — SPARQL→SQL rewriting with no materialization is off-the-shelf and already covers all our relational sources (ADR-0001, §Research). Bespoke SQL generation (r2g P12.2) is a retired fallback — the adopt-vs-build decision in ADR-0001 RESOLVED to adopt Ontop (Apache-2.0 OSS, free).**
- **FR-3 (P1):** Generate and execute **AQL** against the Arango unstructured graph for the same question. **SPARQL→AQL is provided by the *owned* [`arango-sparql-py`](https://github.com/ArthurKeen/arango-sparql-py) transpiler (ADR-0001) — and the remaining work is DONE (2026-07-15): evaluation correctness is CI-gated (WP-C1) and the `translate_partition` federation entry point shipped (WP-C2; canonical keys, `seed_bindings` pushdown, `as_of`; contract: `arango-sparql-py/docs/architecture/proposals/federation-entry-point.md`). `arango-cypher-py`'s role is now the NL engine (D1), not a transpiler fallback.**
- **FR-4 (P1):** **Reassemble** structured + unstructured results into one answer, joined via the canonical entity hub.
- **FR-5 (P1):** Emit a complete **retrieval path** (actual SQL + AQL + source objects) for M7 to cite; refuse (via M7) if any leg is uncitable.
- **FR-6 (P1):** **LLM planner** path (quick-and-dirty decomposition) with the plan surfaced for inspection.
- **FR-7 (P2):** **Deterministic planner** path (mapping-driven decomposition; LLM only as safety net).
- **FR-8 (P2):** **Assembled** execution pattern — materialize a bounded subgraph into Arango and run graph analytics (e.g. PageRank) when the use case needs it.
- **FR-9 (P2):** **Cost/latency instrumentation** per plan (tokens, wall-clock, per-source) — directly addresses the customer's cost objection. Reuse AOE's observability stack (structlog/Prometheus/OTel) per PRD §10.6.
- **FR-10 (P3):** Multi-source planner across ≥3 sources with parallelized independent legs and cross-source join optimization.
- **FR-11 (P1):** **Partial-failure semantics** (PRD §10.5 / CC-5) — when a leg fails while others succeed: default to a partial answer with the failed leg *explicitly declared* in the retrieval path; refuse when the failed leg is load-bearing. Never silent omission.
- **FR-12 (P1):** **As-of stamps** (PRD §10.4 / CC-4) — every sub-query result in the retrieval path carries an as-of timestamp (execution time for live legs; last-ingest time for the Arango graph) so M7 can cite freshness.
- **FR-13 (P1):** **Statistics-driven planning + bind-join default** (PRD §10.11 / CC-11) — the planner consumes the analyzers' statistics (row/collection counts, FK cardinality hints, `sample_field_value_counts` selectivity) from the mapping artifact for join ordering and leg sizing; **engine-side cross-source joins are bind/semi-joins on canonical keys** (small side ships keys into the other leg's predicate), with a bounded key-set size — beyond it, push the join down, switch to the assembled pattern (FR-8), or refuse.
- **FR-14 (P1 floor / P2 full):** **Plan-time admission control** (CC-11) — no naked scans (every leg carries a selective binding derived from the question's concepts); per-leg row/byte budgets with mandatory LIMITs; a round-trip budget (max legs, max sequential depth); pre-flight `EXPLAIN` with a cost ceiling (P2). Plans failing admission are rewritten, downgraded to assembled, or refused — never run as-is.
- **FR-15 (P1):** **Run-time enforcement + trip semantics** (CC-11, CC-5) — per-leg timeouts and row caps at the cursor, overall deadline, federator memory budget (no disk spill), per-source circuit breaker. A capped/truncated leg is **declared in the retrieval path** ("capped at N rows — partial"), never silent; refusals name the reason and the alternative ("run as an assembled job?").

### 4.1 P2.2 statistics, physical-plan, and admission contracts

CSI v1 remains backward compatible: a document may omit `statistics`. When
present, CDF consumes this additive contract:

```json
{
  "statistics": {
    "version": "1",
    "snapshotId": "warehouse-2026-08-05",
    "asOf": "2026-08-05T00:00:00Z",
    "source": {
      "rowCount": 100000,
      "estimatedBytes": 64000000,
      "costPerGbUsd": 2.0
    },
    "classes": {
      "Account": {
        "rowCount": 3000,
        "estimatedBytes": 1500000,
        "properties": {
          "accountId": {"ndv": 3000},
          "healthBand": {"ndv": 4, "selectivity": 0.25}
        }
      }
    },
    "properties": {
      "accountId": {"ndv": 3000}
    }
  }
}
```

`cardinality` is accepted as an alias for `rowCount`; if both are present they
must agree. Counts, bytes, NDV, and rates must be finite and non-negative;
selectivity must be in `[0, 1]`. Unsupported statistics versions and malformed
values fail catalog construction. Unknown additive fields are ignored.

Without statistics, the planner uses conservative defaults (1,000,000 rows and
1,024 bytes/row), reports cost as unknown, and preserves the P1
relational-then-Arango staging. With statistics, connected joins up to eight
legs use exact deterministic dynamic programming; larger connected components
use selective-first greedy ordering. Independent component roots share a stage.
All ties use stable source IDs. The plan reports per-leg rows/bytes/cost,
snapshot/as-of, stages/order, seed directions, and total rows/bytes/cost.

Admission checks estimated rows, bytes, and known cost before execution. Runtime
checks wall time, intermediate rows, final rows, and seed rows. A refusal is a
structured `{code, phase, metric, observed, limit, message}` and is never
represented as silent truncation. `CDF_SEED_BATCH_ROWS` controls one `VALUES`
batch; seed sets up to `CDF_MAX_SEED_ROWS` execute in deterministic batches and
are merged/de-duplicated. Larger sets refuse and never execute the target
unseeded.

### 4.2 P2.2 bounded assembled execution contract

Execution is explicit: `virtual` is the default and preserves the P1
fetch/bind-join behavior; `assembled` is opt-in on `FederationService`,
`POST /federate`, and MCP `federate`. An assembled request is refused unless an
assembly backend is explicitly enabled and configured. It is also refused
before resource creation when any leg lacks validated CSI statistics, or when
the preflight materialized-row/byte estimate exceeds the mandatory assembly
budgets.

Every admitted request receives an unpredictable job ID and an isolated
temporary Arango named graph with dedicated vertex and edge collections.
Source-row vertices carry source ID, partition SPARQL, native query, and as-of
lineage. Joined-intermediate vertices have `derived_from` edges to their direct
inputs. Job IDs and aggregate counts are safe telemetry; credentials and caller
tokens are never passed to or stored in the graph.

Assembly has hard materialized-row, serialized-byte, wall-time, and TTL limits.
A breach refuses the answer; it never truncates. Cleanup runs in `finally` after
success, source failure, admission failure, cancellation, or exception.
Per-collection TTL indexes plus `expires_at` metadata are the crash fallback.
Cleanup failure changes the result to a structured refusal and is exposed in
`AssemblyMetrics` rather than hidden.

**Execution boundary:** the temporary graph is the bounded intermediate and
lineage substrate. WP-12 deliberately retains the proven deterministic Python
table-binding join for answer semantics; it mirrors each joined intermediate
into the graph rather than translating arbitrary joins to AQL. Graph-native
analytics can consume that job graph while it exists, but moving the
answer-producing join itself to AQL requires a separate semantic-parity gate.

### 4.3 P2.3 connector lifecycle and public-error boundary

M5 receives logical source executors from M1. It does not resolve credentials
while reading CSI/R2RML. Before a leg executes, the generation-aware M1 proxy
may resolve and atomically replace its executor; in-flight calls retain their
old handle until completion, after which the old pool/client is drained.
Failed replacement keeps the last known-good executor and records only a
scrubbed operational failure.

Every source-leg and assembly exception is passed through the central
`cdf.connectors` scrubber before it enters `RetrievalStep.error`,
`AssemblyRefusal`, an answer envelope, HTTP, MCP, or logs. Safe source health
metadata is limited to configured/backend/generation-alias/reload status and
time. Generation aliases are operator-provided opaque IDs, never hashes of
secret values.

### 4.4 WP-13/WP-14 canonical-hub runtime boundary

CDF owns a stable `CandidateResolver` seam and independently enforces required
account scope, observable-only inputs, oracle-ID exclusion, an absolute
deadline, resolved-candidate scope, canonical-ID presence, score threshold,
top-vs-second margin, and evidence completeness. The optional AER adapter is
lazy: core CDF and its tests do not require AER to be installed.

WP-14 now wires that CDF-owned contract into source-leg execution. A strict
catalog binding declares the canonical join variable and pattern, account-scope
binding, observable field-to-binding allowlist, policy profile, and resolver
name. Canonical values bypass the resolver. Other distinct observations resolve
under one plan deadline and call budget, then replace the native join value
before telemetry row counts, seed generation, joining, or optional assembly.
Resolution-enabled legs run in the initial unseeded stage so a canonical
`VALUES` clause is never compared with a source-native key.

Abstained/deadline/backend-unavailable rows are removed and counted. Strict mode
refuses any shortfall; opt-in partial mode may return only the safely normalized
remaining rows. Any resolver refusal, including a cross-account candidate, is
always fail-closed. Retrieval steps, citations, HTTP/MCP envelopes, and plan/leg
metrics carry value-free resolution events and evidence. Temporary assembly
lineage stores only reduced event summaries; normalized source rows remain
authoritative.

The AER implementation and adapter remain local/API-ready as of 2026-08-05.
There is still no AER dependency pin or claim that the API is released. Runtime
depends only on CDF's injected resolver protocol. `from_env` supports an
operator-owned `CDF_ENTITY_RESOLVER_FACTORY=package.module:function` composition
seam; the checked-in demo catalog keeps runtime resolution disabled.

### 4.5 P3 WP-15/WP-17/WP-18 governed query boundary

[ADR-0004](adr/ADR-0004-identity-planes-and-policy-enforcement.md) separates
steward/build identity from asker/query identity. HTTP verifies generic OIDC
JWTs when configured; MCP v2 reads the SDK's official verified access-token
context. Both create the same immutable `RequestContext` and discard bearer
material. Direct/library and stdio development calls receive an explicit named
anonymous-dev principal unless authentication is required.

`FederationService.federate_question`, `federate_sparql`, assembled execution,
and `execute_plan` pass the context explicitly. Independent thread-pool legs
receive the same immutable request context inside `SourceExecutionContext`.
Safe request/trace IDs, normalized purpose, tenant, and issuer/subject principal
key may enter answer/execution metadata; bearer tokens and unrestricted claims
may not.

Catalog source auth is `service|delegated`. Service mode preserves legacy
executors. Delegated mode exchanges through an injected `DelegationBroker` and
requires adapter context support; missing configuration refuses that leg and
never retries under service credentials.

The partition plan is authorized before optimizer/admission. Every source,
concept, property, filter, join, and projection use receives an
allow/rewrite/deny decision. Safe rewrites inject principal-bound row scope and
register post-join masks; denied or load-bearing unauthorized uses refuse before
source dispatch. Every returned row is checked against scope before resolution,
assembly, joins, and seed creation. Postflight repeats the decision and governs
bindings, source objects, native/SPARQL citations, disclosure metadata, and
withheld optional data.

The offline catalog PDP is deterministic. The production client is
OpenFGA-compatible and fail-closed, but external IdP/JWKS, OpenFGA
store/model/tuples, STS/source delegation, and source RLS/masking remain
deployment dependencies, not CDF-provisioned features.

## 5. Non-functional requirements
- **No data movement** (loosely-coupled default; assembled only on demand, bounded, temporary).
- **Grounded/cited or refused** — every fact traces to a real sub-query result.
- **Deterministic path is the long-term target; LLM is the safety net** (North Star principle 5).
- **Cost & latency are first-class** — the plan must be inspectable and measurable, not a black box.

## 6. Dependencies
- **Modules:** M4 (mappings), M1 (connectors), M6 (canonical hub), M7 (grounding).
- **Repos (per ADR-0001 + the implementation plan):** **r2g** — the **[[contextual-data-fabric/docs/architecture/_repo-enhancements/r2g-federated-query|federated-query enhancement]]**, reframed: P12.1 forward-CSI+R2RML is the durable contract; P12.2 pushdown SQL is the P1 stopgap vs Ontop. **`arango-sparql-py`** (SPARQL→AQL, owned — finish eval gate + federation entry). **`arango-cypher-py`** (NL→IR engine to harvest; P1 Arango-leg fallback). **`arangodb-schema-analyzer`** (CSI v1 hub). **Ontop** (relational VKG engine, **adopted — Apache-2.0 OSS, free**; PRD §9.10). Reuses agent/query patterns from `customer-context`.

## 7. Phase mapping
- **P1:** loosely-coupled, one relational DB (Postgres) + Arango unstructured graph, LLM planner, full retrieval path.
- **P2:** assembled pattern; deterministic planner hardening; cost/latency instrumentation; Snowflake via M1.
- **P3:** multi-source planner; join optimization; governed policy preflight,
  row/seed enforcement, postflight masking/citations, and OpenFGA-compatible PDP.

## 8. Acceptance criteria / demo (P1)
- A seed CSM question (see [[contextual-data-fabric-prd]] §4) is answered end-to-end: the engine hits **Postgres live** and the **Arango unstructured graph**, joins on the canonical entity, and returns an answer whose **retrieval path shows the actual SQL and AQL** and the source objects. No Postgres bulk copy into Arango. Refuses cleanly if a leg can't be cited.

## 9. Open questions
- LLM vs deterministic **for P1** — default to LLM planner to hit the 1-week goal, deterministic in P2? (Matches PRD §5.2.)
- Join placement: reconcile in the engine vs push a join key to the source. Start with engine-side join via canonical hub.
- ~~Plan representation: is the "query graph over the ontology" the right intermediate?~~ **Resolved — [[adr/ADR-0001-conceptual-query-language|ADR-0001]]:** the conceptual query is a **small typed graph-pattern IR over the ontology that serializes to SPARQL** (query-as-a-graph = SPARQL basic graph patterns); decomposition partitions the query graph by source. Backed by a research pass (75 confirmed findings): OBDA/VKG is the mature pattern (VKG spec `P=(O,M,S)`), Ontop/Stardog do SPARQL→SQL with no materialization across all our relational sources.
- **RESOLVED (ADR-0001, code-read) — IR = SPARQL.** The only option with OWL
  semantics (A2A differentiator) *and* a relational leg (Ontop) *and* an owned
  Arango leg (`arango-sparql-py`). Cypher is the more mature transpiler + owns
  the best NL engine but has no relational leg — so **harvest
  `arango-cypher-py`'s IR-agnostic NL engine to generate SPARQL** (~5 seams).
- **RESOLVED (ADR-0001, code-read) — mapping alignment via `CSI v1` hub.**
  Adopt the existing `CSI v1` interchange (`arango-schema-analyzer`) and build:
  (1) r2g→forward-CSI, (2) CSI→R2RML (Ontop), (3) CSI→MappingBundle (AQL
  transpilers), (4) fix the `phys:` namespace mismatch (`arango-sparql-py`
  accepts a different namespace than the analyzers emit).
- **Cost to accept SPARQL (ADR-0001):** finish `arango-sparql-py` — promote
  real-Arango **evaluation** to a CI gate (translation coverage is broad but
  eval-correctness isn't gated) and fix the variable-predicate→IRI bug; add a
  **query-graph partition entry point + canonical-key return** (it only takes a
  full SPARQL string today). Bounded — weeks.
- **RESOLVED — relational engine:** adopt **Ontop** (Apache-2.0 OSS, free — not a purchase) over r2g P12.2
  (build); ADR recommends Ontop, keep r2g **P12.1 R2RML export** as the contract
  (needed either way per the CSI plan).
- **Net-new regardless of IR:** neither transpiler is federation-shaped — the
  partition planner, canonical-key join, and provenance/as-of are first-class M5
  build, not transpiler tweaks.
