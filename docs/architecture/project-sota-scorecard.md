---
title: "Contextual Data Fabric — SOTA Scorecard"
type:
  - internal
  - scorecard
  - competitive-benchmark
status: current
version: 1.2
date: 2026-08-06
review_cycle: quarterly
requirements: "docs/contextual-data-fabric-prd.md §2.3, §10.1"
related:
  - "docs/architecture/project-scorecard.md"
  - "docs/contextual-data-fabric-north-star.md"
---

# Contextual Data Fabric — SOTA Scorecard

## Purpose

This scorecard turns the aspiration to be state of the art into falsifiable,
competitor-relative gates. It does **not** award points for roadmap intent,
feature presence, or vendor marketing equivalence. A capability earns a high
score only when the project publishes reproducible evidence against a frozen
benchmark and named alternatives.

The regular [[contextual-data-fabric/docs/architecture/project-scorecard|project
scorecard]] answers **“did we implement and gate the planned work?”** Its current
answer is 7/7 implementation gates. This scorecard answers the harder question:
**“have we proved that the result is a top performer?”**

## Current result — 2026-08-06

- **SOTA evidence score:** **51.2 / 100**
- **Promotion status:** below the 70-point SOTA-candidate gate
- **Dimensions at publicly reproducible leader level (4+):** 0/12
- **Dimensions at independently top-performing level (5):** 0/12
- **Implementation gate score:** 100% (separate measure; not a SOTA claim)
- **Current strongest differentiator:** live, cite-or-refuse answer-level provenance
  across SQL/AQL federation with explicit partial/refusal semantics
- **Largest evidence gap:** no public scale/performance or controlled
  competitor bakeoff

The 51.2 score is not a product-quality percentage. It is the weighted maturity
of the **evidence supporting leadership claims**.

## Scoring rule

Every dimension receives an integer evidence level:

- **0 — absent:** no implemented capability or evidence.
- **1 — implemented/design proof:** code or architecture exists, but no
  outcome-level benchmark.
- **2 — internally evaluated:** repeatable unit, fixture, or offline validation
  without a quantitative outcome benchmark.
- **3 — internally benchmarked:** the production path meets a quantitative
  threshold on a versioned internal workload with disclosed inputs, method, and
  an inspectable report. External systems must be live when their behavior is
  what the dimension claims to measure; fixture sources are acceptable when the
  measured subject is an in-process planner or interface contract.
- **4 — publicly reproducible leader:** the project meets the dimension's SOTA
  threshold on a public, version-pinned benchmark that others can rerun.
- **5 — independently top performing:** an independent or controlled bakeoff
  shows leadership over named competitors on the same workload.

`dimension contribution = weight × evidence level / 5`

`SOTA evidence score = sum of all 12 dimension contributions`

The dimension registry is machine-readable at
`src/cdf/eval/corpora/sota-dimensions-v1.json`; `cdf-sota` validates its 12
unique dimensions, 100 total weight, level bounds, contributions, and total.
The runner's top-level `passed` field means its required **evidence checks**
completed successfully. It is not a SOTA promotion result.

No self-assessed result can exceed level 3. Level 4 requires public artifacts;
level 5 requires independent or controlled comparative evidence. Unknown or
undisclosed results never count as wins.

Weights sum to 100 and are frozen for each quarterly cycle. Correctness carries
the largest weight (12) because a fast or broad fabric that returns a wrong
cross-source answer is a failed product. Semantics, optimization, provenance,
and governance each carry 10 because they express the North Star's core
differentiation. Breadth, NL, ER, reliability, and performance each carry 8.
Interfaces and evaluation mechanics carry 4 each as enabling dimensions. A
weight change requires a version bump and must not be used to improve a score
retroactively.

## Dimension scores

### 1. Federated query correctness — 3/5, weight 12, contribution 7.2

**Current evidence:** the full hosted four-engine gate passed 15/15 live
contracts across Postgres/Ontop, Snowflake, ClickHouse, and ArangoDB using
key-pair authentication. Merge-blocking CI passes the 10 cases that do not
require Snowflake and explicitly excludes the other five. Two live cases assert
exact bindings (one positive answer and one empty answer); the other 13 assert
routing, reconciliation, grounding, or refusal contracts. Five fixture goldens
assert exact bindings, and deterministic bind-join, partial-failure, PII, and
injection behavior is contract-tested.

**Why it is not SOTA-proven:** the live corpus is small and mostly
contract-shaped rather than exact-result graded; only 10/15 cases are
merge-blocking. No declared W3C SPARQL/R2RML or federation conformance subset is
published from this repo.

**Leadership threshold:** at least 99% over 200+ live cases spanning one to four
legs; 100% on the declared W3C SPARQL 1.1 and R2RML subsets; zero silent wrong
results under timeout, truncation, retry, schema drift, and source failure.

**Comparators to beat:** Stardog Virtual Graphs, Ontop/GraphDB virtualization,
Denodo, and Starburst/Trino.

### 2. Semantic and ontology depth — 2/5, weight 10, contribution 4.0

**Current evidence:** an authoritative, content-hashed manifest covers ten
classes, source ownership, mappings, join keys, statistics, entitlements,
runtime resolution, and auth mode. Catalog integrity rebuilds exactly.

**Why it is not SOTA-proven:** the demo CSI declares no typed relationships and
does not publish an OWL 2 QL, SHACL, reasoning, alignment-quality, or metric
reproducibility benchmark.

**Leadership threshold:** 25+ classes and typed relationships across at least
five heterogeneous sources; OWL 2 QL or explicitly mapped equivalent; SHACL
constraint validation; certified metrics reproduce identically through SQL,
SPARQL, NL, API, and agent interfaces.

**Comparators to beat:** Stardog, Ontotext GraphDB, Timbr, Palantir Ontology,
and Cambridge Semantics Anzo.

### 3. Connector and source breadth — 3/5, weight 8, contribution 4.8

**Current evidence:** four engine kinds are live in one federated gate:
Postgres/Ontop, Snowflake, ClickHouse, and ArangoDB. All four are configured for
hosted scheduled/manual CI and passed together on the feature branch used to
land the workflow; the three local engines are merge-blocking and passed on the
resulting `main` commit. A post-merge four-engine `main` run is still required
to bind the full evidence directly to that commit.
Connectors share secret resolution, rotation, delegation, telemetry, and
provenance contracts.

**Why it is not SOTA-proven:** four source kinds are far below established
virtualization platforms, and Snowflake is scheduled/manual rather than
merge-blocking.

**Leadership threshold:** 30 production-certified source kinds across at least
six modality families; 12 sources pass one public connector-conformance suite
covering types, nulls, pushdown, cancellation, schema drift, identity, and
failure behavior.

**Comparators to beat:** Denodo, Stardog, Starburst/Trino, Databricks Lakehouse
Federation, and Dremio.

### 4. Federation optimization and economics — 3/5, weight 10, contribution 6.0

**Current evidence:** statistics-driven dynamic-programming planning, a
deterministic greedy fallback, seed direction, preflight/runtime admission,
bounded batching, and per-leg telemetry use production code paths. A versioned exhaustive
oracle corpus covers 2–8 source plans, chains, a star, skew, multi-key joins, and
tie-breaking. The production optimizer matched the optimal join order,
cumulative-row objective, final cardinality, seed directions, remote-byte
estimate, and cost estimate on all 8 cases / 182 feasible plans
(`max_objective_ratio = 1.0`). The corpus also exposed and drove a fix for
disconnected proper subsets of star-shaped dynamic-programming plans.

**Why it is not SOTA-proven:** the oracle uses the planner's disclosed
cardinality model rather than measured remote bytes and latency; there is no
stale-statistics study, cost-per-query SLO, public workload, or comparative
optimizer result.

**Leadership threshold:** source-selection precision and recall at least 0.95;
remote bytes no more than 1.25× an oracle plan; geometric-mean latency no more
than 1.5× oracle and p95 no more than 2× under disclosed network profiles;
100% structured refusal for over-budget plans.

**Comparators to beat:** Denodo cost-based optimization, Starburst adaptive and
fault-tolerant execution, Stardog robust planning, Dremio Reflections, and
Databricks federation pushdown.

### 5. Natural-language and agent accuracy — 2/5, weight 8, contribution 3.2

**Current evidence:** 10/10 offline NL decomposition cases validate parse,
partition, source selection, join keys, refusal, and ingress path. The LLM
fallback is catalog-grounded, few-shot, metered, repaired, and policy-filtered.
The public CK25 execution corpus was run three times with GPT-4o-mini using the
published `arango-sparql-py@623aa24` harness. The owned checkout was dirty at
generation, although the benchmark paths themselves were clean: **5/49, 6/49,
and 6/49**, or
**17/147 (11.6%)**. The tracked, content-hashed report records p50/p95 latency of
1.50/4.68 seconds, 167 provider calls, 852,757 prompt tokens, 16,419 completion
tokens, and **$0.137762** estimated cost. Five cases passed all repetitions, one
passed two, and 43 passed none.

**Why it is not SOTA-proven:** CK25 exposed poor zero-shot execution accuracy
rather than demonstrating a competitive result, so this dimension remains at
level 2. The corpus has no refusal cases, only 49 questions, and is not a
held-out enterprise-federation benchmark. Running a benchmark is evidence;
missing its outcome threshold does not earn a score promotion.

**Leadership threshold:** at least 95% result accuracy on 500+ held-out
enterprise questions, 100% on certified KPI questions, and at least 99%
correct abstention or clarification for ambiguous, unauthorized, and unsupported
requests. Report five stochastic repetitions, latency, tokens, cost, and model
version. Track the contemporary public BIRD execution-accuracy leader as a
general SQL reference, not as a substitute for federation evaluation.

**Comparators to beat:** Databricks Genie, Snowflake Cortex Analyst, Stardog
Voicebox, Timbr agents, and Denodo conversational tooling.

### 6. Answer provenance and grounding — 3/5, weight 10, contribution 6.0

**Current evidence:** grounded live-gate answers carry answer/leg-level
citations; the envelope
records conceptual SPARQL, physical SQL/AQL, source objects, row counts, as-of
time, retrieval status, resolution, authorization, and refusal/partial state.
Ontop now reports its generated PostgreSQL SQL rather than relabeling SPARQL.

**Why it is not SOTA-proven:** there is no W3C PROV-O/OpenLineage export,
tamper-evident audit chain, field-level derivation completeness score, or
cross-product validator.

**Leadership threshold:** 100% field-level provenance completeness over the
gold corpus, including aggregates and entity-resolution edges; stable plan and
result identifiers; PROV-O or OpenLineage-compatible export; zero grounded
answers with incomplete load-bearing citations.

**Comparators to beat:** Palantir lineage, Denodo column dependencies, Starburst
OpenLineage, GraphDB inference provenance, and Stardog linked results. Public
evidence suggests answer-level federation provenance remains an opportunity to
lead.

### 7. Entity resolution — 2/5, weight 8, contribution 3.2

**Current evidence:** the guarded eight-case corpus reports precision 1.0,
recall 0.667, abstention 0.375, zero cross-scope violations, and complete
evidence. Runtime joins fail closed and are budgeted.

**Why it is not SOTA-proven:** evaluation uses a corpus-backed fake resolver;
AER is not released and pinned; there is no large labeled matching/clustering
benchmark or throughput result.

**Leadership threshold:** 500+ held-out examples with automatic-decision
precision at least 0.995, blocking recall at least 0.995, macro and B-cubed F1
at least 0.90, zero cross-scope violations, calibrated confidence, explanations,
rollback, and published throughput at 10 million records.

**Comparators to beat:** Stardog's beta ER and academic cross-domain ER
baselines. Palantir and other vendors expose insufficient public ER evidence,
so an open benchmark can establish leadership.

### 8. Governance and security — 2/5, weight 10, contribution 4.0

**Current evidence:** 30 authorization tests cover allow/rewrite/deny,
tenant-row pushdown, governed bind seeds, masking/HMAC/drop, policy drift,
citations, introspection, prompt filtering, OIDC, secure OpenFGA transport, and
fail-closed behavior.

**Why it is not SOTA-proven:** OpenFGA, IdP, STS, delegation, source-native RLS,
and masking are not live production integrations; there is no adversarial
leakage or policy-overhead benchmark.

**Leadership threshold:** 100% pass over at least 10,000 adversarial policy
cases across results, prompts, logs, errors, caches, statistics, provenance, and
agent tools; live identity passthrough/delegation; fail-closed dependency
outages; p95 policy overhead below 10%; zero secret or unauthorized-value leaks
under fuzzed failures.

**Comparators to beat:** Palantir Ontology/AIP, Databricks Unity Catalog,
Snowflake Horizon, Denodo, and Starburst.

### 9. Reliability and operability — 3/5, weight 8, contribution 4.8

**Current evidence:** one-command local orchestration after documented
prerequisites, mandatory pre-demo gate, declared
partial/refusal behavior, connector rotation and draining, bounded assembly
cleanup, safe retries, strict startup validation for missing catalog connectors,
degraded health metadata in non-strict development mode, and static/unit gates.
The hosted
workflow passed the complete four-engine gate using a repository-secret-backed
Snowflake RSA key, a deterministic 46-row CI fixture, and retained SOTA JSON
on every run plus source logs on failure. `check` and the three-local-engine
`live-local` job are
required on `main`. [Hosted run 31090989998](https://github.com/ArthurKeen/contextual-data-fabric/actions/runs/31090989998)
passed all three jobs on `feat/hosted-sota-evidence` and retained report SHA-256
`21c2947f8b56b3531107cc6bd1add0f4761bf0b8608cd18916ab8a0654257785`.
The subsequent `main` push passed `check` and `live-local`; `live-full` was
skipped by design because it runs only on schedule or manual dispatch.

**Why it is not SOTA-proven:** the Snowflake job is scheduled/manual rather
than merge-blocking, and there is no production SLO history, chaos suite, soak
test, or RTO/RPO proof.

**Leadership threshold:** 99.99% monthly admission/API availability and 99.9%
successful-query availability when declared sources are healthy; RPO zero for
catalog/policy/mappings/audit; RTO below five minutes; zero incorrect
acknowledged results in a 72-hour fault-injection run.

**Comparators to beat:** Snowflake's published query-success commitments and
the 99.5% public service commitments from Denodo Agora, Dremio Cloud, and
Starburst Galaxy.

### 10. Scale and performance — 2/5, weight 8, contribution 3.2

**Current evidence:** bounded rows, bytes, seeds, resolution calls, deadlines,
wall time, concurrency, and assembly TTL are implemented and tested. The
versioned `synthetic-federation-v1` baseline discloses three source cardinalities
(10,000 / 1,000 / 100 rows), bytes and cost rates, 100 planning samples, 20
sequential requests, and 40 requests at four-way concurrency. In hosted run
31090989998, in-process request p50/p95 was 8.34/9.06 ms at 98.3 qps; the
declared 2 ms/source simulated-LAN profile was 14.99/15.76 ms at 92.5 qps.
Planning p95 was 11.24/8.20 ms. These machine-sensitive latency and throughput
figures are a snapshot, not a stable product claim; deterministic fields were
75 source rows, 12,400 bytes, and $0.000000356 reported cost.

**Why it is not SOTA-proven:** this is a small synthetic fixture benchmark, not
a live large-dataset or WAN test; there is no sustained soak, 1-TB workload,
multi-tenant test, or competitor run.

**Leadership threshold:** planning p95 below 250 ms with cached metadata;
single-source overhead no more than 1.25× native; cross-source geometric mean no
more than 1.5× a hand-tuned oracle and p95 no more than 2×; 95% of interactive
queries below five seconds at 32 concurrent users on a disclosed heterogeneous
1-TB workload.

**Comparators to beat:** Denodo, Starburst/Trino, Dremio, Stardog, and
Databricks. Warehouse-only TPC-DS claims do not establish federation leadership.

### 11. Developer and agent interfaces — 3/5, weight 4, contribution 2.4

**Current evidence:** HTTP, semantic MCP, catalog CLI, browser provenance UI,
one-command gates, and safe introspection share the same federation service.
The semantic MCP exposes no raw SQL/AQL escape hatch. A versioned 20-case
corpus now runs identical questions, partial settings, and virtual/assembled
execution modes through real in-process HTTP and MCP transports and compares
normalized semantic envelopes. Fixture sources isolate the measured subject:
transport and envelope parity rather than external connector behavior.

**Why it is not SOTA-proven:** the parity corpus uses fixture source execution
and is not public; there is no published SDK, portfolio-scale agent evaluation,
compatibility policy, or external adopter evidence.

**Leadership threshold:** versioned OpenAPI and MCP schemas tied to catalog
generation; 100% HTTP/MCP parity over 50+ conformance cases; at least 90% agent
success on held-out multi-step workflows; two external reference integrations.

**Comparators to beat:** Palantir Ontology SDK/AIP, Databricks agents, Snowflake
Cortex interfaces, and Timbr's agent benchmark tooling.

### 12. Evaluation rigor — 3/5, weight 4, contribution 2.4

**Current evidence:** live goldens, fixture goldens, NL corpus, resolution
corpus, authorization golden, catalog integrity, Ruff, and mypy are executable.
A single `cdf-sota` / `make sota-baseline-live` runner emits versioned,
content-hashed JSON containing check summaries, durations, environment/package
versions, raw-output hashes, git metadata, and the optional live gate. The
hosted run passed with 310 unit/contract tests (5 environment-gated skips),
15/15 hosted live goldens, 10/10
deterministic NL cases, complete authorization/catalog/static checks,
resolution precision 1.0, the 20-case HTTP/MCP parity gate, and validation of
the retained 147-evaluation CK25 report. Merge-blocking CI runs the 10-case
local live gate and reduced performance smoke tests; full performance parameters
run in the scheduled/manual `live-full` job. CK25 artifact integrity is also
merge-blocking. The evidence runner now fails malformed JSON and false domain
flags even when a subprocess exits zero, reports the 20 parity cases explicitly,
and records owned/runtime package versions. The current local suite passes 322
tests with 5 environment-gated skips.

**Why it is not SOTA-proven:** corpora are small, the full four-engine gate is
not merge-blocking, hosted artifacts expire after 30 days rather than being
signed releases, and no raw competitor bakeoff dataset/results are published.

**Leadership threshold:** one reproducible command emits signed JSON for every
dimension; 500+ total held-out cases with version pins and raw artifacts; all
required live engines in CI; independent rerun instructions; any required-gate
regression blocks merge.

**Comparators to beat:** Databricks Genie evaluation and Timbr benchmark
tooling. Most vendors publish insufficient raw evaluation artifacts, creating
an opportunity for evaluation transparency itself to be a differentiator.

## Leadership frontier

The project should claim leadership only in dimensions that reach level 4 or 5.
The shortest credible path is:

1. **Publish the benchmark package.** The internal runner exists; next freeze
   public workloads, source versions, network profiles, policies, gold result
   hashes, and an inspectable output schema in a signed release.
2. **Turn the differentiator into a public proof.** Expand provenance/correctness
   to 200+ cases and export standards-compatible lineage.
3. **Replace the synthetic performance proof.** Run a disclosed live,
   large-dataset load suite before making external latency or economics claims.
4. **Replace fixture safety proofs with live systems.** Pin AER and run live
   OpenFGA/OIDC/STS plus source-native policy.
5. **Run a controlled bakeoff.** Compare the same six-source workload against
   Stardog, Denodo or Starburst, and one integrated cloud platform.

## Prioritized low-hanging improvement backlog

These items primarily convert implemented capability into stronger evidence.
Score movement is mechanical under the current frozen rubric and applies only
when the listed acceptance evidence is produced.

1. **✅ DONE — Unified evidence baseline runner (+0.8, 2026-08-05).**
   `cdf-sota`, `make sota-baseline`, and `make sota-baseline-live` now emit one
   versioned, content-hashed JSON report covering tests, static checks, catalog
   integrity, authorization, NL, ER, interface parity, optional live goldens,
   durations, environment/package versions, git metadata, output hashes, and
   the validated machine-readable dimension score. A green report means the
   evidence checks passed, not that SOTA was demonstrated.
   Evaluation rigor is promoted from level 2 to level 3.
2. **✅ DONE — HTTP/MCP parity corpus (+0.8, 2026-08-05).**
   A versioned 20-case corpus runs identical question, partial, and execution
   mode inputs through both interfaces and compares normalized semantic
   envelopes while excluding request IDs and timing. Developer/agent interfaces
   are promoted from level 2 to level 3.
3. **✅ DONE — Optimizer oracle benchmark (+2.0, 2026-08-05).**
   Eight fixed cases exhaustively enumerate 182 feasible left-deep plans and
   compare selected join order, cumulative rows, seed direction, final rows,
   estimated bytes, and cost. All cases achieved an objective ratio of 1.0,
   promoting optimization/economics from level 2 to level 3.
4. **✅ DONE — Three-engine merge gate plus hosted four-engine run (+1.6,
   2026-08-06).**
   The workflow runs Postgres/Ontop, ArangoDB, and ClickHouse on every push/PR.
   A hosted feature-branch run also passed Snowflake with RSA key-pair
   authentication, ran the complete evidence baseline, and retained the report;
   failure runs retain source logs. A post-merge full run on `main` remains.
   Reliability is promoted from level 2 to level 3.
5. **✅ DONE — Internal scale/performance baseline (+1.6, 2026-08-05).**
   A versioned synthetic workload publishes p50/p95, concurrency, planning
   overhead, source rows/bytes, cost, dataset cardinalities, and two declared
   network profiles. Scale/performance is promoted from level 1 to level 2.
6. **RUN COMPLETE; PROMOTION NOT EARNED — External 49-case NL corpus.**
   Three execution-graded GPT-4o-mini repetitions produced 17/147 correct
   results (11.6%). Latency, token, call, cost, model, corpus, license, harness,
   dirty-worktree provenance, and per-case consistency evidence is retained
   under `docs/evidence/`.
   NL/agent accuracy remains level 2 until a frozen approach achieves a credible
   outcome; the report must not be converted into +1.6 merely because it exists.
7. **✅ DONE — Evidence and startup false-green hardening (no score change,
   2026-08-06).** CI and default installation share one reviewed full-SHA
   `arango-sparql-py` pin; the live gate enforces its 15/10/5 inventory; tracked
   CK25 integrity is merge-blocking; malformed evaluator output cannot pass;
   and strict startup rejects catalog sources without connector configuration.
   These changes strengthen existing level assignments rather than promoting a
   dimension.

Completed items 1, 2, 3, 4, and 5 moved the evidence score from **44.4 to
51.2**. Item 6 improved diagnosis and reproducibility but not the score because
the measured result failed the promotion threshold. The next outcome-focused
priority is to improve CK25 execution accuracy on a frozen holdout without
leaking test answers into prompts. The next medium-effort evidence promotions
remain live OpenFGA/OIDC policy evidence (+2.0) and a released/pinned live AER
evaluation (+1.6).
Reaching the 70-point SOTA-candidate gate still requires public level-4
benchmarks; internal test volume alone cannot establish competitor-relative
leadership.

## Required controlled bakeoff

The bakeoff must use the same:

- PostgreSQL, Snowflake, ClickHouse, graph, Iceberg/object, and REST/document
  sources;
- 500+ deterministic queries, including one-, two-, and three-plus-source
  joins, skew, aggregation, optional/outer joins, large intermediates, and
  semantic reasoning;
- local, 20 ms, and 100 ms RTT profiles with bandwidth limits;
- stale statistics, schema drift, source throttle/outage, worker loss, and
  identity-provider/PDP outage scenarios;
- blind NL and ER holdouts unavailable to prompts and examples;
- gold materialized results and independent policy/provenance validators; and
- disclosed queries, plans, source calls, bytes, timings, costs, failures, and
  result hashes.

## Competitive reference set

The quarterly review tracks Denodo, Starburst/Trino, Dremio, Databricks,
Snowflake, Stardog, Ontotext GraphDB, Palantir Foundry/AIP, Timbr, Cambridge
Semantics Anzo, Ontop, and — as the **materializing entity-fabric
archetype** — the design partner Data Fabric for Security (their in-house fabric).

the design partner is tracked as a *contrast class*, not a same-workload competitor: its
fabric copies source data into a central entity store (ingest → format →
enrich → resolve/dedupe → group) and is the strongest public write-up of that
approach — relevant to dimensions 6 (its lineage stops at the pipeline; no
answer-level cite-or-refuse), 7 (continuous entity resolution at ingest is
ahead of our deterministic-join posture), and 8 (mature RBAC/lineage claims).
The CDF's default posture is the opposite (zero-copy federation; the ontology
moves, the data does not), but materialization is in the toolkit: r2g is the
ETL path when copying a leg is the right call. Today r2g materializes
per-source; assembling **multiple disparate sources into one materialized
fabric is not yet plumbed**, so no score credit is claimed for it —
materialize-vs-federate is a per-source deployment choice, not an
architectural identity.

Public capability evidence was reviewed on 2026-08-05 from:

- Denodo 9.5 product and optimization documentation:
  https://www.denodo.com/system/files/document-attachments/DS-DenodoPlatform-9.5-final.pdf
- Trino connectors and Starburst cost-based optimization:
  https://trino.io/docs/current/connector.html and
  https://docs.starburst.io/latest/optimizer/cost-based-optimizations.html
- Dremio source and governance documentation:
  https://docs.dremio.com/current/data-sources/ and
  https://docs.dremio.com/current/data-products/govern/
- Databricks Lakehouse Federation and Genie evaluation:
  https://docs.databricks.com/aws/en/query-federation/ and
  https://docs.databricks.com/aws/en/genie-agents/monitor
- Snowflake Semantic Views, Horizon, and Cortex Analyst evaluation:
  https://docs.snowflake.com/en/user-guide/views-semantic/overview,
  https://docs.snowflake.com/en/user-guide/snowflake-horizon, and
  https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations
- Public NL and reliability references:
  https://bird-bench.github.io/,
  https://www.snowflake.com/en/blog/leveling-up-sla-commitment/,
  https://www.denodo.com/en/agora-service-level-agreement,
  https://www.dremio.com/legal/dremio-cloud-support-policy/, and
  https://www.starburst.io/about/security/starburst-galaxy/
- Stardog Virtual Graphs, robust federation planning, and entity resolution:
  https://docs.stardog.com/virtual-graphs/,
  https://labs.stardog.ai/query-planning-federated, and
  https://docs.stardog.com/entity-resolution/
- GraphDB virtualization and provenance:
  https://graphdb.ontotext.com/documentation/11.4/virtualization.html and
  https://graphdb.ontotext.com/documentation/11.4/reasoning.html
- Palantir Ontology, AIP, and lineage:
  https://palantir.com/docs/foundry/architecture-center/ontology-system/,
  https://palantir.com/docs/foundry/architecture-center/aip-architecture/, and
  https://palantir.com/docs/foundry/data-lineage/overview/
- Timbr platform and benchmark framework:
  https://docs.timbr.ai/doc/docs/platform/ and
  https://docs.timbr.ai/doc/docs/platform/managing/benchmarks/
- Ontop and the standards baselines:
  https://ontop-vkg.org/guide/,
  https://w3c.github.io/rdf-tests/sparql/sparql11/,
  https://www.w3.org/TR/rdb2rdf-test-cases/, and
  https://www.w3.org/TR/prov-o/
- the design partner Data Fabric for Security: "Data Fabric For Security — What it Is,
  and Why it Uniquely Addresses the Security Data Challenge" (vendor white
  paper, ©2024; received from the prospect 2026-08-09, on file — public
  copies via https://www.design-partner.com/resources/white-papers). Reviewed
  2026-08-09; vendor evidence per the rule below.

Vendor benchmarks are treated as vendor evidence unless independently audited.
Connector counts exclude aliases where the public documentation makes that
distinction possible. This scorecard must be re-baselined quarterly because
vendor capabilities and public leaderboards move.

## Promotion gates

- **SOTA candidate:** score at least 70, no dimension below level 2, and at
  least three dimensions at level 4.
- **SOTA demonstrated:** score at least 85, no dimension below level 3, at least
  six dimensions at level 4, and one level-5 independent win.
- **Top performer:** score at least 90, no dimension below level 3, at least
  eight dimensions at level 4, and three independently verified level-5 wins,
  including correctness and either performance or governance.

Until those gates are met, use precise claims such as “15/15 hosted live
contracts, including two exact-binding cases” or “100% precision on an
eight-case guarded ER corpus,” not “SOTA.”
