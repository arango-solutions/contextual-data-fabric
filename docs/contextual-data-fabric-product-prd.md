---
title: "Contextual Data Fabric — Product PRD (demo → state of the art)"
type:
  - internal
  - prd
date: 2026-07-24
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric-north-star]]"
status: draft
version: 0.1
---

# Contextual Data Fabric — Product PRD: from demo to state of the art

> **Purpose.** The [P1 PRD](contextual-data-fabric-prd.md) got us to a proven demo. This
> document is the next contract: the multi-phase path from that demo to a product that
> stands beside Palantir Foundry and Denodo — open, composable, graph-native, and
> agent-first. It answers the architecture questions now on the table (§3/§4), extends
> the module set (§5), and phases the work with exit gates (§6).
>
> **Parent:** the [North Star](contextual-data-fabric-north-star.md) is unchanged. Every
> phase here ladders to it; when scope is ambiguous, check against it.
> **Status:** Draft v0.1 for team review. Decisions marked ⚖ need an ADR before code.

---

## 1. Where we stand (the honest baseline, 2026-07-24)

**Proven, live, gated (P1 + Sprint 2):**

- **Three-source federation** on one conceptual vocabulary: CRM→Postgres (Ontop/R2RML),
  usage telemetry→Snowflake (native `SnowflakeExecutor`, ADR-0002), documents→ArangoDB
  (`arango-sparql-py`), joined on `account_id` via bind-join pushdown (`VALUES→IN`).
- **A fourth engine capability**: native ClickHouse leg — proof the executor pattern
  reaches engines Ontop cannot dialect for.
- **Grounded envelope** (M7): per-claim citations carrying the actual SQL/AQL, source
  objects, as-of stamps; refusal when uncitable; declared partial failure (CC-5).
- **Auto-derived mappings**: r2g emits CSI v1 + R2RML from live schemas; CC-12 OWL naming
  end to end; concept ownership is single-source (the planner's invariant).
- **Golden gate** (M10): 5/5 live cases incl. the three-source g5; `make gate` is the
  mandatory pre-demo step; fixture twins cover CI without live stacks.
- **NL→SPARQL**: prepared-question registry (deterministic demo path) + an LLM front-end
  behind a seam; PJ's eval workstream has a **live execution-graded baseline (6/49 on a
  deliberately hard corpus)** and dense few-shot retrieval in `arango-query-core`.
- **Cost instrumented once**: the entire Snowflake sprint burned ~0.57 credits — the first
  real datum for the cost story (CC-6/B7).

**Known limits (what "demo" still means):**

- E1 accepts only single-source BGPs — **no FILTER (beyond exact literals), OPTIONAL,
  UNION, aggregation, ORDER/LIMIT pushdown**. Analytics questions can't push down.
- The planner is **ownership routing, not optimization**: fixed leg order, sequential
  execution, no statistics, no strategy choice beyond one bind-join pattern.
- **No access control**: one admin credential per source; no user/agent entitlements.
- Metadata lives in **files** (`deploy/csi/*.json`, R2RML in `deploy/`), not a queryable
  catalog; statistics, capabilities, and indexes aren't captured at all.
- Alignment (M3) is deferred: sources were engineered to share one vocabulary. The real
  "two ontologies walk into a bar" problem is untouched — as planned, but unsolved.
- Single node, no HA, secrets in `.env`, demo UI only.

The distance from here to state-of-the-art is **exactly the set of questions this PRD
answers**. None of them are speculative; each is the productization of a seam that
already exists.

---

## 2. Positioning (why these bets, against whom)

- **Palantir Foundry** is the closest philosophical competitor — ontology-centric,
  governance-in-the-ontology. It is closed, monolithic, and services-heavy. Our wedge:
  **open building blocks, federation-first (don't move the data), a graph-native hub,
  and structural grounding** (Foundry asserts trust; we cite or refuse).
- **Denodo / Starburst (Trino) / dbt semantic layer** federate or standardize SQL but have
  **no ontology, no entity resolution, no grounding, no agent surface**. They answer
  "query many databases"; we answer "give every agent one governed brain."
- **GraphRAG products** handle unstructured only; we federate it *with* the systems of
  record and cite across the boundary.
- The **agent-first surface (MCP + grounded envelope)** is the differentiator none of the
  above have. Cost/latency discipline (CC-6) is the credibility gate for all of it.

---

## 3. Answers at a glance

Every question on the table, its decision, and where it lands. Details in §4.

| # | Question | Decision (short form) | Phase |
|---|----------|----------------------|-------|
| Q1 | User access control vs admin schema-sampling | **Two identity planes** + OBAC at the semantic layer; citations are access-checked too | P3 |
| Q2 | r2g/AOE/RSA/ASA overlap — common metadata DB? | **Yes: the Fabric Catalog, in the hub itself (M11).** Analyzers become feeders under one contract; files become an export format | P3 |
| Q3 | GraphQL? | **Yes, as a compiled skin over the conceptual IR** — never a second semantic engine | P6 |
| Q4 | Federated query optimization? | **Yes — cost-based, statistics-driven (M12)**; prerequisite is E1 expressiveness (pushdown of filters/aggregates) | P3→P4 |
| Q5 | Identifying / semi-identifying properties for sophisticated joins? | **Join-intelligence registry** (profiling + sketch-based overlap) + AER crosswalk; join **confidence enters the envelope** | P4 |
| Q6 | Use specialized store capabilities (FTS, fuzzy, vector)? | **Capability registry + capability-aware routing**; conceptual predicates compile per-source or refuse with reason | P4 |
| Q7 | Leverage index knowledge? | **Yes — cheap and high-value**: cost input, bind-join gating, index advisor | P4 |
| Q8 | Agentic control: federate vs virtualize vs ETL/CDC, re-allocated on the fly? | **The delivery-mode continuum (M13)** + an allocation controller with an autonomy ladder (advise → gated → autonomous) | P5 |
| Q9 | How to build graph virtualization? | **"Materialize the skeleton"**: topology (keys+edges) into the hub, properties federated on demand; the bespoke in-memory CSR engine is the research track behind it | P5 |
| Q10 | Embeddings from different models across stores? | **Never compare across spaces (structural rule).** Catalog the spaces; embed the query per space; **fuse ranks (RRF), not distances**; one canonical hub space for ER | P5 |

---

## 4. The architecture decisions

### 4.1 Q1 — Two identity planes: the steward and the asker  ⚖ ADR

The tension is real: building the fabric requires **privileged introspection** (schemas,
index inventories, *sampled values* for profiling and join discovery) while using it must
be **least-privilege per user/agent**. These are different jobs and must be different
identities:

- **Build plane (steward).** Per-source *metadata* service identities used by M1/M2
  connectors and the analyzers: schema + statistics + sampling rights, short-lived
  credentials from the secret store, every sampling call audited, and **all sampled
  values passing the redaction gate** before they can reach an LLM prompt or the catalog
  (the D1 schema card already obeys this — generalize it). Sampling artifacts stored in
  the catalog are **profiles and sketches, not rows** (§4.5).
- **Query plane (asker).** Users/agents authenticate to the fabric, never to sources.
  Entitlements are defined **on the ontology** (M8, the Palantir pattern): concept-,
  property-, and row-scope rules, defined once, compiled into every leg (WHERE clauses /
  AQL filters appended by the planner) and enforced again at reassembly.
- **Citations are data.** The envelope must pass the same policy: a user who cannot read
  a source object cannot receive it as a citation. This creates a new refusal class —
  *"refused: insufficient entitlement"* — distinct from *"refused: ungrounded"*, and both
  are first-class, testable outcomes (M10 gets authorization goldens).
- **PII propagation** is the demo-able win: flag `email` as PII once on the ontology →
  masked in every source's legs, samples, and citations at once (North Star §"winning").

### 4.2 Q2 — The Fabric Catalog: one metadata brain (new module M11)  ⚖ ADR

Today the fabric's self-knowledge is scattered: CSI JSON files, R2RML files, r2g's
`~/.r2g/catalog.json` snapshots, AOE's ontology store, AER's crosswalks, nothing at all
for statistics/capabilities/indexes. The overlap between r2g and AOE (both do
structured→ontology) and between RSA/ASA (introspection) is a symptom: **there is no
single place where "what does the fabric know about its sources" lives.**

**Decision: the catalog is the hub.** ArangoDB already holds the master ontology and the
canonical entities; the catalog is the same database, formalized — it *is* the
metadata-graph model the North Star cites (PubMed/NIH: metadata in the graph, data at the
sources). One graph, temporal-versioned on AOE's substrate:

```
Source ──has──▶ PhysicalSchema (versioned snapshots) ──indexed-by──▶ Index
  │                    │
  │has                 │profiled-as──▶ ColumnProfile (stats, sketches, id-class)
  ▶ Capability         │
Concept ──maps-to──▶ Mapping (CSI/R2RML) ──targets──▶ PhysicalObject
  │──owned-by──▶ Source          │──delivery-mode──▶ {federate|virtualize|materialize}
  │──joins-via──▶ JoinKey (confidence, method)
EmbeddingSpace (model, dim, metric, revision) ──covers──▶ PhysicalObject
Entitlement ──governs──▶ Concept/Property
```

- **The analyzers become feeders, not owners.** RSA/ASA/r2g/AOE keep their jobs
  (introspect, extract, emit) but write to the catalog through one contract; the
  role split stays as documented (RSA = read-only physical introspection; AOE owns
  SQL→OWL/SHACL semantics; r2g composes). CSI/R2RML files become **exports** of catalog
  state for CI and Ontop, not the source of truth. `from_env` reads the catalog.
- **Buy-vs-build honesty:** DataHub/OpenMetadata are discovery catalogs — passive
  inventories for humans. Ours is **operational**: the planner reads it on every query
  (ownership, capabilities, statistics, entitlements). That is not what they build.
  Adopt **OpenLineage export** for interop with enterprise catalogs instead of adopting
  their stores. ⚖ ADR should record this comparison.
- This is the **enabling module for six of the ten questions** (Q4 statistics, Q5 join
  registry, Q6 capabilities, Q7 indexes, Q8 delivery modes, Q10 embedding spaces live in
  it). It goes first.

### 4.3 Q3 — GraphQL: yes, as a skin; never a second engine  ⚖ ADR

The conceptual layer is a typed schema — classes, properties, relationships. That is a
GraphQL schema waiting to be generated:

- **Auto-generate the GraphQL SDL from the master ontology** (concept → type, property →
  field, relationship → nested field). Entitlements shape the *visible* schema per caller.
- **Compile whole GraphQL documents to the same conceptual graph-pattern IR** the SPARQL
  path uses — one planner, one grounding path, one gate. Never resolver-per-field
  (the N+1 trap); a GraphQL query is a graph pattern and compiles like one.
- Read-only (the fabric is not a write path); subscriptions become interesting only after
  CDC (P5) exists to power them.
- Priority: **P6**, after the engine is worth multiplying. The agent surface (MCP) and NL
  remain the primary doors; GraphQL is the human-developer door. All three are skins on
  one IR — this "one IR, many dialects" rule is the architectural invariant to protect.

### 4.4 Q4 — Federated query optimization (new module M12)

Naive federation is the difference between a demo and a deployment. Two stages:

**Stage 1 (P3) — expressiveness, the prerequisite.** An optimizer can't help queries the
IR can't express. Extend E1 → E1.5: FILTER pushdown (comparisons, ranges, IN), OPTIONAL
(left join across legs), ORDER/LIMIT pushdown, and **aggregate pushdown** (`GROUP BY` to
Snowflake/ClickHouse — this *is* the "assembled analytics pattern" the P1 PRD promised
Phase 2, and it's what makes the warehouse legs earn their keep). Every extension keeps
the injection-safe, deterministic compilation property.

**Stage 2 (P4) — the cost-based federated planner:**

- **Statistics in the catalog** (from onboarding profiling + scheduled refresh): row
  counts, NDV, min/max, histograms for hot columns, plus observed per-leg latencies from
  envelope telemetry (we already record them — close the loop).
- **Join order & strategy per edge:** bind-join (exists) vs fetch-and-hash-join in the
  fabric vs **Bloom-filter semi-join** (ship a compact filter of join keys to the second
  source — orders of magnitude less transfer on wide joins) vs broadcast of small legs.
- **Parallel leg execution** (legs without data dependencies run concurrently; today all
  legs are sequential) and **adaptive re-planning** (a leg returning 100× the estimate
  triggers strategy change mid-query).
- **Semantic cache**: cache leg results keyed on (sub-query, as-of, entitlement scope) —
  the envelope's as-of semantics make staleness honest by construction.
- Guardrails stay primary (CC-11): the optimizer works *inside* per-leg budgets, and its
  estimates feed better refusals ("this query would scan 400M unindexed rows — refusing;
  here's the index that would fix it," see Q7).

### 4.5 Q5 — Join intelligence: identifying & semi-identifying properties

The locked P1 join was deterministic `account_id` — engineered. Real fabrics don't get
that gift. The upgrade path:

- **Profile for identifier-ness at onboarding** (build plane, §4.1): uniqueness ratio,
  null ratio, format classifiers (email/UUID/E.164/postal), name heuristics. Classify
  every column: **identifying** (unique key), **semi-identifying** (quasi-identifier:
  email, name+company, phone), **descriptive**.
- **Cross-source overlap without moving data:** compute **MinHash/HLL sketches** per
  candidate column at the source; ship sketches (not values) to the hub; estimate
  pairwise value overlap to *discover* join edges nobody declared. Sketches are also the
  privacy-preserving answer to "the admin can analyze, the user can't see" (§4.1).
- **The JoinKey registry** (catalog): every discovered/declared join edge between
  sources, with method (exact / composite / fuzzy-AER) and confidence. The planner reads
  it; the steward curates it (accept/reject, the alignment-review pattern).
- **Fuzzy joins ride AER** (M6's existing ladder: vector blocking + phonetic/n-gram +
  LLM verification band): resolve once, **materialize the crosswalk as canonical-entity
  edges in the hub**, and join legs through the crosswalk at query time — probabilistic
  resolution happens *offline*, query time stays deterministic.
- **Join confidence enters the envelope** ⚖: a claim joined via a 0.87-confidence fuzzy
  match is a different trust class than an exact-key join — exactly parallel to
  ADR-0002's derived-vs-attested distinction. The strict gate can require exact joins;
  concierge mode declares fuzzy ones.

### 4.6 Q6 — Capability-aware federation

Stores are not interchangeable row-servers, and a lowest-common-denominator federation
wastes what customers pay for:

- **Capability registry** (catalog, per source): full-text search (ArangoSearch/BM25,
  Postgres tsvector, ClickHouse token filters, Snowflake SEARCH), fuzzy matching
  (pg_trgm, Arango NGRAM), **vector search** (APPROX_NEAR_COSINE, pgvector, Snowflake
  VECTOR, ClickHouse ANN), geo, aggregation dialect coverage, pushdown limits. Declared
  from a static engine matrix, then **probe-verified at onboarding** (a capability the
  probe can't confirm doesn't exist — the mock-fidelity lesson applied to databases).
- **Conceptual predicates** extend the IR: `text:matches`, `fuzzy:similar`,
  `vector:near`, `geo:within`. The planner compiles each to the owning source's native
  form **if the capability exists**, else: route to the hub's copy (if that concept is
  virtualized/materialized), or **refuse with the reason and the remedy** — never
  silently degrade semantics.
- This is what makes hybrid questions first-class: "accounts whose tickets *sound
  frustrated* AND whose usage is rising" = BM25/vector leg in Arango + SQL leg in
  Snowflake, fused at reassembly — each store doing what it is best at.

### 4.7 Q7 — Index awareness

Cheapest item on the list; the analyzers already read index inventories. Store them in
the catalog and use them three ways: (1) **cost-model input** — access-path costing for
M12; (2) **strategy gating** — bind-join only into indexed columns, else hash-join in the
fabric; (3) an **index advisor** — the fabric observes which predicates and join keys are
hot across the federation and *recommends* indexes to source owners ("`account_id` in
`support.tickets` is bind-joined 400×/day, unindexed"). The fabric knows something no
single-source DBA can see: the cross-source workload. That's an insight product, not
just an optimization.

### 4.8 Q8 — The delivery-mode continuum and the allocation controller (new module M13)  ⚖ ADR

Federate vs virtualize vs ETL is not three architectures — it's **one dial: how much of
a concept you materialize into the hub**:

```
federate ──────────── virtualize ─────────────── materialize
(nothing lives        (keys + topology live      (rows live in hub,
 in the hub;           in hub; properties         CDC keeps them
 freshest, slowest)    hydrate on demand)         fresh; fastest)
```

- **One mapping drives all three.** The CSI/R2RML mapping already tells us how a concept
  realizes in its source — the *same artifact* is the federation compiler input, the
  virtualization fetch spec, and the ETL/CDC pipeline spec. This is the elegant core of
  M13: no second mapping language, ever.
- **CDC for the materialized end:** Postgres logical decoding (Debezium-class), Snowflake
  Streams/Tasks, upserts into the hub keyed on canonical ids; a **freshness watermark**
  per concept in the catalog.
- **Honesty is non-negotiable** ⚖: the envelope declares the serving mode and watermark
  per leg (`served-from: hub-materialized, as-of watermark T`). Materialization must
  never silently change trust semantics — and the golden gate grows *equivalence
  goldens*: the same question answered in all three modes must agree within watermark.
- **The allocation controller** decides the dial position per concept from observed
  workload (envelope telemetry: frequency, latencies, leg cost) × declared policy
  (freshness SLO, latency SLO, source-load and **cost budgets** — Snowflake credits are a
  first-class input, we've already metered them). Hot+stable → materialize; hot+volatile
  → virtualize; cold → federate.
- **Autonomy is a ladder, not a leap:** L1 *advisor* (recommends, human applies — this is
  P5's target), L2 *gated* (applies after approval, belief-revision-style change record),
  L3 *autonomous within guardrails* (bounded by budget/SLO policy; research). An LLM may
  *propose* allocation changes with reasoning; the applied change is always a governed,
  audited, reversible catalog transaction. "Agentic" describes the advisor, not the
  enforcement path — same split as ADR-0002.

### 4.9 Q9 — Graph virtualization: materialize the skeleton first  ⚖ ADR

Your instinct (in-memory sparse compressed topology + filter pushdown) is the right
*end state*; the question is sequencing. Three options, honestly compared:

| Option | What it is | Verdict |
|---|---|---|
| **A. Topology-in-hub** | Materialize only join keys + relationship edges into ArangoDB collections (satellite-style); properties hydrate on demand via per-source pushdown, batched by key | **Do this first (P5).** Reuses Arango's traversal engine, transactions, existing AQL leg, and the CDC machinery; multi-hop questions work immediately; it is literally the "virtualize" dial position of §4.8 |
| **B. In-process CSR cache** | Bespoke in-memory compressed sparse row structure (dictionary-encoded keys, roaring bitmaps, Arrow columns) inside the engine; pushdown hydration | **Research track.** Fastest possible traversals, but it re-implements a graph engine in Python (GIL says no — this is a Rust component), needs its own invalidation, and duplicates what the hub already does. Spike it behind a benchmark exit gate: only graduates if it beats Option A ≥5× on the target traversal suite |
| **C. Optimizer + cache only** | No topology copy; rely on M12 + semantic cache | Baseline — falls out of P4 for free; insufficient for deep multi-hop |

The reframe worth internalizing: **virtualization is not a separate subsystem — it is a
dial position on M13's continuum** (keys+edges materialized, properties federated). That
keeps one mental model, one mapping, one honesty rule.

### 4.10 Q10 — Embedding interoperability  ⚖ ADR

Embeddings from different models are **mutually meaningless** — a pgvector column from
model A and an Arango vector from model B cannot be compared, ever. The design turns that
hard truth into structure:

- **Catalog every embedding space**: model id + revision, dimensions, metric,
  normalization, corpus scope (the `EmbeddingSpace` node, §4.2). A vector column without
  declared space metadata is unusable by the planner — by rule.
- **The structural rule** ⚖: *cross-space distance comparison is refused*, exactly like
  an uncited claim. This is the same "trust is structural" muscle.
- **Query-time pattern**: for a `vector:near` predicate spanning sources, embed the
  *query text* once per target space (each store's own model), run native vector search
  in each store, and **fuse rankings at reassembly (Reciprocal Rank Fusion)** — rank
  fusion is legitimate where distance comparison is not, because each store's ranking is
  internally consistent.
- **One canonical hub space** (the fabric's own model — AER already runs
  sentence-transformers) for entity resolution, alignment, and anything materialized
  into the hub; optionally re-embed content into the canonical space on materialization
  (M13 makes this a pipeline step).
- **Research track**: cross-space alignment (linear/Procrustes maps learned from anchor
  pairs) — promising, unproven; behind an experiment flag with M10 measurement, never on
  the default path.

## 5. Module set extension

M1–M10 stand. The product adds four, honoring "composable, not monolithic":

| # | Module | Responsibility | Builds on |
|---|--------|----------------|-----------|
| **M11** | **Fabric Catalog** | The metadata graph in the hub: sources, schemas (versioned), mappings, statistics/profiles/sketches, capabilities, indexes, join keys, embedding spaces, entitlements, delivery modes + watermarks. One write contract for all feeders; OpenLineage export | RSA, ASA, r2g, AOE, hub ArangoDB |
| **M12** | **Federated Optimizer** | Statistics + cost model, join order/strategy (bind/hash/Bloom-semi-join/broadcast), parallel legs, adaptive re-planning, semantic cache, capability-aware routing, index advisor | M5, M11 |
| **M13** | **Delivery-Mode Controller** | The federate↔virtualize↔materialize dial: CDC pipelines, topology materialization, watermark bookkeeping, workload-driven allocation (autonomy ladder L1→L3) | M4/M11, hub, CDC connectors |
| **M14** | **Developer Surfaces** | GraphQL skin (SDL generated from ontology, compiled to the IR — an engine skin, stays here) + the fabric's **consumable UI contracts**: catalog manifest, ontology-map payload, answer envelope, `/federate`, gate status. The admin console itself (onboarding wizard, catalog browser, controller review queue) is **delegated to ArGOS** (`~/code/argos` PRD) as fabric tabs — integration #3 after its FR-10 AOE + r2g | M5, M8, M11, ArGOS |

Embedding interop is deliberately **not** a module — it's a discipline spread across
M11 (space registry), M5/M12 (fusion), M6 (canonical space), M13 (re-embed on
materialize), governed by one ADR.

**M14 composition rules (re-scoped 2026-08-31).** (1) **No second ontology UI**:
concept curation, alignment review, and belief revision stay in AOE's
object-centric workspace; fabric surfaces deep-link into it. (2) **No fabric-local
console**: the overarching UI is **ArGOS** — the portfolio's context/governance/
provenance plane — whose context contract opens each tool already bound to the
right project, ontology, and sources. CDF's job is to be excellently consumable:
the demo page (M9) stays self-contained and standalone, and every console need
ships as an ArGOS tab against CDF's contracts. Two cross-PRD seams are tracked in
ArGOS §9: **Q-10** (one source registry — its FR-3 vs our ADR-0003 manifest/M11;
decide derivation direction before its M1) and **Q-11** (one ontology-relative
policy vocabulary — its FR-5 vs our ADR-0004/M8; PDP-shared, PEP-distinct, with
the fabric's data-plane decisions never blocking on a portfolio-plane call).

---

## 6. The phases

Sized in themes, not dates; each phase ends in a **gate** (M10 grows with each). The P1
PRD's Phase 2 (Snowflake, cost note) is delivered; its Phase 3 intent (governance) is
absorbed into P3 below. Cut lines are ordered within each phase.

### P3 — Trusted Foundation *(the fabric becomes a system, not a demo)*

| WP | Work | Answers |
|----|------|---------|
| P3.1 | **M11 Catalog v1**: schema + write contract; analyzers feed it; CSI/R2RML become exports; `from_env` reads catalog | Q2 |
| P3.2 | **Identity split**: build/query planes, secret store (Vault-class) behind the SecretResolver seam, Snowflake key-pair auth (M1 FR-8), audited sampling + redaction gate | Q1 |
| P3.3 | **OBAC v1** (M8): concept/property entitlements on the ontology, compiled into legs, **citation-side enforcement**, the authorization refusal class, PII-flag propagation demo | Q1 |
| P3.4 | **E1 → E1.5 expressiveness**: FILTER/IN/range pushdown, ORDER/LIMIT, aggregate pushdown v1 (the assembled analytics pattern) | Q4-prereq |
| P3.5 | Gate expansion: authorization goldens, aggregate goldens, catalog-integrity checks (zero concept overlap becomes a catalog constraint, not a script) | — |
| P3.6 | **Catalog browser v0 — read-only, ~free**: the catalog *is* a graph in the hub, so browse it with the ArangoDB **Graph Visualizer** — a "Fabric Catalog" viewpoint + theme + saved queries (sources → schemas → concepts → mappings → ownership; "who owns concept X", "sources missing statistics"). Installer already exists (the `arangodb-visualizer-customizer` skill). Stewards stop debugging the catalog via raw AQL | Q2 |

**Exit gate:** a new source onboards **through the catalog** with no hand-edited files;
an analytics question (`GROUP BY` on Snowflake) answers via pushdown; flagging a property
PII masks it in answers *and* citations across all sources; a steward can **browse the
catalog graph** (sources, concepts, ownership) in the Graph Visualizer viewpoint;
`make gate` green including the new classes.

### P4 — Intelligent Federation *(the engine earns the word "optimizer")*

| WP | Work | Answers |
|----|------|---------|
| P4.1 | **Profiling + statistics** into the catalog (onboarding + refresh); envelope-telemetry loop closed | Q4 |
| P4.2 | **M12 planner**: cost model, join order, strategies (incl. Bloom semi-join), parallel legs, adaptive re-plan | Q4 |
| P4.3 | **Capability registry + probes**; conceptual predicates (`text:`, `fuzzy:`, `vector:`) compiled per capability; hybrid-question demo | Q6 |
| P4.4 | **Join intelligence**: identifier classification, MinHash/HLL sketch overlap discovery, JoinKey registry, AER crosswalk materialization, **join-confidence in the envelope** | Q5 |
| P4.5 | **Index awareness + advisor** | Q7 |
| P4.6 | **NL quality program**: PJ's execution-graded eval is the metric; dense retrieval, schema-card iteration, self-healing loop; public target on the hard corpus | (NL) |
| P4.7 | Semantic cache v1 (as-of + entitlement-scoped) | Q4 |

**Exit gate:** a defined M10 *performance* golden suite shows measured speedups (target:
≥10× on the worst P3 federated join); a cross-source **fuzzy** join answers with declared
confidence; a hybrid text+SQL question routes per capability; NL beats the published
target on PJ's corpus; the advisor produces a real index recommendation.

### P5 — The Living Fabric *(the dial, the controller, the embeddings)*

| WP | Work | Answers |
|----|------|---------|
| P5.1 | **CDC ingestion** for two engines (Postgres logical decoding, Snowflake Streams) with watermarks in the catalog | Q8 |
| P5.2 | **Topology virtualization** (Option A): keys+edges into hub, on-demand property hydration, invalidation via CDC | Q9 |
| P5.3 | **Mode-aware envelope**: served-from + watermark per leg; equivalence goldens (same answer, all three modes) | Q8/Q9 |
| P5.4 | **Allocation controller L1→L2**: workload observation, recommendations with reasoning, gated application as audited catalog transactions — **including the approval surface**: L2 cannot ship without one; an interim CLI/PR-style flow is acceptable here, with the polished review queue landing as an ArGOS tab (see M14) | Q8 |
| P5.5 | **Embedding interop**: space registry enforcement, per-space query embedding + RRF fusion, canonical hub space wired to M6, re-embed-on-materialize | Q10 |
| P5.6 | Research spikes (flagged, gate-bounded): CSR/Rust virtualization engine vs Option A benchmark; cross-space alignment | Q9/Q10 |

**Exit gate:** the same golden answers correctly in all three modes within watermark; the
controller demonstrably migrates a hot concept federate→materialize under synthetic load
*and back* when load subsides — with every change audited; a cross-store vector question
fuses two different embedding models' results with the rule enforced.

### P6 — Product Surfaces & Scale *(other people can run it)*

| WP | Work | Answers |
|----|------|---------|
| P6.1 | **GraphQL skin** (M14): generated SDL, IR compilation, entitlement-shaped schemas | Q3 |
| P6.2 | **ArGOS integration** (was: fabric-local admin console): adopt the ArGOS context contract + SDK, expose/harden the M14 contracts, and ship the fabric tabs there — onboarding wizard (the `add-source-*` skills, productized), catalog browser (graduating the P3.6 viewpoint), controller review queue (graduating P5.4's interim flow), gate dashboard | — |
| P6.3 | **MCP semantic layer GA** (PRD §10.2): `federate()` + introspection tools, multi-tenant OBAC depth (harvest arango-cypher-py's tenant-AST work) | Q1 |
| P6.4 | **Scale + HA**: stateless engine horizontal scaling, hub as Arango cluster, Helm/packaging, versioned APIs, OTel end-to-end (AOE's observability pattern) | — |
| P6.5 | **FinOps**: per-question cost attribution (per-leg credits/compute — the S8 measurement, systematized), budgets, showback dashboard | — |

**Exit gate:** a team that isn't us onboards a source and answers a governed question
without our help; p95 latency and cost-per-question published against SLOs; the demo SLO
survives a leg failure (declared partial, not flaky).

---

## 7. New cross-cutting requirements (extending PRD §10, CC-1…CC-12)

- **CC-13 — Catalog is the source of truth.** No metadata consumed at query time may
  live only in a file or a tool's private store. Files are exports.
- **CC-14 — Capabilities are probe-verified.** A declared capability that fails its
  onboarding probe does not exist to the planner.
- **CC-15 — Joins carry confidence.** Every cross-source join in an envelope declares
  method + confidence; the strict gate may require exact.
- **CC-16 — Serving mode is declared.** Every leg's answer declares
  federate/virtualize/materialize + watermark. Silent staleness is a defect.
- **CC-17 — Embedding spaces are isolated.** Cross-space distance comparison is
  structurally refused; only rank fusion crosses spaces.
- **CC-18 — Cost is attributed.** Every question can report what it cost, per leg.
- **CC-19 — Controller changes are governed.** Allocation changes are audited,
  reversible catalog transactions with a declared reason — at every autonomy level.

---

## 8. Decisions needing ADRs (write before building)

1. **ADR-0003 — The Fabric Catalog** (§4.2): schema, write contract, hub-resident vs
   external, OpenLineage interop, file-export policy.
2. **ADR-0004 — Identity planes & OBAC enforcement points** (§4.1): where entitlements
   compile (leg vs reassembly vs both), citation redaction, refusal classes.
3. **ADR-0005 — Delivery-mode continuum & controller autonomy ladder** (§4.8): mode
   semantics, watermark contract, L1→L3 criteria.
4. **ADR-0006 — Virtualization Option A vs B** (§4.9): the benchmark exit gate that
   would graduate the CSR/Rust engine.
5. **ADR-0007 — Embedding-space policy** (§4.10): registry schema, fusion methods,
   canonical-space model choice + revision pinning.
6. **ADR-0008 — GraphQL-as-skin** (§4.3): SDL generation rules, IR compilation, what is
   deliberately unsupported (mutations, arbitrary resolvers).

---

## 9. Risks

1. **Catalog scope creep** — M11 could swallow a year. Mitigation: v1 is exactly the six
   node types the planner reads; everything else waits for a consumer.
2. **Optimizer before expressiveness** — building M12 against BGP-only E1 optimizes
   nothing. P3.4 is deliberately sequenced first.
3. **Controller trust** — an autonomous mover of data is scary; hence the ladder (L1
   default), CC-19, and equivalence goldens before any autonomy.
4. **Two-ontology alignment remains the hard research problem** (M3). This PRD keeps it
   deferred but honest: P4's join-discovery sketches are the first real alignment signal
   on real data; a dedicated alignment phase should follow P5 informed by it.
5. **Team surface area** — four new modules across the same owned repos. The catalog
   contract (P3.1) is what keeps r2g/AOE/RSA/ASA from re-fragmenting; land it first and
   enforce via CC-13.
6. **Rust/CSR spike opportunity cost** — bounded by the ADR-0006 exit gate; it dies
   unless it beats the boring answer 5×.

---

*Next actions: team review of §3 decisions → write ADR-0003/0004 (the P3 gate-openers) →
size P3 WPs against owner availability. The golden gate discipline (PJ's rule) extends
unchanged: every phase exit is a `make gate` class, or it didn't happen.*
