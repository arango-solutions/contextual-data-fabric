---
title: "ADR-0005 — Cross-leg federated aggregation and the source-capability registry"
adr: 0005
module: 05-federated-query-engine
status: accepted
date: 2026-08-26
deciders: ["Arthur Keen"]
related:
  - "[[ADR-0003-authoritative-catalog-manifest|ADR-0003]]"
  - "issue #15 (design commission), issue #14 (rung 2, shipped)"
  - "docs/research/federated-aggregation-text-and-reuse.md (evidence base)"
  - "docs/research/federated-query-optimization.md (M12 survey)"
---

# ADR-0005 — Cross-leg aggregation & the capability registry

**Status:** accepted (design only — implementation is P4/M12 work, scheduled
separately). **Trigger:** issue #15; evidence base merged 2026-08-24.

## Context

Single-leg aggregation shipped (#14): a top-level `GROUP BY` whose pattern,
grouping keys, and aggregated variables all route to ONE aggregation-capable
source ships verbatim to that engine (Ontop, Arango today). Everything
cross-source is refused by name, and the refusal is golden-pinned (g16):
SPARQL aggregates are defined over the **joined solution multiset**, so
"aggregate per leg, then join" is silently wrong under join multiplicity —
the one failure mode this fabric exists to never have.

The evidence base (research addendum, Topics 1–2) established: the strongest
industry precedent is *negative* (BigQuery does not push aggregation to
federated sources); only research systems do cross-endpoint SPARQL
aggregation; Yan & Larson (VLDB '95) give the correctness conditions; sketch
wire formats are engine-proprietary; and every production federation routes
per-source *capabilities*, not lowest-common-denominator SQL.

Four questions this ADR answers:

1. How does the fabric ever aggregate across legs **correctly**?
2. Does the federator need an embedded SPARQL processor to combine leg results?
3. Which aggregation shapes are refused **permanently** (vs. not-yet)?
4. What must the catalog know about each source first?

## Decision

### D1 — Cross-leg aggregation = two-phase distributive aggregation over declared-unique join keys (rung 3)

When a `GROUP BY` spans legs, the fabric MAY decompose it as
**partial-aggregate-per-leg → final combine in the federator**, under ALL of:

- every aggregate is **distributive or algebraic**: COUNT, SUM, MIN, MAX,
  AVG (decomposed as SUM+COUNT — never AVG-of-AVGs); COUNT(DISTINCT x) only
  when x is entirely leg-local;
- each leg's partial groups by (query grouping keys ∪ its cross-source join
  keys) — the Yan-Larson key-widening rule;
- duplicate sensitivity is neutralized: the join key is **declared unique on
  the one-side** in the catalog (P6.7 `joinKeys` + the manifest's
  `uniqueConstraints` lineage). SUM/COUNT partials crossing a join whose
  multiplicity is unknown are **refused** — no count-scaling compensation
  (explicitly on the addendum's don't-build list);
- HAVING, ORDER BY, LIMIT over aggregate values evaluate **final-stage only**.

Anything outside these conditions keeps today's named refusal. The g16 golden
is rewritten (not deleted) when rung 3 lands: it must then pin the *remaining*
refusals (holistic cross-leg, unknown-multiplicity joins).

### D2 — The combine is a fold, not a SPARQL engine

The federator does NOT embed a SPARQL processor. At 2–6 legs with only
distributive/algebraic aggregates admitted, the final combine is a
deterministic dictionary fold over the joined partials (SUM of COUNTs, SUM of
SUMs, MIN of MINs, SUM/COUNT for AVG) — dozens of lines, unit-testable,
auditable in the envelope. Two alternatives were considered and rejected:

- **Embedded engine (pyoxigraph/rdflib) evaluating the original query over
  fetched triples** — general, but abandons pushdown (fetch-everything), adds
  a second semantics to certify, and its answers would cite an engine no leg
  executed. *Revisit trigger:* if the admitted fragment ever grows past what
  a fold can express (e.g. nested aggregation), reopen with pyoxigraph as the
  candidate (it is already the W3C reference in arango-sparql-py's tests).
- **Assembled-mode AQL** (mirror joined intermediates into the hub's temp
  graph, aggregate there) — viable and already half-built (`assembly.py`),
  but it turns every cross-leg aggregate into a materialization; kept as the
  *fallback path* for shapes the fold cannot express, never the default.

### D3 — Permanent refusals (by design, not by gap)

- **Holistic aggregates cross-leg** (MEDIAN/percentiles; GROUP_CONCAT with
  cross-leg ordering): single-leg only, ever. No production federation does
  these cross-source without full data movement, and full movement is the
  anti-thesis of this fabric.
- **Cross-engine sketch merging** (HLL et al.): wire formats are proprietary
  per engine — infeasible, don't revisit.
- **Approximate COUNT(DISTINCT)** via native sketches: single-leg,
  concierge-mode only, declared in the envelope as approximate.
- **User-directed SERVICE**: unchanged (g17). The plan MAY later be *emitted*
  in SERVICE form as an EXPLAIN/interop artifact — a rendering, never an
  execution path.

### D4 — The capability registry is the admission prerequisite

Rung-2/3 admission hardcodes engine knowledge today (`kind in {"snowflake",
"clickhouse"} → refuse`). Before rung 3, the ADR-0003 manifest gains a
per-source `capabilities` block, probe-verified at onboarding (a declared
capability whose probe fails does not exist — CC-14):

```json
"capabilities": {
  "aggregation": {"groupBy": true, "having": true, "countDistinct": true,
                   "approxCountDistinct": "hll"},
  "textSearch":  {"dialect": "arangosearch|tsvector|snowflake-search|clickhouse-token|none",
                   "indexedProperties": ["Document.text"],
                   "analyzer": "text_en@rev",
                   "scored": true},
  "orderLimitPushdown": true
}
```

The planner consults capabilities instead of kinds; refusals then name the
*capability*, not the engine ("source X declares no GROUP BY support").
Text-search fields follow the addendum's §2.6 design (analyzer pinning per the
postgres_fdw shippability lesson; unscored dialects can match but not rank;
cross-source rank fusion is RRF, concierge-only). The `cdf:matchesText` magic
predicate is specified there and lands with FTS work, not this ADR.

## Consequences

- #15 closes as **designed**; implementation enters P4/M12 sequencing as:
  capability registry → fold-combine for COUNT/SUM/MIN/MAX/AVG → HAVING/ORDER
  final-stage → concierge extras. Each rung must land with its refusal
  goldens rewritten in the same PR (the `unsupported_contains` inverse guard
  forces this mechanically).
- The envelope grows one honesty field when rung 3 lands: per-leg citations
  mark `partial_aggregate: true` so a reader can see which engine computed
  which partial and how the fold combined them.
- The g19/g20 single-leg goldens and the #14 admission path are unaffected.
- Rung-3 correctness leans on P6.7 declared join keys — the CRM overlay work
  (PR #11) is retroactively the first prerequisite of federated aggregation.
