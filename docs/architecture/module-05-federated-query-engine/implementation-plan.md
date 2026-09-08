---
title: "Module 05 — Federated Query Engine — Implementation Plan"
module: 05-federated-query-engine
type:
  - internal
  - implementation-plan
status: completed
version: 0.3
owner: PJ (Paul Losiewicz)
build_gatekeeper: Arthur Keen
depends_on_modules: ["04-mapping-layer", "01-connectors", "06-entity-resolution", "07-grounding-provenance"]
depends_on_repos: ["r2g", "arango-sparql-py", "arango-cypher-py", "arangodb-schema-analyzer", "relational-schema-analyzer", "customer-context"]
related:
  - "[[contextual-data-fabric/docs/architecture/module-05-federated-query-engine/specification|M5 spec]]"
  - "[[contextual-data-fabric/docs/architecture/module-05-federated-query-engine/adr/ADR-0001-conceptual-query-language|ADR-0001]]"
  - "[[contextual-data-fabric-prd]]"
---

# Module 05 — Federated Query Engine — Implementation Plan

> Sequences the M5 build set by [[adr/ADR-0001-conceptual-query-language|ADR-0001]]
> and the [[specification|M5 spec]] into dependency-ordered work packages, cut
> into the P1 (≈1-week demo) slice vs. P2/P3. Every WP traces to a spec FR
> and/or an ADR decision.

## Implementation status (2026-08-06)

**P1 and the recommended P2/P3 implementation sequence are complete.** The
running module now includes:

- **A1–A4:** r2g forward CSI + R2RML emitters, analyzer-CSI compatibility, and
  CSI → MappingBundle translation.
- **B1 / C1 / C2:** live Ontop/Postgres and arango-sparql-py/ArangoDB legs,
  including `seed_bindings` pushdown and execution-time provenance.
- **Native warehouse legs:** Snowflake and ClickHouse executors generated from
  the same CSI/R2RML contract.
- **E1–E3:** deterministic concept-owner partitioning, FILTER/OPTIONAL pushdown,
  multi-source execution and bind joins, retrieval paths, as-of stamps,
  declared partial failure, and cite-or-refuse grounding.
- **D1-thin:** schema-grounded NL → SPARQL with validation/repair plus a
  deterministic prepared-question registry.
- **F1:** 15 hosted live contracts covering the five-question arc, four source
  kinds, empty results, PII refusal, and prompt-injection handling. Two assert
  exact bindings; merge-blocking CI runs the 10 that do not require Snowflake.
- **M9 surface:** one HTTP seam and browser demo with LLM metrics and a dynamic
  Provenance & Execution workflow.
- **P2.1:** versioned NL corpus/decomposition evaluation, deterministic routing
  with policy-filtered few-shot LLM fallback, per-plan/per-leg economics,
  semantic MCP, and Snowflake password/key-pair authentication.
- **P2.2 WP-9–WP-11:** additive CSI statistics consumption, inspectable
  deterministic cost-based join stages, preflight/runtime admission caps, and
  safe deterministic seed batching (never an unseeded overflow fallback).
- **P2.2 WP-12:** explicit bounded assembly in job-scoped temporary Arango
  graphs with lineage, TTL, budgets, and unconditional cleanup.
- **P2.3 WP-8:** M1 `SecretResolver` env/mounted-file backends, per-source
  registries, generation-aware atomic executor rotation and draining, central
  source/assembly error redaction, strict startup validation for missing catalog
  connectors, and safe degraded credential health metadata.
- **P2.3 WP-13:** local/API-ready M6 semantic canonical-hub resolution:
  precision-first AER service/provider/profile plus an independently guarded CDF
  wrapper and versioned quality gate. A clean AER release and CDF pin are still
  pending.
- **P3 WP-14:** strict catalog-bound runtime row resolution through CDF's
  injected guarded-resolver protocol, before canonical seeding/joining,
  telemetry, and optional assembly. It includes bounded calls/batches/deadline,
  duplicate suppression, fail-closed scope/refusal semantics, safe declared
  partials, and value-free evidence/metrics. Demo sources remain `mode: none`.
- **P3 WP-15/WP-17/WP-18 governed query layer:** ADR-0004, immutable bearer-free
  principals/request context, optional generic OIDC HTTP verification, MCP v2
  access-token context mapping, explicit service/delegated source execution
  context, fail-closed delegation, catalog/OpenFGA-compatible PDPs, plan
  rewrites, row/seed enforcement, post-join masking, postflight checks, and
  governed citations/introspection are implemented. External OpenFGA/IdP/STS
  operation and source-native delegated policy remain deployment work.
- **M11 / conditional RSA:** a versioned authoritative catalog manifest with
  content hashes, statistics, entitlements, auth modes, join keys, and runtime
  resolution bindings; the optional RSA bundle → CSI adapter is implemented
  without fabricating R2RML.

The code sequence is closed. Snowflake key-pair authentication has passed a
hosted four-engine run. Remaining work is deployment and outcome evidence:
release and pin AER, provision production OpenFGA/IdP/STS, validate
source-native delegated policy, rerun the full gate on merged `main`, and improve
the measured 17/147 CK25 NL execution result without tuning on scored answers.
The recomputed evidence is recorded in
[[contextual-data-fabric/docs/architecture/project-scorecard|the project
scorecard]].

## Guiding constraints (from ADR-0001 + PRD)

- **IR = SPARQL** (canonical), generated by the schema-grounded NL pipeline in
  **`arango-sparql-py`**. Postgres uses **Ontop** (SPARQL→SQL); Snowflake and
  ClickHouse use native executors; ArangoDB uses `arango-sparql-py`
  (SPARQL→AQL).
- **Mapping hub = `CSI v1`** (owned, in `arango-schema-analyzer`); **r2g is the
  forward producer**. Everything drives off CSI → R2RML (SQL) / MappingBundle
  (AQL).
- **No data movement**; **cite-or-refuse**; **deterministic-capable, cost-
  inspectable**; join cross-source on **AER canonical keys** (M6).
- **Integrate owned components; the genuinely net-new work is the federation
  layer** (partition planner, join, provenance) — everything else is finish/adapt.

## Component readiness (recap)

| Component | State | Gap for M5 |
| :--- | :--- | :--- |
| RSA / `arangodb-schema-analyzer` (mappings) | reverse CSI export drives the Arango source | direct RSA → CSI remains conditional P3 work only if a non-r2g relational path needs it |
| r2g (mapping + pushdown) | forward CSI + R2RML emitters shipped and drive Postgres, Snowflake, and ClickHouse mappings | none blocking |
| Ontop (Postgres SPARQL→SQL) | live, non-materializing, driven by r2g R2RML | none blocking |
| `arango-sparql-py` (Arango SPARQL→AQL) | **A2, A3, C1, C2 landed (2026-07-15, `b26f35d`)**: eval correctness CI-gated (live-Arango + W3C execution suites; variable-predicate IRI bug fixed), `phys:` namespace accepted, CSI→MappingBundle adapter, and `translate_partition` federation entry (canonical keys as subject-IRI columns; **`seed_bindings` VALUES pushdown = the FR-13 bind-join mechanism**; `as_of` executor-stamped). Contract: `arango-sparql-py/docs/architecture/proposals/federation-entry-point.md` — renegotiable before pinning (one consumer today). | **none blocking** — E1 partition contract consumes shape 1 (sub-SELECT string) as shipped |
| `arango-sparql-py` NL pipeline | schema-grounded NL→SPARQL, repair, token/cost records, versioned corpus/evaluator, deterministic router, governed few-shot fallback | external 49-case stochastic corpus remains portfolio-scale evidence |
| AER (M6) | WP-13 service/API, offline precision gate, and WP-14 runtime normalization are implemented in local AER/CDF worktrees | clean AER release + CDF pin |
| M7 (grounding) | cited envelope, cite-or-refuse gate, and provenance UI shipped | richer answer generation remains outside M5 |

## Workstreams & work packages

IDs are `WP-M5-n`. **Owner**: repo WPs → **Arthur** (build gatekeeper); engine
WPs → **PJ**; NL-engine reuse → **shared**. **Dep** = hard prerequisite.

### A. Mapping alignment (unblockers — the CSI hub)
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **A1** ✅ | **DONE** — r2g forward `CSI v1` emitter | r2g | Arthur | — | ADR #3.1; r2g P12.1 |
| **A2** ✅ | **DONE (2026-07-15, `64027b6`)** — analyzer `phys:` namespace accepted as canonical | arango-sparql-py | Arthur | — | ADR #3.4 |
| **A3** ✅ | **DONE (2026-07-15, `a1ef785`)** — CSI v1 → `MappingBundle` adapter (landed in `arango-sparql-py`, not r2g as planned) | arango-sparql-py | Arthur | ~~A1~~, A2 | ADR #3.3 |
| **A4** ✅ | **DONE** — CSI → R2RML serializer for Ontop and native warehouse executors | r2g | Arthur | A1 | ADR #3.2; r2g P12.1 |

### B. Relational leg (SPARQL→SQL)
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **B1** ✅ | **DONE** — live Ontop over r2g R2RML; SPARQL→SQL pushdown vs Postgres without materialization | infra / CDF | Arthur | A4 | FR-2; ADR #2 |
| **B1-alt** | **RETIRED** — Ontop was adopted; native executors cover warehouse dialects | r2g | Arthur | A1 | FR-2; ADR #2 / r2g P12.2 |

### C. Arango leg (SPARQL→AQL) — finish `arango-sparql-py`
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **C1** ✅ | **DONE (2026-07-15, `acf6892` / WP-BE-EVALGATE)** — variable-predicate→IRI bug + 4 AQL runtime bugs fixed; live-ArangoDB + W3C execution suites run in CI (service container) + nightly | arango-sparql-py | Arthur | A2 | ADR #1 cost; FR-3 |
| **C2** ✅ | **DONE (2026-07-15, `b26f35d`)** — `translate_partition(PartitionSpec, resolver, canonical_keys)`: wire shape 1 (sub-SELECT string), canonical key = subject IRI as its own result column, **`seed_bindings` VALUES pushdown** (hostile-seed escaping tested), `as_of` executor-stamped; two-leg federation parity test (partition + pushdown + engine-join == whole-query). **Contract doc: `arango-sparql-py/docs/architecture/proposals/federation-entry-point.md`** — E1 may renegotiate before pinning | arango-sparql-py | Arthur | C1 | ADR #1/#4; FR-3/4 |

### D. NL → SPARQL IR (reuse the owned engine)
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **D1** ✅ | **DONE (thin P1 seam)** — schema-card prompt, SPARQL extraction, planner validation/repair, refusal, and provider metering | arango-sparql-py / CDF | shared | A3, C1/B1 | ADR #1; FR-6 |
| **D2** ✅ | **DONE (2026-08-05)** — versioned NL corpus, deterministic/fixture-capable evaluator, lexical few-shot retrieval, decomposition/source/join/refusal/path scoring, and policy-filtered prompt context | arango-cypher-py → CDF | shared | D1 | FR-6; PRD §10.1 |

### E. Federation engine (the net-new heart of M5)
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **E1** ✅ | **DONE** — CSI concept-owner partitioning with per-source subqueries, join keys, and supported FILTER/OPTIONAL pushdown | CDF (M5) | PJ | A1/A3 | FR-1 |
| **E2** ✅ | **DONE** — execute and reassemble Ontop, Snowflake, ClickHouse, and Arango partitions using bounded seed pushdown | CDF (M5) | PJ | E1, B1, C2 | FR-2/3/4 |
| **E3** ✅ | **DONE** — retrieval path, provenance, as-of, partial failure, and cite-or-refuse grounding | CDF (M5) | PJ | E2 | FR-5/11/12 |

### F. Eval & agent surface
| WP | Work | Repo | Owner | Dep | Trace |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **F1** ✅ | **DONE** — 15-case live golden regression gate | CDF | PJ | E3 | PRD §10.1 |
| **F2** ✅ | **DONE (2026-08-05)** — MCP v2 stdio surface with `federate(question) → cited envelope`, safe catalog/NL introspection, injectable service wiring, and the SDK's OAuth resource-server hook for authenticated HTTP deployments; no raw AQL/SQL tools | CDF | PJ | E3 | FR (agent I/F) |

### Deferred (P2/P3)
| WP | Work | Phase | Trace |
| :-- | :-- | :-- | :-- |
| **G1** ✅ | **DONE** — deterministic corpus router is the default, with catalog-grounded and policy-filtered LLM fallback | P2 | FR-7 |
| **G2** ✅ | **DONE** — per-plan and per-leg latency, rows, bytes/cost when available, retries, seed strategy, truncation, resolution, and assembly telemetry | P2 | FR-9 |
| **G3** ✅ | **DONE** — explicit bounded materialized-subgraph mode with lineage, budgets, TTL, and cleanup | P2 | FR-8 |
| **G4** ✅ | **DONE (pulled forward 2026-07-22)** — native `SnowflakeExecutor`; ClickHouse subsequently landed through the same seam | Sprint 2 | r2g P12.7 |
| **G5** ✅ | **DONE** — statistics-driven dynamic-programming join ordering with deterministic greedy fallback and admission estimates | P3 | FR-10 |
| **G6** ✅ | **DONE (conditional adapter)** — RSA bundle → validated CSI v1 without claiming or fabricating R2RML | P3 | ADR #3.5 |

## Completed P1 critical path

```
A1/A2/A3 → A4 → B1/C1/C2 → E1 → E2 → E3 → F1
                         ↘ D1-thin ↗
```

Every node on the P1 path is complete and live-gated. The authoritative
completion record is the
[[contextual-data-fabric/docs/archive/p1-closeout-plan|P1 close-out
record]]. D2 and the P2/P3 work packages are independent follow-on work, not
blockers on the shipped demo.

## P1 — completed walking skeleton

**Original goal (M5 spec §8 / PRD B4; exceeded):** one seed CSM question answered end-to-end,
federating **live Postgres + the Arango unstructured graph**, joined on the
canonical entity, returning an answer whose **retrieval path shows the actual
SQL + AQL**; no bulk copy; refuse if a leg can't be cited.

P1 exceeded its original thin-slice target:

- r2g emits the production CSI/R2RML mappings.
- Ontop was adopted for Postgres; Snowflake and ClickHouse use native
  executors; ArangoDB uses arango-sparql-py.
- D1-thin translates free-form NL when configured and retains deterministic
  prepared prompts for the scripted demo.
- E1/E2/E3 implement general concept-owner partitioning and four-kind
  federation over the locked `account_id` join spine.
- F1 runs 15 live golden cases rather than the original 1–2 seed questions.

**P1 exit: achieved.** The demo returns grounded, cited answers with real
SQL/AQL, source objects, row counts, as-of metadata, and a declared join path;
no source data is bulk-copied, and uncitable requests refuse cleanly.

## P2 / P3

- **P2.3 WP-8 complete (2026-08-05):** source credentials resolve only when
  executors are built, never from CSI/R2RML. Production mounted JSON secrets
  rotate by opaque generation alias; replacement failure keeps the last
  known-good executor, while successful replacement drains the old adapter
  after in-flight calls complete. HTTP/MCP/retrieval/assembly errors share one
  redaction boundary.
- **P2.3 WP-13 implemented locally/API-ready (2026-08-05):** AER and CDF now
  share a precision-first canonical-hub contract with independent scope/oracle/
  deadline guards, threshold + margin abstention, complete evidence, and a
  deterministic quality gate. The AER API is not on remote `main`; do not add a
  CDF pin until a clean AER release is cut.
- **P2 complete (2026-08-05):** D2/G1/G2 provide the versioned NL evaluation,
  deterministic planner default, governed LLM fallback, and per-leg economics;
  F2 provides semantic MCP; P2.2 provides statistics, optimization, admission,
  bounded seed handling, and explicit assembly.
- **WP-14 / P3 complete (2026-08-05):** configured source rows are normalized
  to canonical IDs through CDF's injected guarded-resolver seam before seed
  generation, joining, telemetry row counts, or optional assembly. Native
  unmatched keys are removed; cross-account/refused outcomes fail closed;
  strict/partial envelopes declare counted shortfalls and value-free evidence.
  Resolution-aware planning keeps normalization legs unseeded, then seeds only
  canonical bindings. AER remains unreleased/unpinned and demo sources remain
  disabled, so this is a runtime integration seam rather than a released AER
  deployment claim.
- **WP-15/WP-17/WP-18 policy layer complete (2026-08-05):** HTTP and MCP edges
  can produce the same immutable query-plane principal/context; request
  metadata and context propagate through service, assembled, concurrent, and
  connector seams; delegated sources fail closed without broker/adapter
  support. M11 rules and OpenFGA-compatible checks now drive allow/rewrite/deny
  preflight and postflight, row scope, masking/drop, citation disclosure, and
  introspection. OpenFGA/IdP/STS provisioning, full tenant deployment isolation,
  source-native RLS/masking, and actual Snowflake/Postgres delegation remain
  external.
- **G3 / WP-12 boundary:** `virtual` remains the default. Explicit `assembled`
  requests require statistics-backed preflight estimates and mandatory
  row/serialized-byte/wall-time/TTL budgets, then use an unpredictable job ID
  and isolated temporary Arango graph/collections. Source rows and deterministic
  joined intermediates are materialized with lineage and `derived_from` edges;
  cleanup is unconditional with TTL indexes as crash fallback. The proven
  Python table-binding join remains answer-authoritative until arbitrary AQL
  joins have an independent semantic-parity gate.
- **P3 code complete (2026-08-05):** WP-14–WP-18, M11, G5, and conditional G6
  are implemented and gated. Production OpenFGA tuple/model operations,
  source-native delegated/RLS/masking integrations, and a released AER pin are
  deployment follow-ons.

## Risks (carried from ADR-0001)

1. ~~**`arango-sparql-py` evaluation correctness is unproven**~~ — **RETIRED
   (2026-07-15):** C1 landed (variable-predicate IRI fix + 4 AQL runtime bugs;
   live-Arango + W3C execution suites CI-gated) and C2 shipped the federation
   entry with a two-leg parity test. The P1 Cypher→AQL fallback is withdrawn.
2. ~~**Ontop infra vs r2g P12.2**~~ — **RESOLVED:** Ontop is the Postgres leg;
   native executors handle Snowflake and ClickHouse.
3. **Free-form SPARQL generation:** D2 provides a repeatable fixture corpus and
   governed few-shot fallback. The external 49-case CK25 corpus has now run
   three times with GPT-4o-mini and achieved only 17/147 (11.6%); this is a
   measured quality gap, not an untested path or a score promotion.
4. **Reasoning at build vs query time** (ADR #5): materialize `sameAs`/
   `equivalentClass` in M2/M3 so M5 stays fast/deterministic.

## Acceptance

- **P1:** ✅ complete — five-question arc, four live source kinds, real SQL/AQL
  in the retrieval path, deterministic join spine, no bulk copy, clean refusal,
  and 15 hosted live contracts (10 merge-blocking; 2 exact-binding graded).
- **P2:** ✅ complete — canonical SPARQL-OBDA loop passes the live and offline
  gates with deterministic planning by default, governed LLM fallback,
  per-source telemetry, budgeted optimization, and explicit bounded assembly.
- **P3:** ✅ implementation complete — ≥3-source cost-based federation, runtime
  canonical resolution, authoritative catalog, OIDC/delegation contracts, and
  allow/rewrite/deny governance are code-gated. Production external services
  remain deployment certification work.
