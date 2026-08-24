# Federated Aggregation, Text Search, and Partial-Result Reuse — M12 Addendum

Status: research addendum to `docs/research/federated-query-optimization.md` (the M12 survey).
Date: 2026-08-23. Scope: aggregation decomposition, full-text predicates in federation, and
view-based / algebraic result reuse ("the Algebraix question"). Joins, statistics, adaptivity,
and the per-leg cache's freshness key design live in the main survey (Q3–Q5) and are only
cross-referenced. Evidence tags as in the main survey: `[peer-reviewed]`, `[docs, mechanism]`,
`[measured]`, `[reported, unverified]`.

---

## Executive summary

1. **Aggregation.** Theory (Yan & Larson 1995) and every production engine agree on the same
   two-phase shape — partial aggregation near the data, final at the coordinator — but *within
   one engine*. Across federation seams the strongest production precedent is negative:
   BigQuery **refuses aggregation pushdown to federated Cloud SQL sources entirely**. Only one
   peer-reviewed system (CoDA, ESWC 2015) does cross-endpoint SPARQL aggregation; FedX and
   Comunica do not. Right posture: an admission ladder — single-leg pushdown first,
   distributive two-phase over declared-unique join keys second, holistic aggregates refused.
2. **Text search.** Production federation routes text predicates by *capability declaration*:
   Trino's `apply*` SPI (decline via `Optional.empty()`), postgres_fdw shippability +
   `extensions` allow-list. Every mature SPARQL store exposes FTS as a **magic property** that
   stays inside the standard grammar (Jena `text:query`, Virtuoso `bif:contains`, Stardog
   `textMatch`). Cross-engine relevance-*score* fusion has no principled basis; rank-based
   fusion (RRF, SIGIR 2009) is the only defensible combiner — concierge mode only.
3. **Reuse.** Algebraix's "data algebra" is a proprietary instantiation of open-literature
   pillars that predate it — answering-queries-using-views (Halevy 2001), semantic caching
   (Dar et al. 1996), view matching (Goldstein & Larson 2001). Its core patent family
   (priority 2006-05-15) hit its 20-year anticipated expiry in **May 2026**; longest adjusted
   term found runs to **November 2026** — a diligence footnote, not a blocker, and the
   underlying techniques are unencumbered. For a storage-less hub: exact canonical-key leg
   caching (designed in Q5) is the win; a narrow "containment-lite" tier (seed-set
   subsumption) is worth building; full view matching and IVM are not.

---

## Topic 1 — Federated aggregation decomposition

### 1.1 Theory: eager/lazy aggregation (Yan & Larson, VLDB 1995)

Yan & Larson formalized moving GROUP BY past joins in both directions: **eager aggregation**
pushes a *partial* group-by below a join (the original group-by remains above, now over fewer
rows); **lazy aggregation** is the inverse pull-up `[peer-reviewed]`
(https://www.vldb.org/conf/1995/P345.PDF). The correctness conditions under bag semantics that
matter for a federation:

- The pushed-down (partial) grouping key must be *widened to include the join columns* of the
  leg it is pushed into, so the upper join and final aggregation still see the groups they need.
- **Duplicate-sensitive** aggregates (SUM, COUNT, AVG-via-SUM/COUNT) are only correct through a
  join if join fan-out is compensated — either the other side is duplicate-free on the join key
  (a declared unique/primary key) or a propagated COUNT is used to scale partials. MIN/MAX are
  **duplicate-insensitive** and push through joins freely.
- AVG never decomposes as AVG-of-AVGs; it decomposes as (SUM, COUNT) partials.

### 1.2 How production engines split partial → final

- **Trino/Presto** plans `Aggregation[PARTIAL] → exchange → Aggregation[FINAL]`; multiple
  DISTINCT aggregates rewrite via **MarkDistinct** (`mark-distinct-strategy`
  NONE/ALWAYS/AUTOMATIC) and an `OptimizeMixedDistinctAggregations` rewrite that preserves
  partial aggregation `[docs, mechanism]`
  (https://trino.io/docs/current/admin/properties-optimizer.html,
  https://www.querifylabs.com/blog/distinct-aggregation-optimization-in-apache-calcite-and-trino).
  Its `PushPartialAggregationThroughJoin` rule is restricted (no computed grouping-key
  expressions — trino #24731): even intra-engine aggregate-through-join is treated gingerly.
- **Spark** plans two `HashAggregate` operators around a shuffle (partial, then final/merge);
  DISTINCT is handled by an Expand-based multi-stage rewrite; AQE re-optimizes stage boundaries
  from runtime statistics `[docs, mechanism]`
  (https://books.japila.pl/spark-sql-internals/physical-operators/HashAggregateExec/,
  https://dataninjago.com/2022/01/04/spark-deep-dive-12-aggregation-strategy/).
- **DataFusion** exposes the split explicitly as `AggregateMode::Partial` /
  `AggregateMode::Final`, with `Accumulator::state()` serializing partial state and
  `merge_batch()` combining it `[docs, mechanism]`
  (https://datafusion.apache.org/user-guide/explain-usage.html,
  https://docs.rs/datafusion/latest/datafusion/logical_expr/trait.Accumulator.html).
- **BigQuery federated queries** are the cautionary precedent for *cross-boundary* aggregation:
  for Cloud SQL / external sources, "only column pruning and filter pushdowns are supported.
  Specifically, compute, join, limit, order by and aggregation pushdowns aren't supported"
  `[docs]` (https://docs.cloud.google.com/bigquery/docs/federated-queries-intro). Google ships
  aggregation *pull-up* (do it all at the coordinator), not decomposition, across the federation
  seam.

Reading: partial/final aggregation is universal *inside* an engine that owns both phases and
the exchange. Across a federation seam the shipped state must be a portable value (a SUM, a
COUNT), not engine-internal accumulator state — hence the decomposition table below.

### 1.3 Approximate COUNT(DISTINCT): HLL sketches across partials

- HyperLogLog sketches are **mergeable**: union of sketches = sketch of the union, so per-leg
  sketches can be combined without rescanning `[docs, mechanism]`.
- Accuracy evidence: Presto's `approx_distinct` has a default max standard error of **2.3%**
  (configurable 0.40625%–26%) `[docs, measured]`
  (https://prestodb.io/docs/current/functions/hyperloglog.html); Facebook reports the same
  ~2.3% observed above cardinality 256, exact below `[measured]`
  (https://engineering.fb.com/2018/12/13/data-infrastructure/hyperloglog/). BigQuery's HLL++
  functions land in the ~1–2% error band at default precision `[docs]`.
- **The federation catch: sketch wire formats are engine-proprietary.** A Presto HLL, a
  BigQuery HLL++ sketch, and a Snowflake HLL state are not interchangeable; cross-engine merge
  requires a common library (Apache DataSketches publishes binary-compatible sketches across
  Java/C++/Python — https://datasketches.apache.org/ `[docs]`) deployed *at every source*,
  which we do not control. So HLL in our federation is only usable per-leg (compute the final
  approximate count inside one source), not as cross-source partial state.
- **Exact alternative** (the one that works across heterogeneous sources): two-phase distinct —
  each leg runs `SELECT DISTINCT group_keys, x` (a partial GROUP BY that collapses duplicates),
  ships distinct *values*, and the coordinator counts after the join. Cost is bounded by the
  number of distinct values, which the per-leg budget already meters. This is the portable
  equivalent of Spark's Expand rewrite.

### 1.4 Algebraic vs holistic; HAVING and AVG rules

Gray et al.'s data-cube classification `[peer-reviewed]`
(https://www.cs.cmu.edu/~natassa/courses/15-721/papers/cube_op.pdf):

- **Distributive** (partials of the same type merge directly): COUNT, SUM, MIN, MAX.
- **Algebraic** (bounded-size tuple of distributive partials): AVG = (SUM, COUNT); variance/
  stddev via (SUM, SUM², COUNT).
- **Holistic** (no constant-bound partial state): MEDIAN, percentiles, MODE, RANK.

**HAVING** is a post-aggregation filter: it always evaluates at the **final** stage. The only
pushdown is the classical rewrite when the predicate references grouping keys only — then it
is a WHERE and joins the normal filter-pushdown path.

**Holistic aggregates in practice:** no engine decomposes them exactly with bounded state.
Engines either (a) ship all values to one node (exact, expensive), or (b) substitute a
mergeable quantile sketch — t-digest (Presto `approx_percentile`, unbounded worst-case error
but strong empirical tails) or KLL (formal additive rank-error guarantee, <1.65% at k=200)
`[peer-reviewed / docs]`
(https://datasketches.apache.org/docs/QuantilesStudies/KllSketchVsTDigest.html,
https://www.sciencedirect.com/science/article/pii/S2665963820300403). Per-server percentiles
must never be averaged — only sketch merges are correct. Same portability catch as HLL applies.

### 1.5 Federated SPARQL aggregation — who actually does it

- **CoDA** (Ibragimov, Hose, Pedersen, Zimányi — "Processing Aggregate Queries in a Federation
  of SPARQL Endpoints", ESWC 2015) is, per its own claim, the first system to optimize SPARQL
  aggregate queries across endpoint federations: a cost-based optimizer choosing among
  strategies that push partial grouping/aggregation into SPARQL 1.1 endpoints
  `[peer-reviewed]` (https://link.springer.com/chapter/10.1007/978-3-319-18818-8_17). Research
  prototype; no production lineage found.
- **FedX** (and its GraphDB embedding) is SPARQL 1.0-era join optimization; no cross-source
  aggregation decomposition `[docs]`
  (https://graphdb.ontotext.com/documentation/11.4/fedx-federation.html).
- **Comunica 3.x** groups operations that apply *exclusively to one source* and ships them in
  bulk — i.e. exactly the "single-leg pushdown" rung; it does not split one aggregation across
  sources `[docs]` (https://comunica.dev/blog/2024-03-19-release_3_0/).

Conclusion: cross-source SPARQL aggregation decomposition is a research topic, not a shipped
capability anywhere; single-source pushdown is the industry floor *and* ceiling.

### 1.6 Decomposition rule table (per SPARQL 1.1 aggregate)

| Aggregate | Class | Per-leg partial | Final (in-engine) | Safe through join fan-out? | Federation notes |
|---|---|---|---|---|---|
| COUNT(*) / COUNT(x) | distributive | COUNT per (group ∪ join keys) | SUM of partials | Only if other legs unique on join key (else scale by counts) | Portable partial (an integer) |
| SUM | distributive | SUM | SUM | Same condition as COUNT | Portable; watch numeric overflow/precision |
| MIN / MAX | distributive | MIN / MAX | MIN / MAX | **Yes** (duplicate-insensitive) | Safest push-down |
| AVG | algebraic | (SUM, COUNT) pair | SUM(sums)/SUM(counts) | Same condition as SUM | Never average averages |
| COUNT(DISTINCT x) | distinct-sensitive | DISTINCT (group, x) values | COUNT at coordinator | Yes (dedup is idempotent) | Exact but value-volume-bounded; HLL only single-leg (formats not portable) |
| SAMPLE | distributive | any one value | any one partial | Yes | Trivial |
| GROUP_CONCAT | holistic-ish (order/sep-sensitive) | — | — | — | **Refuse cross-source**; single-leg only |
| MEDIAN / percentile (non-std) | holistic | full values or sketch | merge | No bounded exact state | Single-leg exact, or declared-approximate sketch (concierge) |
| HAVING | filter | only if references grouping keys only (→ WHERE) | evaluate after final agg | n/a | Always final-stage otherwise |

### 1.7 Recommended admission ladder for M12

- **Rung 0 (today):** refuse by name — keep for GROUP_CONCAT-across-sources, percentiles,
  and anything not on the table above.
- **Rung 1 — single-leg pushdown (build first):** admit an aggregation query when the *entire*
  BGP + GROUP BY resolves to one source that supports it: Ontop/Postgres (full SPARQL 1.1
  endpoint) and ArangoDB (full aggregation upstream in the transpiler) today; Snowflake and
  ClickHouse once their BGP→SQL compilers emit GROUP BY. The engine only relabels and stamps
  the envelope. This is Comunica's exclusive-group behavior and is more than BigQuery gives
  its federated sources.
- **Rung 2 — two-phase over declared-unique join keys:** admit aggregate-over-join when
  (a) all aggregates are distributive/algebraic per the table, (b) every measure expression is
  single-leg, (c) grouping keys are widened with join keys on the measure leg (eager
  aggregation), and (d) every *other* leg is declared unique on its join key in the catalog
  (the RSA joinKeys / shared-key work provides exactly this uniqueness metadata) — which makes
  duplicate-sensitivity moot without count-scaling. COUNT(DISTINCT) rides this rung via exact
  distinct-value shipment under the per-leg budget.
- **Rung 3 (concierge only, later):** sketch-based approximations (per-leg HLL/percentile with
  the engine's native function), surfaced with a declared `approximate: {method, error_bound}`
  field in the envelope — never in cite-or-refuse mode.
- **Do not build:** general partial-aggregation-through-join with COUNT-scaling (the full
  Yan–Larson machinery). Trino restricts it even intra-engine with full statistics; across
  engines with r2g-mapped schemas the correctness surface (bag semantics + mapping-induced
  duplicates) is too large for the payoff at 2–6 legs.

---

## Topic 2 — Text / full-text search in federation

### 2.1 How federating engines route text predicates

- **Trino → Elasticsearch/OpenSearch:** full-text is *not* compiled from SQL LIKE; it is an
  explicit escape hatch — the `raw_query` **table function** takes Elastic Query DSL verbatim
  and pushes the whole query down (the older query-in-table-name syntax is deprecated)
  `[docs, mechanism]` (https://trino.io/docs/current/connector/elasticsearch.html). Result
  order from the source is explicitly not preserved by the engine. Lesson: production engines
  treat FTS as an opaque, source-dialect predicate routed whole to one source — not as a
  decomposable relational operator.
- **postgres_fdw:** an expression ships to the remote only if it is "safe" — built-in
  operators/functions (plus immutable functions from extensions allow-listed via the server's
  `extensions` option), no mutable functions `[docs, mechanism]`
  (https://www.postgresql.org/docs/current/postgres-fdw.html). Consequence for tsvector: the
  `@@` match operator is built-in and shippable, but one-argument `to_tsvector(text)` is only
  STABLE (it reads `default_text_search_config`) and thus not shipped — the two-argument
  `to_tsvector('english', col)` form is IMMUTABLE and shippable; the Postgres FTS docs
  recommend the two-argument form for exactly this class of reason `[docs]`
  (https://www.postgresql.org/docs/current/functions-textsearch.html). Lesson: *analyzer
  identity must be pinned explicitly* or pushdown silently degrades.
- **BigQuery federated sources:** filter pushdown only, and only for scalar literal types —
  no text-search pushdown story at all `[docs]`
  (https://docs.cloud.google.com/bigquery/docs/federated-queries-intro).

### 2.2 Capability negotiation precedents

- **Trino SPI:** the optimizer *offers* each operation to the connector —
  `applyFilter`, `applyAggregation`, `applyLimit`, `applyTableFunction`, … — and the connector
  accepts by returning a rewritten handle or declines with `Optional.empty()`
  `[docs, mechanism]` (https://trino.io/docs/current/develop/connectors.html). Capability is
  discovered per-call, not declared statically; connectors must decline honestly or the
  optimizer loops.
- **Calcite adapters:** capability is encoded as *convention + converter rules* — an adapter
  registers rules that can translate relational operators into its calling convention; whatever
  no rule matches stays in the Enumerable (in-engine) convention `[peer-reviewed]`
  (https://arxiv.org/pdf/1802.10233). Same shape: capability = "I have a translation for this
  operator", with a guaranteed in-engine fallback.
- Our engine's equivalent seam already exists: refusal-by-name at admission. The registry
  below turns "refuse aggregation/FTS by name" into "consult the capability row, then admit,
  degrade, or refuse with remedy".

### 2.3 Cross-source relevance fusion — what is defensible

- **Raw score fusion is unprincipled.** BM25/tsrank/Elastic `_score` values are functions of
  corpus statistics (IDF, length norms) and engine parameters; scores from different engines
  over different corpora are not on a common scale. The classical metasearch combiners
  CombSUM/CombMNZ (Fox & Shaw, TREC-2 1994) assume comparably normalized scores and were
  evaluated on runs over the *same* corpus.
- **Rank fusion is the principled fallback.** Reciprocal Rank Fusion — score each doc
  Σ 1/(k + rank) — beat Condorcet-fuse, CombMNZ, and individual learning-to-rank methods on
  LETOR 3 (Cormack, Clarke & Büttcher, SIGIR 2009) `[peer-reviewed, measured]`
  (https://dl.acm.org/doi/10.1145/1571941.1572114), and has since become the default hybrid
  combiner in OpenSearch, Elasticsearch, Azure AI Search, and Weaviate `[docs]`. RRF uses only
  ranks, so it is the one combiner that survives heterogeneous engines.
- Verdict for the fabric: single-source ranked results are citable as-is (score is a
  source-native fact with provenance). Cross-source *merged rankings* are inherently a
  judgment call → concierge mode only, with the envelope declaring `fusion: rrf(k=60)` and
  per-leg native ranks preserved in citations. Never present fused scores as measurements.

### 2.4 SPARQL FTS surfaces — syntax precedents that stay in-grammar

All major stores expose FTS through the *magic property / property function* pattern — a
reserved predicate IRI inside a plain triple pattern, so the standard grammar is untouched:

- **Jena:** `(?s ?score) text:query ("keyword" 10)` — subject list can bind score/literal;
  Lucene syntax in the string `[docs]` (https://jena.apache.org/documentation/query/text-query.html).
- **Virtuoso:** `?o bif:contains "keyword"` as a filter-ish predicate over text-indexed
  objects `[docs]` (https://docs.openlinksw.com/virtuoso/sparqlextensions/).
- **Stardog:** `?l <tag:stardog:api:property:textMatch> "query"` with optional score/limit
  arguments `[docs]` (https://docs.stardog.com/query-stardog/full-text-search).
- **GraphDB:** Lucene/Solr/Elastic *connectors* materialize search as ordinary triple patterns
  against a connector-managed index `[docs]`.
- The W3C sparql-dev inventory catalogs these as the established extension idiom `[docs]`
  (https://github.com/w3c/sparql-dev/wiki/Inventory-of-existing-extensions-to-SPARQL-1.1).

### 2.5 What our four source kinds can actually do

| Source | Native FTS | Ranking | Route |
|---|---|---|---|
| ArangoDB | ArangoSearch views + analyzers | BM25/TFIDF | Transpiler → `SEARCH` + `BM25()` — best first target |
| ClickHouse | `text` index (beta; GA-recommended from 26.2) with `hasToken/hasAllTokens/hasAnyTokens`; analyzer fixed at index build `[docs]` (https://clickhouse.com/docs/engines/table-engines/mergetree-family/textindexes) | boolean (no BM25) | BGP→SQL emits token functions; containment only, no scores |
| Snowflake | `SEARCH()` function; FULL_TEXT search optimization on columns; **analyzer in the query must match the analyzer in the index** or the access path is skipped `[docs]` (https://docs.snowflake.com/en/user-guide/querying-with-search-functions) | boolean | BGP→SQL emits `SEARCH(col, 'terms')`; containment only |
| PostgreSQL via Ontop | tsvector exists in PG, but Ontop exposes standard SPARQL 1.1 only; no documented FTS magic predicate in Ontop [reported, unverified — none found in Ontop docs] | — | Treat as `fts: unsupported` until an Ontop lens/REGEX fallback is validated |

### 2.6 Recommended design

1. **Conceptual predicate:** one magic property in our namespace, e.g.
   `?ent cdf:matchesText ("query string" ?score)` (Jena-style arg list; score binding optional
   and only bound where the source ranks). Single-leg by construction — like OPTIONAL today,
   it must resolve entirely within one source or be refused. This keeps standard-grammar
   parsing (it is just a triple pattern) per the store precedents above.
2. **Capability-registry fields** (per source × concept-property):
   `fts.supported: bool`; `fts.dialect: pg_tsquery | snowflake_search | clickhouse_tokens |
   arangosearch`; `fts.indexed_properties: [property IRI → index/view id]`;
   `fts.analyzer: identifier` (pin it — the postgres_fdw STABLE-function lesson and
   Snowflake's analyzer-match rule both show silent degradation when analyzers drift);
   `fts.scoring: none | bm25 | tfidf`; `fts.limits: {max_terms, max_results}`. Admission
   consults the registry exactly the way Trino's optimizer consults `applyFilter`.
3. **No index → refuse with remedy, degrade only in concierge.** Cite-or-refuse mode: if the
   property row says unsupported/unindexed, refuse by name with the remedy ("enable
   FULL_TEXT search optimization on X / create ArangoSearch view Y"). Concierge mode may
   degrade to an unranked containment scan (`LIKE`/`ILIKE`, Arango `CONTAINS`) with the
   envelope declaring `degraded: unindexed_scan` and the leg still under its cost budget —
   this makes explicit what Trino does silently when pushdown is declined (full scan).
4. **Ranking:** single-leg ranked results pass scores through as source-native, provenance-
   stamped fields. Multi-leg ranked fusion = RRF, concierge only, fusion method declared.

---

## Topic 3 — Partial-result reuse / data-algebra caching (the Algebraix question)

> **Provenance statement.** Everything in this section derives exclusively from
> **public sources**: Gary Sherman's published book (*The Algebra of Data*),
> the published patent record, and the independent academic/open-source
> literature cited per claim below. No non-public Algebraix Data material was
> used, referenced, or available in preparing it, and the fabric's reuse design
> (the L0–L3 ladder in §3.5) is specified against the open-literature
> techniques — answering-queries-using-views, semantic caching, view matching —
> which predate or are independent of the Algebraix patent family.

### 3.1 Open-literature foundations (all predate Algebraix's 2006 priority date or are independent)

- **Answering queries using views** — Halevy's survey (VLDB Journal 10(4), 2001) organizes the
  whole space: given materialized views, find rewritings of a new query that use them; covers
  soundness (equivalent vs. contained rewritings) and cost `[peer-reviewed]`
  (https://link.springer.com/article/10.1007/s007780100054).
- **Semantic caching** — Dar, Franklin, Jónsson, Srivastava, Tan (VLDB 1996): the client keeps
  a *semantic description* of cached data; a new query splits into a cache-answerable part and
  a **remainder query** sent to the server `[peer-reviewed]`
  (https://courses.cs.duke.edu//spring02/cps296.1/papers/DFJST-VLDB1996.pdf). This is exactly
  the shape of reusing a cached leg for a superset seed-set and fetching only the difference.
- **View matching in production optimizers** — Goldstein & Larson (SIGMOD 2001): the
  fast/scalable view-matching algorithm shipped in SQL Server, with filter-tree indexing of
  views `[peer-reviewed]` (https://dl.acm.org/doi/10.1145/375663.375706). **Apache Calcite's
  `MaterializedViewRule` implements this paper** with extensions (aggregate rollup, union
  rewriting, constraint-driven rewrites) `[docs, mechanism]`
  (https://calcite.apache.org/docs/materialized_views.html). Trino has an open issue to adopt
  the same (trino #20850); Snowflake/BigQuery MVs do automatic rewrite behind the same theory.
- **Query containment** — deciding whether one conjunctive query's answers are always a subset
  of another's is NP-complete in query size (Chandra & Merlin, STOC 1977), but our legs are
  tiny (a handful of triple patterns), and the *restricted* checks worth doing (identical
  canonical BGP + seed-set subset + filter-range implication) are linear — the classical
  observation that CQ containment is practical exactly when queries are small.

### 3.2 SPARQL-specific results

- **BGP/monotone SPARQL canonicalization is solved and shipped as research software:** Salas &
  Hogan's QCan canonicalizes monotone SPARQL (BGPs + AND/UNION/projection) — blank-node
  canonical labeling + graph leaning; worst case doubly-exponential, but efficient on 43.6M
  real queries with only a handful of pathological cases `[peer-reviewed, measured]`
  (https://aidanhogan.com/qcan/extended.pdf, https://aidanhogan.com/docs/qcan_sparql_demo.pdf;
  RDF-graph side: https://aidanhogan.com/docs/rdf-canonicalisation.pdf). Our conceptual BGPs
  are small, mostly blank-node-free, and already normalized by the planner — canonical keys
  are cheap for us.
- **SPARQL caching literature:** Papailiou, Tsoumakos, Karras & Koziris, "Graph-Aware,
  Workload-Adaptive SPARQL Query Caching" (SIGMOD 2015) caches subgraph-pattern results and
  adapts to workload `[peer-reviewed]` (https://dl.acm.org/doi/10.1145/2723372.2723714);
  earlier intermediate-result caching at WWW 2011 (https://dl.acm.org/doi/10.1145/1963192.1963273).
  Research systems; the durable idea is caching at *pattern* granularity, which matches our
  per-leg design.

### 3.3 Incremental view maintenance — the maintenance side

- **DBSP** (Budiu et al., VLDB 2023 best paper; commercialized as **Feldera**) gives general
  algebraic IVM for rich SQL including recursion `[peer-reviewed]`
  (https://docs.feldera.com/vldb23.pdf); **Materialize** productionizes differential dataflow;
  **DRed** (delete/re-derive) is the classical Datalog deletion algorithm with modern variants.
- All of these *maintain state the maintainer owns* against a change feed. Our hub owns no
  storage and sees no change feeds; freshness is contracted via as-of stamps and TTL/probe
  (survey Q5). IVM would re-introduce the storage and CDC obligations the architecture
  deliberately avoids — revisit only if the fabric ever hosts its own materialized rollups.

### 3.4 Algebraix specifics — what is public, and the IP diligence flags

**Public documentation.** Gary Sherman (principal mathematician, 2008–2014) built a
ZF-set-theoretic "data algebra" for Algebraix Data Corp; the accessible write-ups are the book
*The Algebra of Data* (Sherman & Bloor, 2015) and trade press `[reported, unverified — no
peer-reviewed evaluation or reproducible benchmark of their SPARQL store was found]`. The
mechanism as described: every query becomes a canonical algebraic expression; the store
accumulates computed sub-expressions and their algebraic relations; new queries are answered
by substituting *provably equivalent or contained* stored expressions instead of recomputing —
i.e., AQUV + semantic caching over a canonical algebra, applied to an RDF/SPARQL workload.

**Patent trail (diligence flags, not legal advice).** Lineage: XSPRADA Corp → Algebraix Data
Corp → Permission.io → **Algebraix LLC** (current assignee). Key family, all sharing priority
**2006-05-15** `[docs — Google Patents legal-status fields]`:

- US7613734 ("providing data sets using a store of algebraic relations") — granted 2009;
  adjusted expiration **2026-11-19** (https://patents.google.com/patent/US7613734).
- US8032509 ("data storage and retrieval using algebraic relations composed from query
  language statements") — granted 2011; anticipated expiration **2026-05-15**, i.e. already
  passed (https://patents.google.com/patent/US8032509B2/en).
- Same-family: US7720806, US7769754, US7865503, US7877370, US8380695
  (https://patents.justia.com/assignee/algebraix-data-corporation).
- Litigation: Algebraix LLC asserted six of these against IBM (watsonx); case **settled**
  (https://www.patsnap.com/resources/blog/litigation/algebraix-llc-v-ibm-data-management-patent-dispute-ends-in-settlement-patsnap-eureka/).

Net: the 2006-priority family is at/past its 20-year term as of this writing (longest adjusted
date found: Nov 2026). More importantly, the open-literature techniques we would actually
implement (Dar 1996, Halevy 2001, Goldstein–Larson 2001, Calcite's implementation) predate the
family or are independent public art. Flag for counsel only if we ever ship a feature marketed
as "algebraic query substitution over a stored expression algebra"; nothing below requires it.

### 3.5 Freshness and entitlement interaction — precedents

- **Snowflake's result cache is the production template for policy-aware reuse:** a hit
  requires an *exact* statement match, unchanged underlying data/micro-partitions, no
  non-reusable functions, unchanged configuration, and — critically — "the role accessing the
  cached results has the required privileges" (re-verified at hit time) `[docs, mechanism]`
  (https://docs.snowflake.com/en/user-guide/querying-persisted-results). Row-access-policy
  changes invalidate cached results; policies are evaluated inside the query so differently
  entitled users produce different statements/results
  (https://docs.snowflake.com/en/user-guide/security-row-intro).
- Our survey-Q5 key already encodes the two lessons: **as-of in the key** (a hit can never be
  stale for time-travel-pinned legs) and **entitlement scope in the key** (a result computed
  under one user's row policy can never serve another — a cross-user hit requires the leg to
  be declared entitlement-invariant in the catalog, the analog of Snowflake's privilege
  re-check). Reuse levels below inherit this key unchanged; containment-based reuse must
  additionally require *identical* as-of and entitlement dimensions — containment reasoning
  applies only to the seed/filter dimension, never to freshness or policy dimensions.

### 3.6 Applicability verdict — ranked by impact/effort

| Level | Technique | Impact | Effort | Exploits which existing seam |
|---|---|---|---|---|
| **L0** | Exact-match canonical-expression cache per leg (Snowflake-style: exact key or miss) | High — repeated demo/dashboard queries, repeated seeded sub-queries across turns | Low — survey Q5 already specifies the key; canonical seeded sub-query text exists | Canonical seeded sub-query keys; as-of stamps; envelope cites the cached key verbatim |
| **L1** | Containment-lite (semantic-caching remainder): same canonical BGP + filters, new VALUES seed set ⊆ cached seed set → serve from cache; ⊃ → fetch only the difference seeds and merge | Medium-high — bind-join legs re-run with overlapping seed sets constantly | Medium — seed-set subset test is set inclusion; range-filter implication optional later | Per-leg budgets (skip a leg = skip its latency *and* its Snowflake dollar cost — the budget ledger quantifies the win per hit) |
| **L2** | General view matching / aggregate rollup rewrite (Goldstein–Larson / Calcite-class) | Medium, only if telemetry shows recurring aggregate shapes | High — view-matching engine + rollup correctness | Would need a workload log first; defer until Rung-1/2 aggregation ships and telemetry exists |
| **L3** | Full IVM (Materialize/Feldera/DRed-class) | Low for us | Very high — requires owned storage + change feeds | None — contradicts the storage-less, as-of-contracted architecture. **Do not build.** |

The Algebraix idea, translated honestly, is L0 + L1 over a canonical algebra — the same
substance from unencumbered literature, scoped to per-leg granularity where the envelope
keeps citations truthful (a cached leg cites the seeded sub-query + as-of it was computed under).

---

## Combined recommendation — ordering into the M12 sequence

Insert into the M12 optimizer plan (after the main survey's statistics-first and join-menu
items, which remain first):

1. **L0 exact leg cache** (Topic 3). First because it is specified (Q5), cheap, and every
   later feature benefits. Hit/miss + dollar-saved counters go into envelope telemetry.
2. **Aggregation Rung 1 — single-leg pushdown** (Topic 1). Route whole aggregate queries to
   Ontop or Arango legs; refusal message for others names the missing GROUP BY support in the
   Snowflake/ClickHouse compilers (which then becomes an ordinary compiler work item).
3. **FTS conceptual predicate, ArangoSearch first** (Topic 2). `cdf:matchesText` + capability
   registry rows; Arango leg gets BM25-ranked single-leg search; Snowflake `SEARCH()` and
   ClickHouse token functions follow as boolean containment; Postgres/Ontop stays refused
   until a validated path exists. Refuse-with-remedy in cite-or-refuse; declared degraded
   scan in concierge.
4. **Aggregation Rung 2 — distributive two-phase over declared-unique join keys** (Topic 1).
   Depends on the joinKeys/uniqueness catalog metadata (already landing) and on Rung 1's
   compiler work. COUNT(DISTINCT) via exact distinct-value shipment under budget.
5. **L1 containment-lite seed-subset reuse** (Topic 3). After L0 telemetry proves overlap
   rates justify it; same key, plus a subset index over seed sets.
6. **Concierge extras, strictly last:** RRF fusion for multi-leg ranked search; per-leg
   HLL/percentile sketches with declared error bounds.

**What NOT to build (explicit):**

- General partial-aggregation-through-join with COUNT-scaling under bag semantics (full
  Yan–Larson). Restricted even intra-engine in Trino; unverifiable across r2g-mapped sources.
- Cross-engine HLL/quantile **sketch merging** — wire formats are engine-proprietary; we do
  not control source-side libraries. Per-leg native sketches only, concierge only.
- Cross-engine **raw relevance-score fusion** presented as measurement — no principled basis;
  RRF-on-ranks, declared, concierge only.
- A general CQ-containment / view-matching engine ahead of workload evidence (L2 before
  telemetry) — NP-complete machinery for a 2–6-leg engine with no recorded reuse demand yet.
- **Incremental view maintenance** in any form — the hub owns no storage or change feeds;
  as-of stamps + TTL/probe (Q5) are the freshness contract.
- Answer-level or fused-result caching — only per-leg caching keeps citations grounded
  (survey Q5/Q6 rationale; entitlement + as-of live in the key, and containment reasoning is
  never applied across the freshness or entitlement dimensions).

---

## Citations

### Topic 1 — Aggregation
- Yan & Larson, "Eager Aggregation and Lazy Aggregation," VLDB 1995 — https://www.vldb.org/conf/1995/P345.PDF
- Gray et al., "Data Cube: A Relational Aggregation Operator," ICDE 1996 — https://www.cs.cmu.edu/~natassa/courses/15-721/papers/cube_op.pdf
- Trino optimizer properties — https://trino.io/docs/current/admin/properties-optimizer.html ; Querify Labs distinct-agg analysis — https://www.querifylabs.com/blog/distinct-aggregation-optimization-in-apache-calcite-and-trino ; Trino issue #24731 — https://github.com/trinodb/trino/issues/24731
- Spark HashAggregateExec — https://books.japila.pl/spark-sql-internals/physical-operators/HashAggregateExec/ ; aggregation strategy — https://dataninjago.com/2022/01/04/spark-deep-dive-12-aggregation-strategy/
- DataFusion explain/aggregation docs — https://datafusion.apache.org/user-guide/explain-usage.html ; https://docs.rs/datafusion/latest/datafusion/logical_expr/trait.Accumulator.html
- BigQuery federated queries intro (pushdown limits) — https://docs.cloud.google.com/bigquery/docs/federated-queries-intro
- Presto HLL functions — https://prestodb.io/docs/current/functions/hyperloglog.html ; Facebook HLL-in-Presto — https://engineering.fb.com/2018/12/13/data-infrastructure/hyperloglog/
- Apache DataSketches, KLL vs t-digest — https://datasketches.apache.org/docs/QuantilesStudies/KllSketchVsTDigest.html ; Dunning, "The t-digest" — https://www.sciencedirect.com/science/article/pii/S2665963820300403
- Ibragimov, Hose, Pedersen, Zimányi (CoDA), ESWC 2015 — https://link.springer.com/chapter/10.1007/978-3-319-18818-8_17
- Comunica 3.0 release — https://comunica.dev/blog/2024-03-19-release_3_0/ ; GraphDB FedX docs — https://graphdb.ontotext.com/documentation/11.4/fedx-federation.html

### Topic 2 — Text search
- Trino Elasticsearch connector (`raw_query`) — https://trino.io/docs/current/connector/elasticsearch.html
- Trino connector SPI (apply* methods) — https://trino.io/docs/current/develop/connectors.html ; aggregation-pushdown PR #3697 — https://github.com/trinodb/trino/pull/3697
- postgres_fdw docs (shippability, `extensions` option) — https://www.postgresql.org/docs/current/postgres-fdw.html ; PG text-search functions — https://www.postgresql.org/docs/current/functions-textsearch.html
- Begoli et al., "Apache Calcite," SIGMOD 2018 — https://arxiv.org/pdf/1802.10233
- Fox & Shaw, "Combination of Multiple Searches," TREC-2, 1994 (CombSUM/CombMNZ; classical metasearch)
- Cormack, Clarke & Büttcher, RRF, SIGIR 2009 — https://dl.acm.org/doi/10.1145/1571941.1572114
- Jena text — https://jena.apache.org/documentation/query/text-query.html ; Virtuoso bif:contains — https://docs.openlinksw.com/virtuoso/sparqlextensions/ ; Stardog search — https://docs.stardog.com/query-stardog/full-text-search ; W3C sparql-dev inventory — https://github.com/w3c/sparql-dev/wiki/Inventory-of-existing-extensions-to-SPARQL-1.1
- ClickHouse text indexes — https://clickhouse.com/docs/engines/table-engines/mergetree-family/textindexes
- Snowflake SEARCH / full-text — https://docs.snowflake.com/en/user-guide/querying-with-search-functions ; https://docs.snowflake.com/en/user-guide/search-optimization-service

### Topic 3 — Reuse / Algebraix
- Halevy, "Answering queries using views: A survey," VLDB Journal 2001 — https://link.springer.com/article/10.1007/s007780100054
- Dar, Franklin, Jónsson, Srivastava, Tan, "Semantic Data Caching and Replacement," VLDB 1996 — https://courses.cs.duke.edu//spring02/cps296.1/papers/DFJST-VLDB1996.pdf
- Goldstein & Larson, "Optimizing Queries Using Materialized Views," SIGMOD 2001 — https://dl.acm.org/doi/10.1145/375663.375706
- Apache Calcite materialized-view rewriting — https://calcite.apache.org/docs/materialized_views.html ; Trino MV-rewrite issue #20850 — https://github.com/trinodb/trino/issues/20850
- Salas & Hogan, "Canonicalisation of Monotone SPARQL Queries" — https://aidanhogan.com/qcan/extended.pdf ; QCan demo — https://aidanhogan.com/docs/qcan_sparql_demo.pdf ; RDF canonical forms — https://aidanhogan.com/docs/rdf-canonicalisation.pdf
- Papailiou et al., SPARQL query caching, SIGMOD 2015 — https://dl.acm.org/doi/10.1145/2723372.2723714 ; intermediate-result caching, WWW 2011 — https://dl.acm.org/doi/10.1145/1963192.1963273
- Budiu et al., "DBSP," VLDB 2023 — https://docs.feldera.com/vldb23.pdf ; Feldera — https://www.feldera.com/
- Sherman & Bloor, *The Algebra of Data*, 2015 — https://www.amazon.com/Algebra-Data-Foundation-Economy/dp/0978979168 ; Datanami coverage — https://www.datanami.com/2015/12/14/the-algebra-of-data-promises-a-better-math-for-analytics/
- Algebraix Data Corp patent portfolio — https://patents.justia.com/assignee/algebraix-data-corporation
- US7613734 (adjusted expiry 2026-11-19) — https://patents.google.com/patent/US7613734
- US8032509 (anticipated expiry 2026-05-15) — https://patents.google.com/patent/US8032509B2/en
- Algebraix LLC v. IBM settlement coverage — https://www.patsnap.com/resources/blog/litigation/algebraix-llc-v-ibm-data-management-patent-dispute-ends-in-settlement-patsnap-eureka/ ; https://insight.rpxcorp.com/news/84030-former-permission-io-patents-at-issue-against-ibm
- Snowflake persisted query results (cache-hit conditions) — https://docs.snowflake.com/en/user-guide/querying-persisted-results
- Snowflake row access policies — https://docs.snowflake.com/en/user-guide/security-row-intro
