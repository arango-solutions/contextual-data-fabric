---
title: "CDF — Six-Month Roadmap (Sep 2026 – Feb 2027)"
type:
  - internal
  - roadmap
date: 2026-08-31
related:
  - "[[contextual-data-fabric-product-prd]] (requirements; phases P3–P6)"
  - "[[contextual-data-fabric-north-star]]"
  - "docs/architecture/project-sota-scorecard.md (evidence gates)"
status: draft
version: 0.1
---

# CDF — Six-Month Roadmap (2026-09-01 → 2027-02-28)

> **How to read this.** The [product PRD](contextual-data-fabric-product-prd.md)
> owns *what and why* (Q1–Q10 decisions, modules M11–M14, phases P3–P6 by theme);
> this roadmap owns the **calendar**: eight 3-week sprints with exit gates in the
> house idiom (a sprint is done when its gate is green, or it isn't done). It also
> introduces the roadmap's backbone, **M15 — the Federation Forge**: the
> ontology→schema mapping pipeline run in reverse as a test-federation generator.
>
> **Cadence rules (added 2026-08-31).** The 3-week frame is a **ceiling, not a
> target**, and it works only with the two-tier cadence this repo already runs —
> the inner loop stays PR-sized with every PR landing through the green gate, so
> sprints are *planning and evidence checkpoints, never integration events*.
> (1) **Finish-early / pull-forward:** a sprint ends the moment its exit gate is
> green; the next sprint starts immediately — never pad to the calendar
> (observed velocity says several will finish in under two weeks; Parkinson's
> law is the one real risk of a 3-week frame, and this rule kills it).
> (2) **Day-7 checkpoint:** one async mid-sprint status to the owner — gate
> trajectory, blockers, anything that should redirect — giving 2-week-equivalent
> steering at 3-week ceremony cost. The scarce resource is owner decision
> bandwidth, not engineering hours; boundaries are priced accordingly.
> **Revisit at the S4 boundary:** with four sprints of actuals, if gates are
> consistently green by day 10, re-cut S5–S8 at two weeks.

---

## 0. Baseline (what is true on 2026-09-01)

Shipped and gated: four-engine federation with E1.5 (filters, OPTIONAL, single-leg
aggregation), cite-or-refuse envelope + declared partials, 20 goldens (5
Snowflake-excluded — trial expired), catalog manifest + integrity gate + label
curation, governance module (identity planes, entitlements, authorization goldens),
ontology diagram + presentation directives in the demo, ADR-0001…0005, the
optimization/aggregation/reuse research corpus, and the ArGOS re-scope (consoles are
ArGOS tabs; CDF ships contracts). Known holes: no statistics-driven planning, no
capability registry, no text search, join intelligence undesigned-in-code, scale
evidence level 2, estate components (RSA/ASA/r2g/AOE) carrying known
one-workaround-deep fixes.

## 1. The three workstreams this roadmap adds to the PRD

### WS-A — Estate hardening (AOE, ASA, RSA, r2g, query libs)

The fabric is only as strong as its feeders, and every recent sprint shipped a
*workaround* for a feeder gap that deserves an upstream fix:

| Component | Known debt (already filed/worked around) | Hardening target |
|---|---|---|
| **RSA** | FK inference misses natural-key references (CRM overlay is the workaround) | inference targets declared-unique natural keys; overlay stays as curator override |
| **ASA** | relationships only from edge collections (issue asa#27, declared-refs overlay is the workaround); case-sensitive gaps | `apply_key_overlay`-style declared-references API + attribute-reference inference |
| **r2g** | `migrate-config` resurrects deliberate exclusions + strips curator comments; case-sensitive `singularize` | migration respects exclusions, preserves comments; upstream the singularize fix |
| **AOE** | alignment (M3) APIs unexposed to the fabric; release-gated flow only | expose alignment/belief APIs per the repo-enhancement spec; wire the fabric's Q-11 policy vocabulary when ArGOS FR-5 lands |
| **query libs** | pin discipline manual; NL eval level 2 | tagged releases consumed by pin (CC-9), eval-gated bumps |

Cadence: a hardening lane in **every** sprint (S1 files the per-repo issue lists;
S6 is a consolidation sprint that lands the big ones). Rule: a workaround merged in
CDF *must* have its upstream issue filed the same week — no silent permanence.

### WS-B — M15, the Federation Forge (new module; testing-class, like M9/M10)

**The idea (Arthur, 2026-08-31): run the ontology→schema mapping process in
reverse.** From one ontology, *generate* physical schemas for different system
kinds, *partition* the data across systems, and *apply controlled
denormalizations* — producing unlimited **federation shapes**, each born with its
**ground truth attached**: the generating ontology *is* the expected aligned
ontology, the partition map *is* the expected catalog ownership, the injected
denormalizations *are* the expected collision/synonym report, and queries composed
against the ontology have computable expected answers.

Three generators, one contract:

1. **Schema generator (reverse mapping):** ontology + dialect → DDL/collections
   (Postgres, Snowflake, ClickHouse SQL; Arango document/edge collections) +
   synthesized data honoring keys and declared cardinalities. Inverts the exact
   pipeline r2g/RSA/ASA already run forward — and the **roundtrip property is the
   core correctness test**: `introspect(generate(O)) ≡ O` through the real
   analyzers, per dialect.
2. **Partitioner:** an assignment of concepts→systems (respecting single-owner
   ownership and declared join keys) → one *federation shape*. Shapes are sampled:
   2-leg…6-leg, hub-heavy, chain joins, wide/narrow entities — the planner finally
   gets tested on topologies we didn't hand-craft.
3. **Denormalizer:** controlled transformations with recorded intent — embed a 1:N
   into the parent, duplicate a column across entities (a known collision), split
   or merge tables, rename to synonyms, and **strip declared constraints** (emit a
   variant with no PKs/FKs at all, the Snowflake reality, so the *inference* path
   is what gets tested) — inverting `r2g analyze-denorm`'s smell catalog into a
   smell *injector*. catalog-integrity, join-intelligence, and alignment (M3,
   later) get labeled test beds instead of anecdotes.

Alongside the generated shapes, a **reference-database corpus** (PRD RD-4b): real,
well-known schemas run through the full extract→map→federate→answer loop —
Northwind first (r2g already trains against it), then Chinook, Sakila, and an
AdventureWorks-class schema. Generated shapes give breadth; reference databases
keep the forge honest against schemas humans actually wrote.

**Why this is the backbone:** it unlocks WS-A validation (feeders tested against
generated shapes, not one corpus), the scale program (S4: turn the row-count knob),
join-intelligence evaluation (S7: discovered joins scored against the partition
map), NL synthetic corpora (questions generated from the ontology with gold
queries), and — decisively — **publishable SOTA evidence**: the scorecard's level-4
rungs require publicly reproducible workloads, our real corpus is customer-shaped
and private, and forge-generated federations are publishable by construction.

Design lands as **ADR-0006** (S1). Home: generator core in r2g (it owns the
mapping machinery both directions), orchestration + shape/goldens emission in CDF
under `deploy/forge/` + `cdf.eval`.

### WS-C — Customer-evaluation readiness (PRD §12, added 2026-09-05)

The rung above demo-ready. The PRD's readiness ladder (§12, RD-1…RD-8) names what
"a customer tests it in an isolated scope" requires; this workstream schedules the
items the calendar didn't already carry:

| Item | What lands | When |
|---|---|---|
| RD-1/RD-3 HITL loops | AOE curation + r2g mapping-review reachable as ArGOS tabs; curator edits survive regeneration (the WS-A r2g debt is the blocker) | contracts S6; consoles sequenced behind ArGOS R1–R3 |
| RD-2 owner consent | entity/property exclusions enforced at catalog admission (Q-11 vocabulary + manifest entitlements) | design S2, enforcement S3 |
| RD-4b reference corpus | Northwind through the full loop; Chinook/Sakila following | S3 (with the forge suite) |
| RD-5 deployment discovery | prospect-interview kit + written deployment-requirements memo | kit S1, memo by S4 |
| RD-6 secrets graduation | CC-7 P2: secret store behind SecretResolver; source-permission stance | S4 |
| RD-7 user docs | operator docs skeleton (install → connect → curate → ask → read an envelope), grown per sprint | skeleton S2, gate at S8 |
| RD-8 team process | branch protection + required review across the estate; library release trains on the CC-9 pins | before first added engineer lands code (interns: S1–S2) |

**Honest statement for stakeholders:** R2 is not a date on this calendar — RD-1's
console depends on ArGOS's roadmap, and RD-5's answers come from customers. What
this roadmap commits to is that *every fabric-side gate* (RD-2, RD-4, RD-6, RD-7,
RD-8 and the RD-3 contract half) is green by S8 (Feb 2027), so customer evaluation
becomes an ArGOS-sequencing decision, not an engineering one.

---

## 2. The calendar — eight 3-week sprints

### S1 · Sep 1–19 — “Unblock, instrument, and design the Forge”  *(the 3-week example)*

Concrete and staffable now:

1. **Snowflake restored** (decision + execution: billing on the expired trial or a
   fresh account; `setup.sql` + loader re-run) → the 5 excluded goldens return;
   gate back to 20/20 live. *(½ day once decided.)*
2. **Capability registry v1** (ADR-0005 D4): `capabilities` block in the manifest,
   probe-verified at onboarding; planner refusals name capabilities, not engine
   kinds. Small, already specified. *(≈3 days.)*
3. **Scale knob v0:** scale-factor parameter on the existing corpus loaders
   (10×/100× row multiplication with key integrity); record `performance-baseline`
   at 1×/10×/100× — the first scale datapoints on the existing harness. *(≈3 days.)*
4. **ADR-0006 — the Federation Forge**: generator contract, roundtrip property,
   shape descriptor format (ontology + partition map + denorm log + expected
   catalog/goldens), dialect plugin seam, r2g-vs-CDF split. *(≈4 days incl. review.)*
5. **Forge walking skeleton:** ontology→Postgres DDL + naive synthesis + load +
   **roundtrip test green through real RSA/r2g** for the demo ontology. *(≈1 week.)*
6. **WS-A round 1:** per-repo hardening issue lists filed (table above), tagged
   releases + CC-9 pins for RSA/ASA/r2g as consumed today.

**Exit gate:** gate 20/20 live · capability-named refusal demo · 100× baseline
numbers recorded · ADR-0006 accepted · `introspect(generate(O)) ≡ O` green for
Postgres · five hardening issue lists filed upstream.

### S2 · Sep 22 – Oct 10 — “Aggregation rung 3 + Forge roundtrip everywhere”
- Fold-combine cross-leg aggregation (COUNT/SUM/MIN/MAX/AVG over declared-unique
  join keys, per ADR-0005 D1/D2); refusal goldens rewritten in the same PR;
  `partial_aggregate` citations in the envelope.
- Forge: Snowflake + ClickHouse DDL dialects, Arango collection generation;
  roundtrip green on all four.
- **Gate:** a cross-leg COUNT answers grounded with per-leg partials cited; a
  forge-generated 2-leg federation onboards via the `add-source-*` skills untouched.

### S3 · Oct 13–31 — “Generated federations end-to-end”
- Partitioner v1: shape descriptors → live multi-system deployments (compose +
  loaders) → auto-generated catalogs → **auto-generated goldens with computed
  expected answers**; first 10-shape suite in CI (fixture mode) + nightly (live).
- Text search v1: `cdf:matchesText` on the Arango leg (ADR-0005 D4 fields, analyzer
  pinning, refuse-with-remedy elsewhere).
- **Gate:** `make forge-suite` runs N generated shapes through partition→execute→
  ground with zero hand-written fixtures; a text-search question answers on Arango
  and refuses (named) on Snowflake.

### S4 · Nov 3–21 — “Scale, measured”
- Forge at volume: 10⁶-row federations, skew and cardinality knobs.
- M12 statistics v1: envelope-telemetry loop closed; row counts/NDV in the catalog;
  seed-strategy ladder (batched VALUES → temp-table → min/max → hash) chosen by
  stats (the research addendum's sequence).
- p95 budgets on perf goldens, CI-tracked (the `perf` marker pattern from
  arango-sparql-py).
- **Gate:** published internal report: latency/transfer vs shape × scale, before/
  after the strategy ladder — the scorecard's scale dimension moves 2→3.

### S5 · Nov 24 – Dec 12 — “Shapes that lie: the denormalizer”
- Denorm injector v1 (embed, duplicate-column, split/merge, synonym-rename) with
  recorded intent; catalog-integrity and label-curation evaluated against injected
  truth (precision/recall of collision/synonym/hub detection — measured, not
  anecdotal).
- Hardening: RSA natural-key inference + ASA declared-references API consumed if
  landed upstream (workarounds deleted; overlays remain as curator overrides).
- **Gate:** integrity-report P/R on 20 denormalized shapes published; at least one
  CDF workaround deleted in favor of an upstream fix.

### S6 · Dec 15 – Jan 9 — “Consolidation” *(holiday-sized on purpose)*
- WS-A round 2: land/absorb remaining upstream fixes; estate release tags; docs.
- L0 canonical leg cache (exact-match, as-of + entitlement scoped — research Topic
  3’s first rung).
- ArGOS check-in: contracts versioned for its R1 needs (Q-10 registry direction
  executed on whichever side was decided).
- **Gate:** every consumed estate component at a tagged release with green goldens;
  cache hit/miss visible in envelope metrics.

### S7 · Jan 12–30 — “Join intelligence, tested on the Forge”
- Identifier/semi-identifier profiling + MinHash/HLL sketches into the catalog;
  join-discovery scored against forge partition maps (known-true join edges =
  labeled evaluation, the thing real corpora can never give us).
- JoinKey registry + curator accept/reject; join-confidence field enters the
  envelope (trust class, per PRD §4.5).
- **Gate:** discovery P/R published across shape families; a fuzzy-join question
  answers with declared confidence on a forge shape with no declared keys.

### S8 · Feb 2–20 — “Evidence sprint: go public”
- Package a forge-generated benchmark (shapes + data + goldens + runner) as a
  **public, version-pinned artifact**; run CDF on it end-to-end; publish results +
  method — the scorecard's first level-4 push (correctness + scale dimensions).
- Run COA's `golden_compare` on our r2g output (the comparison report's
  recommendation); record the numbers.
- Six-month review: scorecard re-scored, next-half plan drafted against P5
  (CDC/virtualization/controller) which this roadmap deliberately did not start.
- **Gate:** benchmark artifact public; scorecard delta published; H1-2027 plan
  reviewed.

---

## 3. What this roadmap deliberately defers (and why)

- **P5 delivery modes (CDC, virtualization, controller)** — the continuum needs the
  statistics, capability, and cache substrate S4–S7 build; starting it now would
  stack unproven layers.
- **GraphQL skin + ArGOS fabric tabs** — sequenced behind ArGOS R1–R3 (its own
  roadmap); CDF's obligation in this window is versioned contracts (S6).
- **M3 alignment** — unchanged; but note the forge quietly builds its future test
  bed (generated per-source ontologies with known correspondences).
- **CNL** — parked per the exploration doc; the forge's question generation
  overlaps its synthetic-corpus use and keeps the option warm.

## 4. Risks

1. **Forge scope creep** — it can become a product. It is a *testing* module
   (M9/M10 class, not sold); ADR-0006 must say so and S3's gate keeps it harnessed
   to the suite.
2. **Owner time on feeder repos** — WS-A depends on RSA/ASA/r2g/AOE attention; the
   per-sprint hardening lane is sized at ≤20% and the workaround-stays rule means a
   slipped upstream fix degrades gracefully.
3. **Snowflake account** — S1 item #1; every Snowflake-touching gate stays
   declared-excluded until resolved (honest, but eroding).
4. **Synthetic ≠ real** — forge evidence must never *replace* customer-shaped
   validation; the private corpus remains the demo/eval spine, the forge is breadth
   and publishability.
5. **Two roadmaps, one team** — ArGOS R0–R2 overlaps this window; the explicit
   coupling points are S1 (Q-10 decision) and S6 (contract versioning), nothing
   else.
