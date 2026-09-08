---
title: Contextual Data Fabric — PRD
categories:
  - "[[Projects]]"
type:
  - internal
  - PRD
date: 2026-07-13
org:
  - "[[Arango]]"
project:
  - "[[Arango Contextual Data Fabric]]"
related:
  - "[[Customer360]]"
  - "[[the design partner]]"
  - "[[2026-07-13 the design partner Customer Context Roadmap]]"
  - "[[the design partner Feedback Summary]]"
people:
  - "[[Arthur Keen]]"
  - "[[MF]]"
  - "[[MG]]"
  - "[[DBM]]"
topics:
  - "[[Ontology]]"
  - "[[Graph]]"
status: draft
version: 0.3
---

# Contextual Data Fabric — Product Requirements Document

> **Status:** Draft v0.2 for team review. Posted per the [[2026-07-13 the design partner Customer Context Roadmap]] action item ("PJ to draft PRD"). Arthur needs this before refactoring r2g / the ontology extractor so we scope the build rather than over-build.
>
> **v0.2 (2026-07-13):** reconciled every "as understood — Arthur to confirm" claim against the actual repos (r2g, relational-schema-analyzer, arango-schema-analyzer, arango-ontoextract, arango-entity-resolution). The ★ structured→ontology question is **resolved (yes)**; the risk has moved to **ontology alignment** and **r2g pushdown query generation**, which are builds, not confirms. Repo references in §8 are now pinned. New §10 adds cross-cutting requirements (evaluation, agent interface, consistency, partial failure, caching, security).
>
> **v0.3 (2026-07-14):** absorbed the deep-analysis passes. **ADR-0001** (M5) decides the conceptual-query IR — typed graph-pattern serializing to **SPARQL**, `CSI v1` as the mapping hub, Ontop buy-vs-build open (§9.10) — making M5 mostly integration of owned components (`arango-sparql-py`, `arango-cypher-py`, the analyzers). AOE PRD **§6.17–§6.19** definitizes alignment / A-box / competency questions; r2g Phase 12 is reframed around CSI+R2RML. **Use cases formalized** from PJ's 12 locked questions (`docs/use-cases.md`, §4); `customer-context` cloned + verified.
>
> **Reviewers:** [[Arthur Keen]] (build gatekeeper), [[MF]], [[MG]], [[DBM]].
>
> **How to read this:** §1–§5 are the general vision and architecture. **§6 is the phased plan; §7 is the detailed Phase 1 (the 1-week near-term goal).** §8 lists the repos to pull into Claude Code context. §9 is open decisions for the team. §10 (new in v0.2) is cross-cutting requirements that bind every module.
>
> **Companion:** [[contextual-data-fabric-north-star|North Star]] — the end-state vision every phase ladders toward. This PRD is the near-term contract; the North Star is the horizon to check scope against.

---

## 1. Summary

**Contextual Data Fabric** repositions Arango from a *data store* to an **ontology-based metadata / agent-brain hub**. Enterprises have data scattered across many warehouses, lakes, apps, and unstructured sources, each with its own local semantics. Instead of moving that data, the Contextual Data Fabric:

1. **Auto-derives a use-case-driven ontology** from both structured (schemas, catalogs, Snowflake/Databricks/Postgres) and unstructured (docs, Slack, email, transcripts) sources, and **aligns** them into one master conceptual model; and
2. **Executes federated queries** — an English question hits the ontology, the ontology's functional mappings decompose it into per-source queries (SQL pushdown, AQL, agent calls), results are reassembled, and the answer comes back **grounded and cited** with the retrieval path spanning every source it touched.

Arango holds the ontology, the entity resolution / canonical entities, the mappings, and *selected* context — **not** the bulk of the raw data. Everything else stays at the source and is fetched on demand. (Mental model: the PubMed/NIH ~16 TB **metadata** graph that stores linkages, not raw data.)

This sits under [[Arango Contextual Data Fabric]]and is being built for (and pressure-tested against) the [[the design partner]] customer-context engagement, but the fabric is a general, composable platform capability.

---

## 2. Problem & Context

### 2.1 The general problem
In the agent era, the bottleneck is no longer storing data — it is giving agents a **single, governed, semantically-normalized view** across many systems without copying everything into one place. If every agent talks directly to every system (agent-to-agent, "A2A"), you get an **N² translation problem** and you must re-implement business rules and access control on every edge. An **ontology** turns that into a wagon-wheel (linear): translate once to a shared representation; enforce rules once, in one place.

### 2.2 The customer signal ([[the design partner]])
From the [[2026-07-10 - C360 the design partner Demo]], [[2026-07-09 - C360 field-feedback review]], and [[the design partner Feedback Summary]]:

- **Current state:** Snowflake medallion (bronze/silver/gold), **data mesh** with team-owned marts; a "Customer 360 view" exists but **each domain re-creates the same semantics/metrics** — duplication is the pain.
- **Ask #1 — auto-derive the ontology.** the data-platform lead: *"the biggest challenge is defining these entities… we want to do it in a programmatic way, as new things pop up, not rely on someone's knowledge."* Our hand-modeled graph was flagged as **unrealistic at scale**. Framing: **"structured data in, ontology out."**
- **Ask #2 — Arango as the routing brain, no data duplication.** *"We don't want to move the data… the brain has to be on this side."* Agents hit Arango first; if it can't answer, the ontology routes to the source, fetches live, resolves the entity, returns.
- **Hard constraints:** no bulk materialization into Arango; **cost and latency are political** (the data-platform lead has discouraged his team from Arango over token costs — see [[2026-07-09 - C360 field-feedback review]]); ontology overlap across domains must be reconciled; **they want to SEE it working**, not conceptual.

### 2.3 The competitive question we must answer
**If a customer already has a Snowflake agent and can orchestrate via A2A, what does Arango add?** The prototype must *demonstrate*, not assert:
1. **Ontology normalization at the complexity threshold** — A2A can't reconcile `customer account` vs `client account` vs `account`; an ontology does.
2. **Governance the agents inherit for free** — business rules, security, access control enforced at the ontology level (the Palantir / IAM-via-ontology pattern).
3. **Grounded, traceable routing** — the hub knows what it can answer and, when it can't, routes with a **cited** retrieval path that spans Arango *and* the source system. Nobody doing pure A2A has this.

---

## 3. Goals & Non-Goals

### 3.1 Goals
- Auto-derive and **align** a use-case-driven ontology across structured + unstructured sources.
- **Federated query** across sources with **no bulk data duplication** (loosely-coupled first; assembled/materialized when analytics demand it).
- Preserve our existing differentiator: **grounded, cited answers** with a full retrieval path — extended across the federation boundary (SQL + AQL + source object in the citation).
- Ship as **composable building blocks** (Lego model) — each independently publishable, LLM *or* deterministic implementations both valid — not one monolith.
- Be **OSI**-aligned (open semantic interface; already implemented in the relational analyzer / ontology extractor — worth touting; 40+ companies signed on).

### 3.2 Non-Goals (for now)
- We are **not** building the customer's agent application or their agent-orchestration layer — we are the **data/ontology hub** they consult. (Demos may use a thin agent layer we don't sell.)
- We are **not** mirroring whole warehouses into Arango.
- We are **not** shipping ontology-based access control (OBAC/IAM-via-ontology) in early phases — it is a high-value **future** capability and partnership angle, flagged for research.
- **Synthetic data is deferred** — architecture first, data second (per the roadmap). Phase 1 uses the minimum synthetic footprint needed to demo.

---

## 4. Users & Use Cases

**Primary persona:** an enterprise **agent** (and the teams building agents) that needs a governed, cross-source view — e.g. a Customer-Success / sales agent reasoning over a customer's full journey.

> **Formalized (v0.3.1):** use cases, personas, and the competency-question table now live in **[`docs/use-cases.md`](use-cases.md)**, derived from PJ's **12 locked questions** in `customer-context` (7 recovered verbatim from the repo; 5+2 to be committed by PJ) plus his recorded decisions: user = internal Arango employee, interaction = **natural language only**, and **no source-picker** — the ontology routes to sources; users offered a picker would select everything anyway (the AutoGraph-slider lesson). Proposed P1 demo question: **Q2** (renewal risk + WHY), with the structured leg moving to live Postgres. *(Q12, the former "green metrics / red sentiment" centerpiece, was dropped 2026-08-04 — the sentiment/entity extraction it needed is deprecated.)*

**Seed use cases** (drive the ontology — use-case-driven, not boil-the-ocean). From [[C360 Example Questions]]:
- *Across my accounts, how should I prioritize my attention?*
- *An executive wants to meet a client — which account benefits most from C-suite time?*
- *Product has new offerings and wants feedback — which account and which people should I nominate?*
- *Across my portfolio, which accounts are missing information I should collect? Stack-rank it.*

These are the questions a CSM actually asks, they require joining structured metrics with unstructured sentiment/signals, and they are the natural anchor for the ontology's scope.

---

## 5. Architecture Overview

Two clean separation points (the team's own framing) — likely two publishable libraries / building blocks:

### 5.1 Building block A — Onto Extract layer (ontology extraction + alignment)
- Extract a **source ontology** from each source: relational schemas / catalogs (via **r2g** + the relational-schema analyzer), Snowflake/Databricks/Postgres metadata, and unstructured corpora (via the **ontology extractor**).
- **Align** per-source ontologies into a master conceptual model: extract per source → compute **diffs/deltas** → accept/reject into the master → **iterative/cyclic refinement** to convergence. Curation by agent or human per policy.
- **Belief management:** track which ontology element came from which document/schema; cascade updates/deletions on source change; **time-travel** across ontology versions; **change control** (a human or agent blesses expansions — important because the ontology drives access and business rules).
- Output is a **conceptual schema** (not an academic BFO-style ontology) with **functional mappings** to each source: concept→table, property→attribute, and value transforms (e.g. inches↔mm). The mapping *is* what becomes the query.

### 5.2 Building block B — Federated query layer
- English question → resolve concepts against the ontology → the mappings determine **which sources** must be queried → **decompose** into per-source queries → execute (SQL pushdown, AQL over Arango, or agent calls) → **reassemble** → return a validated, **cited** answer envelope.
- Analogue: the Cypher/SPARQL **transpiler** (English → Cypher → AQL), but general-purpose and federated — it must *break the query up*, run the parts, and stitch results. **Now decided (ADR-0001, M5):** the conceptual-query IR is a **typed graph-pattern IR serializing to SPARQL** (the OBDA/VKG pattern); the owned transpilers `arango-sparql-py` (SPARQL→AQL) and `arango-cypher-py` (NL engine + Cypher→AQL fallback) carry the Arango leg, Ontop or r2g P12.2 the SQL leg — M5 is mostly **integration of owned components**; the net-new build is the federation layer (partition planner, canonical-key join, provenance).
- **Two query patterns:** **loosely-coupled** (pointers on the entity, fetch on demand — the Phase-1 default) and **assembled** (temporarily materialize a subgraph into Arango for graph analytics like PageRank / persistent case files, à la the JLR "contextual data fabric" case Arthur built).
- **Decomposition stance (composable):** offer both an **LLM** quick-and-dirty decomposer *and* a **deterministic** pipeline; the deterministic path is the long-term target, with the **LLM as the safety net**. The PRD does not force one — it's a blueprint choice.
- **Conceptual-query language ([[contextual-data-fabric/docs/architecture/module-05-federated-query-engine/adr/ADR-0001-conceptual-query-language|ADR-0001]]):** the question is expressed against the ontology in a **conceptual-query IR** — recommended **SPARQL** (the OBDA / Virtual Knowledge Graph pattern, where "the mapping is the query" is literally R2RML + SPARQL→source rewriting; OWL semantics reconcile terms across sources). **We already own the transpiler stack on both sides:** RSA (relational) + `arangodb-schema-analyzer` (Arango) emit a shared conceptual↔physical bundle; **Ontop** (SQL, buy) and **`arango-sparql-py`** (AQL, own) transpile SPARQL, while **`arango-cypher-py`** (own, mature) transpiles Cypher and ships a proven NL→conceptual-query engine. So M5 is mostly **integration of owned components**, not a build. A code-read of the transpilers settled the IR fork (ADR-0001): **SPARQL is the canonical IR** (only option with OWL semantics + a relational leg via Ontop + an owned Arango leg via `arango-sparql-py`), while **`arango-cypher-py`'s proven NL engine is harvested to generate it**. Mapping alignment is via the existing **`CSI v1`** interchange (r2g as forward producer → R2RML for Ontop, MappingBundle for the AQL transpilers). Decomposition = partition the query graph by source; join on AER canonical keys. The partition planner, canonical-key join, and provenance are the genuine net-new M5 build.

### 5.3 The hub
Arango stores the master ontology, the mappings, the **canonical entities / entity resolution** (via **AER**), and *selected* context (including the already-ingested unstructured graph). It is the **router**: answer locally when possible; otherwise federate out with a cited path.

### 5.4 Reconciliation with our existing differentiator
Auto-derive the ontology (the T-box) → **compile it into the same clean named-graph + curated-AQL layer we already own** (the [[Customer360]] v3 pipeline). This automates the modeling step ("don't hand-craft") **without** losing deterministic named traversals or the citation/AQL grounding ("no black box"). The ontology then does double duty as the Arango-vs-source router.

---

## 6. Phased Approach

The near-term goal (per Arthur) drives Phase 1; later phases widen source coverage and add governance/analytics.

| Phase | Theme | Outcome |
|-------|-------|---------|
| **Phase 1 (≈1 week)** | **Federated query to one database + unstructured docs in Arango** | An English question answered by federating **one relational DB (live, not mirrored)** with the **unstructured graph already in Arango**, unified by a small use-case-driven ontology, returned **grounded + cited** with a retrieval path spanning both. Proves the "what does Arango add over A2A" story at small scale. |
| **Phase 2** | Highest-value connector + assembled pattern | **Snowflake** connector — **pulled forward: due 2026-07-24** (free-tier check RESOLVED: 30-day trial, $400 credits, no credit card; see §7.7); the **assembled/materialized** query pattern for analytics; richer ontology **alignment** across ≥2 structured sources + unstructured; cost/latency instrumentation to directly answer the data-platform lead's token objection. |
| **Phase 3** | Governance + change management | **Ontology-based access control** (IAM-via-ontology / Palantir pattern) via declarative mappings; belief-management **change control**, curation workflows, and **time-travel** surfaced; **Databricks** connector. |
| **Cross-cutting** | Packaging & standards | Composable **pip-library** packaging of both building blocks; **deterministic** query-pipeline hardening; **OSI** compliance surfaced; synthetic-data generation (deferred) once the architecture is proven. |

---

## 7. Phase 1 — Detailed (the 1-week near-term goal)

**Objective:** Demonstrate a **federated query across one relational database and the unstructured documents already ingested in Arango**, unified by an auto-derived, use-case-scoped ontology, returning a grounded, cited answer whose retrieval path spans both sources — **with no bulk data moved out of the relational DB**.

### 7.1 The "one database"
**Proposed: PostgreSQL.** Rationale: Arthur already has a Postgres connector with substantial capability built in; it is the fastest path to a working proof (the roadmap explicitly says "start with Postgres for the proof"). **Snowflake is the higher-value target but is gated on a free-tier check (PJ, in progress)** and moves to Phase 2. *(Open decision §9 if the team wants to attempt Snowflake directly in Phase 1.)*

> **v0.2 note:** the free-tier check gates the **demo account only**, not connector code — Snowflake support *already exists* in both r2g (Phase 6, done: metadata introspection + consistent-snapshot streaming sessions) and `relational-schema-analyzer` (live source). If PJ's account check lands early, Snowflake-in-P1 costs connector-config time, not build time.

### 7.2 The unstructured side (already in Arango)
Reuse the [[Customer360]] v3 pipeline (repo `customer-context`): connectors/chunking → LangGraph extractor (A-box instances against a hand-authored T-box) → span gate → person coreference → embeddings (BM25 + vector) → **AER** entity resolution → survivorship. This graph is already query-ready; federating over it just needs **AQL generation** driven by the ontology (deterministic).

### 7.3 Phase 1 building blocks (work breakdown)

| # | Block | What it does | Proposed owner |
|---|-------|--------------|----------------|
| **B1** | **Structured → ontology** | Run r2g / relational-schema analyzer on the Postgres schema → source ontology (concepts, properties, keys). Confirm/produce the unstructured-side ontology from the Arango doc graph. | Arthur |
| **B2** | **Ontology alignment (minimal)** | Align the Postgres ontology + the unstructured ontology into a small **use-case-scoped** master (seed use cases from §4). Show the human-in-the-loop **"confirm ~2%"** step. Hand-construction of the central ontology is acceptable at this size. | Arthur + PJ |
| **B3** | **Functional mappings** | Master-ontology concept/property → Postgres table/column (with any value transforms) **and** → Arango collection/AQL. The mapping is the query. | Arthur |
| **B4** | **Federated query executor** | English → resolve concepts via ontology → decompose → generate **Postgres SQL (pushdown)** + **Arango AQL** → execute → reassemble. LLM decomposer, deterministic mapping execution, LLM as safety net. | PJ |
| **B5** | **Grounded, cited retrieval path** | Validated answer envelope + citations + retrieval path spanning **actual SQL + AQL + source objects** (extend the existing customer-360 citation/envelope + traversal viz). | PJ |
| **B6** | **Thin demo harness** | Minimal UI (reuse the customer-360 Vercel app pattern) to run 1–3 seed questions end-to-end, on the CC-8 topology (`docs/architecture/deployment-p1.md`). | PJ |
| **B7** | **Cost/latency baseline** | Record tokens + wall-clock per seed question on the fabric path (planner, per-leg, total), **and the same question answered the naive way** (all relevant context stuffed to the LLM / simulated A2A) — the comparison point the data-platform lead's objection demands. Half a day: instrument-lite logging, one table in the demo appendix. Full instrumentation stays P2 (M5 FR-9). | PJ (measured while building B4) |

### 7.4 Phase 1 success criteria (demo)
- At least **one** (target 2–3) seed question answered **end-to-end**, federating live Postgres + the Arango unstructured graph.
- **No bulk Postgres data mirrored** into Arango — the relational facts are fetched live via pushdown; the citation shows the **actual SQL** and source object.
- The **ontology is visibly auto-derived** from the Postgres schema (+ unstructured side) and **aligned**, with the human-confirm step shown.
- The answer is **grounded**: exact citations + retrieval path across both sources; refuses if it can't cite (existing grounding gate).
- A **cost/latency baseline table** (B7): fabric path vs naive path for at least one seed question — tokens and wall-clock. Promoted from nice-to-have in v0.2.1: the cost objection is the stated biggest headwind, so the demo carries at least one number, not zero.

### 7.5 Phase 1 out-of-scope
Snowflake/Databricks; assembled/materialized pattern; OBAC; belief-management change control & time-travel UX; full synthetic-data program; multi-structured-source alignment. All deferred to Phase 2+.

### 7.6 Phase 1 dependencies & risks
- **★ RESOLVED (v0.2): does structured→ontology exist? Yes — twice over, with documented ownership.** AOE's capability table marks **Schema → Ontology (structured): Done** (ArangoDB collection schemas and relational/SQL schemas → OWL/SHACL classes/properties/constraints), with the split defined as *"AOE owns the SQL→OWL/SHACL mapping; `relational-schema-analyzer` is a read-only physical-schema introspector."* Independently, **RSA v0.4.0** (PyPI) emits `{conceptualSchema, physicalMapping, metadata}` bundles + OWL export from 7 live sources (incl. Postgres, Snowflake, Databricks) and dbt/OSI catalogs. r2g Phase 10 (implemented) adds LLM-assisted ontology derivation with a human review UI — the "confirm ~2%" step of B2 already has a working surface. **B1 is wiring, not building.**
- **★ NEW make-or-break: multi-source ontology alignment is a build, not a confirm.** AOE's README states it outright: *"Multi-source ontology alignment: Not built."* Building blocks exist (effective-graph union + import conflict flagging, the cross-tier overlap-candidate finder, pairwise class merge) but there is no alignment/resolution orchestration. **The P1 plan (not fallback) is to hand-construct the small use-case-scoped master (B2)**; automated alignment lands P2 per the AOE spec `arango-ontoextract/docs/multi-source-alignment.md`.
- **r2g pushdown query generation is the other genuinely new P1 build.** r2g already emits mappings decoupled from any load (`ingest-schema → schema.json → generate-config → mapping.yaml`), so runtime-mapping export and a no-materialization mode are near; **per-source pushdown query generation does not exist yet** — specified as r2g PRD **Phase 12** and in the `r2g-federated-query` enhancement spec.
- ~~**`customer-context` has no local copy.**~~ **Step zero DONE (v0.2.1):** cloned to `~/code/customer-context` and verified — the v3 ingestion pipeline (span gate, coref, **real AER imports**, survivorship, `account_scope_key` over-merge guard), the pure-code grounding gate + typed zod envelope (`agent/src/`), and the Next.js citation/retrieval UI (`web/`) all exist as described. One confirmed gap: the envelope is single-deployment two-graph shaped (`structured|unstructured`) — the multi-source citation extension (M7 / customer-context RE-2) is genuine work.
- **Postgres vs Snowflake** — Phase 1 assumes Postgres; the pending free-tier check gates the demo *account* only — connector code already exists (see §7.1 note).
- **Minimal synthetic footprint** — Phase 1 needs a small synthetic Postgres schema + the existing unstructured corpus; full synthetic-data work stays deferred. (r2g ships Chinook/Pagila sample Postgres databases under `docker/` — a candidate P1 schema at zero cost.)

---

### 7.7 Snowflake sprint (added 2026-07-21 — **due Friday 2026-07-24**)

**Objective:** Snowflake joins the federation as a **third live source**, making the demo a genuine three-source federation matching the locked source-system inventory: **CRM → Postgres, usage telemetry → Snowflake, documents → ArangoDB**, joined on `account_id`.

**Instance decision (research, 2026-07-21):** a **Snowflake 30-day trial account** — $400 credits, **no credit card, auto-suspends at exhaustion**; our workload (46 telemetry rows, XS warehouse, 60s auto-suspend) is effectively free. A real service keeps the "live Snowflake" claim honest. Emulators were evaluated and rejected for the query leg: `fakesnow` emulates the *Python connector* (no JDBC surface, and Ontop speaks JDBC); LocalStack's Snowflake is paid. (This JDBC-based rejection is superseded by Option B below — the native executor uses the Python connector directly, so a connector-level emulator like `fakesnow` could stand in; we kept the env-gated live test regardless, to keep the "live Snowflake" claim honest.) **CI:** env-gated live test via repo secrets, skipping cleanly without them (the established pattern).

**Approach (Option B, native executor — landed 2026-07-22):** the Snowflake leg is a native **`SnowflakeExecutor`** (`src/cdf/adapters/snowflake.py`), wired per `kind` in `FederationService.from_env`: it compiles the E1 BGP + the same **r2g-generated R2RML** straight to Snowflake SQL over `snowflake-connector-python` — the same deterministic-SQL story as the Postgres leg, but without an Ontop/JDBC hop. `usage_metrics` moves out of the Postgres mapping so **`UsageMetric` routes uniquely to Snowflake** (concept ownership must be unambiguous for the planner). This **superseded the original plan of a second Ontop endpoint over the `snowflake-jdbc` driver** — a native executor sidesteps the JDBC/Arrow/Java-17 quirk entirely. Snowflake's **uppercase identifier folding** is still pre-empted: physical names land uppercase and CC-12's naming layer already maps them (`USAGE_METRICS` → `UsageMetric`, `QUERY_VOLUME_M` → `queryVolumeM`).

**Security (CC-7):** trial account still gets the floor — a read-only role, `STATEMENT_TIMEOUT_IN_SECONDS` on the warehouse, a resource monitor (CC-11), credentials only in the engine env. Key-pair auth (M1 FR-8) if time permits; password auth acceptable for the trial week.

Work breakdown + day plan: **P1 close-out plan, “Sprint 2.”** Repo-side requirements propagated to r2g (P12.7 pulled forward) and the M1 spec (FR-4).

**"Should we use Snowflake Cortex instead?"** — the customer FAQ now has a standing written answer: **ADR-0002**. Short form: default no (cost, determinism, single-point semantics, derived-vs-attested provenance); supported in principle as an *agentic connector* with an explicit contract (structured rows + the SQL Cortex generated + as-of, labeled `agent-attested`) when a customer mandates it. Out of scope for this sprint.

## 8. Referenced Repositories (for Claude Code context)

*Pull these into Claude Code when building so it has full context (per Arthur's request). Repo references pinned in v0.2 against Arthur's local `~/code` checkouts; the `arango-solutions` copies may lag his personal repos. One cleanup: `~/code/relational_schema_analyzer` (underscore) is an empty stale directory — delete it so nothing picks up the wrong path.*

- **`Contextual Data Fabric`** — the new project repo Arthur is creating (private for now). Home for the composable building blocks.
- **`customer-context`** (`arango-solutions/customer-context`) — the [[Customer360]] v3 pipeline: connectors/chunking, LangGraph extractor, span gate, person coref, embeddings (BM25 + vector), **AER** integration, survivorship, grounded/cited answer envelope + Vercel demo app. Basis for the unstructured side + demo harness (B4–B6). **✓ Cloned + verified (v0.2.1, `~/code/customer-context`)** — pipeline, grounding gate, envelope, and UI confirmed present; see the enhancement spec §1 for the verified inventory.
- **`r2g`** (relational-to-graph) — Arthur's; the **reference application** for relational schema → ontology/graph; **implements OSI**; composes the **relational-schema analyzer (RSA)** and **Arango-schema analyzer** pip libraries. RSA is the production-grade dependency and carries the production bar; r2g itself is a well-tested reference app (CI: ruff + mypy + large unit suite + Dockerized integration) but is not held to a production operational bar (single-node; no scale/HA). **B1–B3 depend on RSA (pinned) + named, tested r2g modules — not on r2g as a whole.**
- **Arango OntoExtract (AOE)** (`~/code/arango-ontoextract`) — Arthur's; LLM-driven ontology extraction + curation platform. Verified state (v0.2): unstructured extraction (6-agent LangGraph pipeline), **structured→ontology done** (SQL + ArangoDB schemas → OWL/SHACL), **belief revision built** (§6.16: touchpoint verdicts, Levi-identity revisions on the temporal substrate, Revisions Inbox, consolidation, 6 MCP tools), **time-travel done** (VCR timeline, point-in-time snapshots), SHACL constraints, JWT+RBAC, observability (structlog/Prometheus/OTel), MCP server (18 tools). **Multi-source alignment: not built** — the fabric's M3 gap. Basis for the Onto Extract layer.
- **Arango Entity Resolution (AER)** (`~/code/arango-entity-resolution`, PyPI `arango-entity-resolution` v3.5.1) — `CrossCollectionMatchingService` + `WCCClusteringService`; the cross-source ER engine used in `customer-context`. Verified state (v0.2): already ships **vector/ANN blocking** (sentence-transformers + `APPROX_NEAR_COSINE`), phonetic/n-gram matching, and **LLM match verification** for the 0.55–0.80 confidence band, plus an MCP server — semantic matching is largely done; the P2 work is the canonical-hub API + preserving the customer-context guards.
- **Relational-schema analyzer (RSA)** / **Arango-schema analyzer** — versioned pip libraries consumed by r2g and the ontology extractor. RSA is the **production-grade core** for structured→ontology; the fabric's structured building blocks (B1, M1/M2/M4) pin RSA's PyPI release + stable tool-contract bundle rather than depending on r2g internals.
- **`arango-sparql-py`** (`~/code/arango-sparql-py`, v0.1.0, ArthurKeen) — **SPARQL 1.1 → AQL transpiler** (rdflib + OWL/Turtle schema ontology; broad translation coverage, injection-safe, ~781 tests). Per ADR-0001: the **owned Arango leg** for the SPARQL IR. **The finishing work is DONE (2026-07-15):** WPs A2/A3/C1/C2 landed — evaluation correctness CI-gated (live-Arango + W3C suites), CSI→MappingBundle adapter, and the `translate_partition` federation entry (canonical keys, `seed_bindings` bind-join pushdown, `as_of`; contract: `arango-sparql-py/docs/architecture/proposals/federation-entry-point.md`).
- **`arango-cypher-py`** (`~/code/arango-cypher-py`, v0.2.0, ArthurKeen) — **openCypher → AQL transpiler** (TCK core ~90%) plus the stack's best asset: a **proven, IR-agnostic NL→conceptual-query engine** (few-shot, fuzzy entity resolution, EXPLAIN-grounded self-healing, eval harness at 93–100%). Per ADR-0001: reused to *generate* the SPARQL IR (seam swap, WP D1) and available as the P1 Arango-leg fallback; its eval harness seeds M10.
- **`arango-solutions-mcp-server`** (`~/code/arango-solutions-mcp-server`, package `arangodb-mcp-server` v2.0.0, Apache-2.0) — a 74-tool MCP server over ArangoDB: AQL execute/validate/explain with bundled AQL + cypher2aql + optimization manuals, graph traversals, **vector + hybrid (BM25+vector) search**, embeddings, pattern-memory tools, stream transactions, cluster/user/permission admin; stdio + bearer-token-authenticated HTTP/SSE; dockerized. Already in local use fronting the JLR contextual-memory-fabric database. **Role in the fabric: §10.2** — the database-level MCP layer (build/ops tooling + the host pattern for the fabric's own semantic MCP tools), deliberately *not* the agent-facing query path.

---

## 9. Open Decisions for the Team

1. **Phase-1 database:** Postgres (proposed — fastest proof) vs attempt Snowflake directly (higher value; the gate is the demo account, not connector code — §7.1 note). → §7.1
2. ~~**Structured→ontology readiness (★)**~~ — **RESOLVED (v0.2):** confirmed done in both AOE (owns SQL→OWL/SHACL) and RSA (introspection + tool-contract bundle). Remaining sub-decision: which path B1 demos with (proposed: RSA introspection bundle → AOE OWL/SHACL mapping, matching the documented split). → §7.6
3. **Query decomposition:** are we happy presenting LLM *and* deterministic as a composable choice, with deterministic as the long-term target? → §5.2
4. **Ontology scope:** confirm the seed CSM use cases (§4) as the Phase-1 ontology scope.
5. **Repo shape:** one repo with modules, or separate repos per building block (Onto Extract layer vs Query layer)? Arthur's call as gatekeeper.
6. **Naming:** confirm **"Contextual Data Fabric"** (also JLR's term; channel `#contextual-fabric`).
7. **Evaluation bar (new, v0.2):** confirm the golden-set approach (§10.1, module M10) and who authors expected answers for the seed questions.
8. **Agent interface (new, v0.2):** is **MCP** the fabric's agent-facing surface (§10.2)? Five constituent repos already ship MCP servers (AOE alone: 18 workspace tools + 6 belief-revision tools); deciding early shapes M5's contract and M9.
9. **Master-ontology store (new, v0.2):** adopt AOE's ArangoRDF-PGT + temporal-versioning store as the fabric's master-ontology home, or define a fabric-native representation? (§10.3)
10. **Relational leg: Ontop (buy) vs r2g P12.2 (build) (new, v0.3):** ADR-0001 #2 — Ontop is mature SPARQL→SQL-over-R2RML covering every relational source we use, but adds a Java VKG service; r2g P12.2 avoids infra but reinvents a solved problem. **Recommendation: Ontop for P2; P12.2 as the P1 stopgap (M5 plan B1-alt); r2g P12.1 (CSI v1 + R2RML) is the contract either way.** *(Note: the IR question itself — SPARQL vs Cypher — is resolved by ADR-0001's code-read: SPARQL, with `arango-cypher-py`'s NL engine harvested to generate it.)*

---

## 10. Cross-Cutting Requirements *(new in v0.2)*

Requirements that bind every module; each module spec references the ones that apply. Numbered CC-1…CC-12 with the phase they take effect.

### 10.1 Evaluation & correctness (CC-1, P1)
"Trust is structural" must be testable. **A golden set of seed questions with expected answers, expected sources touched, and expected citations** is a P1 deliverable alongside the demo (start with the §4 seed questions). Every planner change (LLM or deterministic) runs against it; regressions block. P2 extends it with decomposition-accuracy scoring (did the plan hit the right sources / join keys?) and adopts the LLM-as-judge patterns AOE already implements (faithfulness scoring, qualitative evaluation agent). Owned by module **M10 (Evaluation)** — see the architecture index.

### 10.2 Agent interface (CC-2, decide P1, ship P2)
The primary persona is an agent, so the fabric must define its programmatic surface, not just a demo UI. **Proposed: MCP, in two distinct layers:**

- **Semantic layer (the fabric's own MCP tools — the product):** `federate(question) -> cited envelope` plus introspection tools over the ontology/mappings (list concepts, show a concept's mappings, explain a plan). This is the surface agents consult; every answer goes through the ontology, the grounding gate, and (P3) OBAC. RSA, arango-schema-analyzer, AER, r2g, and AOE all already expose MCP servers, so this is consistent with every building block. **Build it on the `arango-solutions-mcp-server` stack** (FastMCP + its bearer-token auth middleware + Docker packaging) — either as a tool module in that server or as a sibling following its patterns; don't invent a second server framework.
- **Database layer (`arango-solutions-mcp-server` as-is — tooling, not product):** 74 tools of raw AQL/graph/vector access to the hub. Invaluable for **build-time and ops** (it is already how agents manipulate Arango during this project's development, and it fronts the JLR fabric locally). But raw AQL **bypasses the ontology and governance** — exposing it to customer agents would recreate the exact ungoverned-access problem the fabric exists to solve (§2.1) and undercut the demo story. It stays off the agent path in demos and production; M8's OBAC applies to the semantic layer, with database-level access held to admin credentials.

P1 may demo through the library call + UI; the MCP decision (§9.8) should land before P2 so M5's contract doesn't have to be retrofitted.

### 10.3 Ontology & mapping storage/versioning (CC-3, P1 decision)
The master ontology needs a defined physical home in Arango. **Proposed: AOE's existing store — ArangoRDF PGT (OWL semantics preserved) + the temporal versioning substrate** — rather than a fabric-native format (§9.9). The M4 **mapping artifact must be versioned too**: since "the mapping is the query," an unversioned mapping is an unversioned query. P1: mappings carry a version/hash cited in the envelope; P2: mapping versions align with ontology versions (same temporal pattern).

### 10.4 Consistency & as-of semantics (CC-4, P1 minimal)
A federated answer joins **live** relational facts with a **pre-ingested** unstructured graph — the legs have different freshness. P1 minimal: every citation carries an **as-of timestamp** (query execution time for live legs; last-ingest time for the Arango graph), surfaced in the envelope. P2+: staleness thresholds per source and a visible "unstructured side current as of …" annotation in the demo UI.

### 10.5 Partial-failure semantics (CC-5, P1)
Defined behavior when one federation leg fails (timeout, auth, source down) while others succeed: **default is a partial answer with the failed leg explicitly declared** (the "partially-grounded" badge), never silent omission; **refusal when the failed leg is load-bearing** for the question (no answerable claim survives without it). The retrieval path records the failure the same way it records a success. (M5 emits, M7 renders.)

### 10.6 Cost, latency & caching stance (CC-6, P2)
Cost/latency instrumentation is already FR'd (M5 FR-9, M7 FR-5); this adds the **caching stance**: metadata bundles and compiled query plans are cacheable (invalidated on schema change via RSA re-analysis / belief-management cascade); **query results are not cached in P1–P2** (correctness + "don't move the data" first). Instrumentation should reuse AOE's observability stack (structlog, Prometheus, OTel) rather than inventing one.

### 10.7 Security floor & credential architecture (CC-7, P1 floor / P2 hardened)
The fabric accesses multiple live systems; credentials are handled by one architecture, phased:

- **Identity model:** the fabric connects **as itself** — one least-privilege, **read-only** service identity per source. **No per-user credential passthrough**: agent/user-level authorization is M8's job (OBAC scopes what the *answer* contains; the fabric's connection identity stays its own). Passthrough/on-behalf-of is explicitly out of scope until M8.
- **Ownership & resolution:** credentials belong to **M1 (Connectors) only**. Every artifact that travels — CSI v1, R2RML, `mapping.yaml`, Ontop datasource config, citations/retrieval paths — references sources by **logical name** (`postgres-crm`, `snowflake-gold`); M1 resolves logical name → credential at `open()` time through a **SecretResolver seam**. *R2RML and JDBC-style configs can syntactically carry connection strings — they must never.* Mappings get versioned, cited, and shared; a credential embedded there leaks into git, envelopes, and the hub.
- **The hub stores mappings, not secrets** — nothing credential-shaped in ArangoDB collections, envelopes, or logs; token **redaction** on every read-back surface incl. MCP tools (reuse **r2g Phase 8's pattern**: encrypted provider-config registry, `$ENV_VAR` references resolved at use time, redaction on read, DSN-scrubbed errors).
- **P1 floor:** git-ignored `.env` loaded only by the M5 engine (the UI never sees a connection string — CC-8); read-only Postgres role; non-root Arango user; no raw-credential logging.
- **P2 hardening:** a real secret store (Vault or the host cloud's manager) behind the same SecretResolver seam — config change, not code change. Per-source auth done right from day one of each connector: **Snowflake key-pair auth** (passwords are being deprecated), **Databricks service principal + OAuth M2M**; rotation happens in the store, not in code. If Ontop is adopted (§9.10), its datasource config is **templated from the secret store at container start**, never baked into an image or repo.

r2g's Phase 9 lane discipline ("carry governance metadata, never launder sensitive data") applies from day one; its classification/entitlement machinery and suggested-RBAC/OPA emission become concrete inputs to M8 in P3.

### 10.8 Deployment topology (CC-8, P1)
The P1 demo environment is defined in `docs/architecture/deployment-p1.md`: **four live processes on one host** — Postgres + ArangoDB via a repo-owned `docker-compose.yml` (Postgres seeded from r2g's Chinook/Pagila samples), the M5 engine (FastAPI; holds all source credentials per CC-7), and the customer-context Next.js UI run locally — with the LLM API as the only external dependency. **AOE, RSA, and r2g are build-time tools**: they produce the ontology, mappings, and ingested graph *before* the demo and are not on the live path (fewer moving parts in front of the customer; everything live is inspectable AQL/SQL). Vercel hosting is a Phase-2 option, not a P1 assumption.

### 10.9 Dependency pinning & compatibility (CC-9, P1)
The fabric consumes its building blocks as **versioned artifacts, never floating**. Policy:
- **One pin table** — the compatibility matrix in the [architecture index](architecture/README.md) is the single source of truth for which version of each block the fabric currently builds against (RSA, `arangodb-schema-analyzer`, AER, the r2g Phase-12 module once it ships, AOE, customer-context @ commit).
- **Arthur bumps pins** (build gatekeeper); a pin bump is a PR like any other change.
- **A pin bump re-runs the M10 golden set** — that is the compatibility test. Red golden set = the bump doesn't merge. (P1: manual run; P2: CI gate per M10 FR-6.)
- Blocks not yet on PyPI (customer-context, AOE) pin by **git commit SHA** until they version.

### 10.10 Packaging & licensing (CC-10, deferred to P2 — deliberately)
"Independently publishable blocks" is a North Star principle but **not P1 work**; recorded here so deferral is a decision, not an omission:
- **License alignment:** r2g, RSA, `arangodb-schema-analyzer`, and AER are all **Apache-2.0**. **AOE has no LICENSE file** (verified v0.2.1) — an action item before anything imports it as a dependency or it's pitched as a publishable block. Fabric-owned packages default to Apache-2.0 to match the ecosystem unless the team decides otherwise.
- **Naming:** fabric-owned packages under one prefix (proposal: `arango-fabric-*`, e.g. `arango-fabric-query`, `arango-fabric-mappings`) — decide with the repo-shape question (§9.5).
- **Release process:** semver + changelog + PyPI, following the RSA precedent (it is the model: extracted core, versioned, contract-documented). P2 packages the first block; P3 the set.

### 10.11 Resource guardrails & admission control (CC-11, P1 floor / P2 full)
Federated architectures fail three ways: large cross-source joins, large result sets processed on the federator, and chatty multi-round-trip plans. Optimizers mitigate but cannot cover everything (cross-boundary cardinality estimates are unreliable; unstructured/vector legs have no statistics; the LLM planner can emit pathological decompositions; agentic legs are opaque) — so the fabric enforces budgets and **treats an unfiltered federated pull as bulk data movement by stealth**: a violation of principle 1, not a slow query.

- **Statistics-driven planning first:** the analyzers already emit **collection/row counts, FK cardinality hints (1:1 vs 1:N), and per-field value distributions** (`sample_field_value_counts`) — the planner uses them for join ordering, bind-join direction (small side ships keys), predicate-selectivity estimates, and evidence-based budgets. These statistics must **survive the CSI/mapping pipeline** to reach the planner (M4), and are refreshed by incremental re-analysis (schema-analyzers RE-4). Sample *values* entering LLM prompts pass the analyzers' redaction options + r2g Phase-9 classification gates.
- **Plan-time admission (P1 floor / P2 full):** no naked scans — every leg carries a selective binding derived from the question's concepts; per-leg row/byte budgets with mandatory LIMITs; engine-side joins are **bind/semi-joins on canonical keys** with a bounded key-set size (beyond it: push the join down, switch to the assembled pattern, or refuse); a round-trip budget (max legs, max sequential depth — kills N+1 plans structurally); pre-flight `EXPLAIN` (Postgres/AQL/Ontop) as confirmation with a cost ceiling.
- **Run-time enforcement (P1 floor):** per-leg timeouts + row caps at the cursor; overall query deadline + federator memory budget, no disk spill; per-source circuit breaker (reuse AOE's pattern); defense in depth at the source — the CC-7 read-only role also carries `statement_timeout`/memory caps; Snowflake resource monitors in P2.
- **Trip semantics (CC-5 extended from failure to exhaustion):** a capped/truncated leg is **declared in the retrieval path** ("leg capped at N rows — partial"), never silent; genuinely-large analytics degrade to the **assembled pattern** (M5 FR-8: deliberate, bounded, acknowledged) or the query is **refused with the reason and the alternative** ("requires joining ~2M rows across sources — run as an assembled job?"). A federation that knows its limits and says so is the trust story — and, with FR-9's cost instrumentation as the feedback loop, the direct answer to the cost/latency objection: every plan inspectable **and budgeted**.

### 10.12 Ontology naming convention (CC-12, P1 — decided 2026-07-18)
Every derived conceptual model follows the **W3C-community OWL naming style**: **classes singular PascalCase** (`Employee`, `MortgageTransaction` — never `employees`), **properties lowerCamel, singular unless inherently plural** (`socialSecurityNumber`, `hasPart` — never `social_security_number`, `HAS_NAME`). Conceptual queries are read by people; `?p a c:Employee ; c:socialSecurityNumber ?ssn` is glance-friendly and interoperable with the OWL ecosystem.

- **Enforced at the contract, not by convention:** the rule is **normative in the CSI v1 contract** and checked by `validate_csi` (the hub every producer emits into — one rule covers CDF, AOE, and both analyzers uniformly). Producer defaults: RSA and `arangodb-schema-analyzer` normalize entity + property names at baseline; r2g's `export-csi`/`export-r2rml` apply its existing Phase-5f `apply_naming_convention` (which already defaults to PascalCase/camelCase); AOE instructs the convention in extraction prompts **and** validates output with the same validator.
- **Only the conceptual layer renames.** `physicalMapping` (and R2RML logical tables/columns) keep the raw physical names — `Account.accountId → accounts.account_id` is precisely what the conceptual↔physical split exists for. Transpilers and Ontop are unaffected; only queries see the new names.
- **Singularization is assisted, not blind:** RSA's recorded concern ("English singularization is unreliable") is handled with r2g's `singularize` + a per-source override map, and the M3 "confirm ~2%" curation step is the human backstop for the `courses→course`-class mistakes.
- **Timing:** adopted **before pinning** (CC-9) while generated artifacts have one consumer — every CSI/R2RML/question/golden regenerates; deferring would make this a breaking change to accreted customer mappings.

**Typing provenance (added 2026-09-08, from PR review — PJ).** The CSI must
record *how each source's types are known*, per class/property/relationship:
**declared** (relational DDL; a graph collection with JSON-schema validation
enabled; a curator's declared-references overlay), or **inferred** (sampled
from data — the schema-free graph default). Extraction checks for enforcement
where the engine offers it (ArangoDB collection `schema` properties) and
records declared typing when present; graph databases split into
types-declared and types-inferred families and the fabric must never present
an inferred type with declared confidence. Scorecard claims about "typed
relationships" cite the provenance class they rest on.

### 10.13 Access control & identity (CC-13, M8/P3 — later phase)
The user asking a question must be entitled to the data each leg returns; a query may be **partly answerable** (some legs permitted, others withheld). Today the engine connects to every source as one read-only service identity (CC-7) and `POST /federate` is **anonymous** — no per-user authorization — and the M5 single-leg **FILTER/OPTIONAL pushdown** (E1) runs under the *service* role, so source row-level security is **not** per-user and projected columns are unmasked. This CC commits the phased design; the full research, citations, and open decisions live in [access-control-research.md](architecture/access-control-research.md). It **extends CC-7** (which stops at "no passthrough until M8") and realizes **M8 (Governance/OBAC)** + product-PRD §4.1 ("the steward and the asker").

- **Phase A — delegated identity (baseline).** Authenticate the user at the edge (OIDC) and **propagate identity to each source** (OAuth 2.0 Token Exchange / RFC 8693; DB `SET ROLE`; a per-source service-account-vs-passthrough choice, as data-virtualization products like Denodo expose) so source-native RLS/masking enforces for the *real* user. This is the highest-leverage step: it makes the E1 predicate pushdown **leak-safe** (masking/RLS fires *before* the pushed predicate) and removes the **confused-deputy** exposure of a broad service account. The NL/LLM front-end acts strictly with the user's authority (OWASP LLM06, "excessive agency"), never a shared identity.
- **Phase B — planner pre-flight authorization + disclosure.** Per-leg **allow / rewrite (inject the source's row filter, drop a masked column) / deny** before dispatch (XACML PEP/PDP shape); authorized **partial results** with a coarse "N sources withheld" disclosure; prefer **silent row/column filtering** over per-row denial (the existence-leak / 404-vs-403 rule). Envelope changes are **additive**: a distinct `refused: insufficient entitlement` status reason (vs "ungrounded") and a `withheld_sources` field; every access decision is **cited in the retrieval path** (M8 FR-2). Citations pass the same policy as bindings — a datum the user cannot read is **redacted** — and the E2 bind-join `VALUES … IN (…)` key-set is scoped to what the user may see.
- **Phase C — central ReBAC decision, delegated enforcement.** One policy engine makes the semantic allow/deny + query-shaping decision; **ReBAC (OpenFGA / Zanzibar) is the recommended default** because the ontology is already a relationship graph — "which concepts/objects can this user see" becomes a reverse index the planner (M4) consumes for shaping — while identity **still** propagates to sources (defense in depth). Reuse r2g Phase 9's classification/OPA emission + AOE's JWT+RBAC as load-time inputs.
- **Phase D — ontology markings + purpose controls (Palantir-style end-state).** Once data is governed under the ontology: attach markings/classification to concepts/properties and **propagate them into the merged, cited answer** so each cited datum inherits its source's restrictions; add purpose-based (need-to-know) scoping. **Keep source least-privilege controls live beneath** so the ontology layer can never be a bypass — note the raw-AQL MCP path bypasses the ontology and must stay admin-only. This is the north-star "governance lives in the ontology."

**Cross-cutting invariants:** the user's identity reaches enforcement (no service-account-only path); citations are governed like data; scope/filter over hard-deny to avoid existence leaks; source-native controls stay in force beneath any ontology policy. **Decision record:** **ADR-0004** — identity planes & OBAC enforcement points (leg vs reassembly vs both; ReBAC vs ABAC; tenancy; citation redaction; refusal classes).

---

## 11. Process & Ownership

Composable-blueprint build: identify sub-modules → PR per sub-module → reconcile with the super-module → iterate (catches requirements drift). **Arthur is the build gatekeeper**; others review, test, and contribute per module. Standups + on-demand check-ins for decisions. This PRD is the shared contract Arthur refactors against — comment inline / via PR.

*Sources: [[2026-07-13 the design partner Customer Context Roadmap]], [[the design partner Feedback Summary]], [[2026-07-10 - C360 the design partner Demo]], [[2026-07-09 - C360 field-feedback review]], [[2026-07-10 - the design partner Feedback Brainstorm]], [[C360 Example Questions]].*
