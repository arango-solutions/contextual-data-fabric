---
title: "CDF — Six-Month Roadmap (Sep 2026 – Feb 2027)"
type:
  - internal
  - roadmap
date: 2026-08-31
related:
  - "[[contextual-data-fabric-product-strategy-prd]] (requirements; phases P3–P6)"
  - "[[contextual-data-fabric-north-star]]"
  - "docs/architecture/project-sota-scorecard.md (evidence gates)"
status: draft
version: 0.1
---

# CDF — Six-Month Roadmap (2026-09-01 → 2027-02-28)

> **How to read this.** The [product PRD](contextual-data-fabric-product-strategy-prd.md)
> owns *what and why* (Q1–Q10 decisions, modules M11–M14, phases P3–P6 by theme);
> this roadmap owns the **calendar**: eight 3-week sprints with exit gates in the
> house idiom (a sprint is done when its gate is green, or it isn't done). It also
> introduces the roadmap's backbone, **M15 — the Federation Forge**: the
> ontology→schema mapping pipeline run in reverse as a test-federation generator.
>
> **Cadence decision (2026-09-08, team consensus): sprints are 2 weeks,**
> superseding the 3-week frame and the "revisit at the S4 boundary" rule below
> — observed velocity already ran ahead of the 3-week boxes, and the team
> called it. Effective from S2; S1 closes on its exit gate per the
> finish-early rule. **The S2–S8 date boxes below are stale until the
> 2026-09-09 roadmap review re-cuts them** — content blocks and exit gates
> stand; only the calendar re-slices (note for the re-cut: keep the
> consolidation sprint over the mid-December holiday, wherever it falls in
> the new numbering).
>
> **Cadence rules (added 2026-08-31; frame now 2 weeks).** The sprint frame is
> a **ceiling, not a target**, and it works only with the two-tier cadence
> this repo already runs —
> the inner loop stays PR-sized with every PR landing through the green gate, so
> sprints are *planning and evidence checkpoints, never integration events*.
> (1) **Finish-early / pull-forward:** a sprint ends the moment its exit gate is
> green; the next sprint starts immediately — never pad to the calendar
> (observed velocity says several will finish in under two weeks; Parkinson's
> law is the one real risk of a 3-week frame, and this rule kills it).
> (2) **Day-7 checkpoint:** one async mid-sprint status to the owner — gate
> trajectory, blockers, anything that should redirect — giving 2-week-equivalent
> steering at minimal ceremony cost. The scarce resource is owner decision
> bandwidth, not engineering hours; boundaries are priced accordingly.
> **Revisit at the S4 boundary:** with four sprints of actuals, if gates are
> consistently green by day 10, re-cut S5–S8 at two weeks.

---

## 0. Baseline (what is true on 2026-09-01)

Shipped and gated: four-engine federation with E1.5 (filters, OPTIONAL, single-leg
aggregation), cite-or-refuse envelope + declared partials, 20 goldens (all live as of
2026-09-15 — Snowflake is a paid account that authenticates by key pair; the 5
Snowflake cases were excluded only while a key-pair misconfiguration was
mis-read as a lost account), catalog manifest + integrity gate + label
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
| **r2g** | `migrate-config` resurrects deliberate exclusions + strips curator comments; ~~case-sensitive `singularize`~~ (fixed upstream 2026-09-15, r2g #2: `USAGE_METRICS` → `UsageMetric`; unreleased on 0.4.1) | migration respects exclusions, preserves comments; ~~upstream the singularize fix~~ → cut r2g 0.4.2, regenerate `deploy/snowflake/mapping.yaml` + `deploy/csi/snowflake-telemetry.json`, and drop the forced-lowercase `usage_metrics` collection name |
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
| RD-5 deployment discovery | prospect-interview kit + written deployment-requirements memo; validates CC-8's owner-side extraction hypothesis (RSA/AOE/r2g packaged as owner-run instances shipping only contracts to CDF — the federal-prospect signal) | kit S1, memo by S4 |
| RD-6 secrets graduation | CC-7 P2: secret store behind SecretResolver; source-permission stance | S4 |
| RD-7 user docs | operator docs skeleton (install → connect → curate → ask → read an envelope), grown per sprint; **plus embedded in-product docs (RD-7b/CC-20)**: UI help surfaces, field-documented OpenAPI, CLI --help, catalog descriptions surfaced | skeleton S2, embedded-docs lane from S3, gate at S8 |
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

1. **Snowflake restored** — planned as a re-provisioning (a fresh account, then
   `setup.sql` and the loader again); none of that was needed. *(½ day.)*
   **Done 2026-09-15:** the account was never lost — it is a paid account that
   authenticates by key pair, and the outage was a key-pair misconfiguration —
   and `make gate` against the full live stack returned **20 cases, all green**,
   including g5 (Postgres ⋈ Snowflake ⋈ ArangoDB) and g11.
2. **Capability registry v1** (ADR-0005 D4): `capabilities` block in the manifest,
   probe-verified at onboarding; planner refusals name capabilities, not engine
   kinds. Small, already specified. *(≈3 days.)* **Done 2026-09-15** (ArthurKeen/contextual-data-fabric#37 — a mirror-numbered PR from before the 2026-09-06 topology switch): `capabilities` blocks in the manifest for all four sources, `cdf-catalog probe` verifies them at onboarding (CC-14), planner refusals name the capability and the sources that declare it; the Forge suite (#34) exercises both admission branches on every kind through the registry.
3. **Scale knob v0:** scale-factor parameter on the existing corpus loaders
   (10×/100× row multiplication with key integrity); record `performance-baseline`
   at 1×/10×/100× — the first scale datapoints on the existing harness. *(≈3 days.)* **Done 2026-09-15** (mirror PRs ArthurKeen/contextual-data-fabric#39 and #40): `CDF_SCALE_FACTOR` on the loaders with join-spine integrity; `docs/evidence/scale-baseline-{1,10,100}x.json` recorded live — the three-leg join returning 12.72M result rows (fan-out from 1,272 at 1×) at ~7.2 s p50 at 100×; disclosed evidence, not a scale claim.
4. **ADR-0006 — the Federation Forge**: generator contract, roundtrip property,
   shape descriptor format (ontology + partition map + denorm log + expected
   catalog/goldens), dialect plugin seam, r2g-vs-CDF split. *(≈4 days incl. review.)* **Done 2026-09-08** (#32, `54e16f0`): accepted after PJ's review; D-1 amended to v2 on 2026-09-16 (declared capabilities, #34).
5. **Forge walking skeleton:** ontology→Postgres DDL + naive synthesis + load +
   **roundtrip test green through real RSA/r2g** for the demo ontology. *(≈1 week.)* **Done 2026-09-15** (arango-solutions/r2g-arango#1: `forge.py` + `r2g forge generate`, roundtrip through the real connector and forward map); CDF's orchestrator half — sampler, descriptor, oracle, `make forge-suite`, the committed seed-421 suite — merged 2026-09-16 (#34) after PJ's review (drift check, sign-off ledger, declared capabilities, org pins).
6. **WS-A round 1:** per-repo hardening issue lists filed (table above), tagged
   releases + CC-9 pins for RSA/ASA/r2g as consumed today. *(2026-09-15: RSA 0.8.0, ASA 0.14.0 and r2g 0.4.1 are cut and on PyPI and recorded in the CC-9 table; `arango-sparql-py` still has no release, so its pin stays a git SHA.)* **Done 2026-09-15:** five hardening lists filed on the org repos — arango-solutions/r2g-arango#6, arango-solutions/relational-schema-analyzer#3, arango-solutions/arango-schema-analyzer#8, arango-solutions/arango-ontoextract#16, arango-solutions/arango-sparql-py#8 (covers sparql-py + cypher-py); both git pins now point at the org repos (2026-09-16).

**Exit gate:** gate 20/20 live · capability-named refusal demo · 100× baseline
numbers recorded · ADR-0006 accepted · `introspect(generate(O)) ≡ O` green for
Postgres · five hardening issue lists filed upstream.

**S1 closed 2026-09-16 — all six gate items green** (evidence per item above;
the last to land was the Forge orchestrator, #34, merged 2026-09-16). Per the
finish-early rule S2 starts immediately; its content block stands, its date box
awaits the 2-week re-cut noted at the top.

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
- **ArGOS fabric tabs** — sequenced behind ArGOS R1–R3 (its own roadmap); CDF's
  obligation in this window is versioned contracts (S6). (The GraphQL skin that used to
  share this line was dropped 2026-09-17 — product PRD §4.3.)
- **M3 alignment** — unchanged; but note the forge quietly builds its future test
  bed (generated per-source ontologies with known correspondences).
- **CNL** — parked per the exploration doc; the forge's question generation
  overlaps its synthetic-corpus use and keeps the option warm.

## 4. Division of labor (proposed 2026-09-08 — ratify at the 09-09 review)

Three people, three durable lanes, one rule: **every PR is reviewed by a
non-author**, so each lane below is an ownership default, not a silo.

**Solutions Architect (SA) — architecture, estate, customer.**
Specs and ADRs (PRD gatekeeper; ADR-0006 owner); the Forge generator core in
r2g and the WS-A hardening of r2g/RSA/ASA/AOE; AOE A-box work;
releases + CC-9 pins across the estate; mirror/ops; RD-5 prospect interviews
and RD-2 owner-consent design; demos.

**Solutions Engineer (SE) — NL accuracy, evaluation, question governance.**
The NL lane end-to-end (arango-query-core, nl2sparql, cypher-py, the
fabric's NL front-end) including the documented-null follow-ups (selective
predicate surfacing); owner of `use-cases.md` and question locking; the
Forge's question/golden composition (the SE's shape catalog is the D-4
dependency) and the NL synthetic corpora it unlocks; CK25/eval program and
judge quality; primary reviewer for engine-behavior PRs.

**Intern — well-gated, pattern-following work with visible wins.**
The identified starter set (reconcile against the task list already drafted
outside this repo — fold it into GitHub issues at the review):
- **RD-4b reference corpus**: Northwind through the full
  extract→map→federate→answer loop, then Chinook/Sakila — self-contained,
  gate-verifiable, touches the whole pipeline read-only.
- **RD-7 user docs**: the operator-docs skeleton (install → connect →
  curate → ask → read an envelope), grown each sprint from what they just
  learned onboarding — the intern IS the target audience.
- **Forge dialect plugins** (after the skeleton lands): each new dialect
  follows the Postgres pattern behind one seam — classic
  second-verse-same-as-the-first work with a roundtrip test as the bar.
- **WS-A regression tests**: every feeder-repo fix gets its regression test;
  writing those against filed issues teaches the estate fast.
Rules from RD-8 apply before their first code PR: branch protection
everywhere, the SOP read end-to-end, first PR is a docs or test PR.

**Forge ownership, end to end** (added after SE review of this PR flagged
the gap): generator core and dialect seam in r2g — SA builds, Intern extends
per dialect. The unowned middle was M15 orchestration, and it splits along
ADR-0006 D-4's own line:

- **SA owns the orchestrator** (`deploy/forge/` + `cdf.eval`): the shape
  sampler, descriptor emission, partition-map/expected-catalog computation,
  and `make forge-suite` + CI wiring. Scenario descriptors (the YAML) are
  *generated, versioned artifacts* of that machinery — recorded ownership
  ("which system ended up owning what") is the partition map the orchestrator
  writes, not a ledger anyone keeps by hand.
- **SE owns the coverage policy**: which shape families get generated and in
  what order (the roadmap-named 2–6 leg / hub-heavy / chain families),
  the question + answer-key composition over each shape, and sign-off on
  any descriptor that enters CI as a golden — so nothing becomes a passing
  test without the evaluation lane's judgment.
- Hand-tuned one-off descriptors (a specific regression shape) may be
  authored by anyone under SE's policy; non-author review applies as
  everywhere else.

Standing duties that rotate rather than belong: demo readiness
(`pre-demo-gate` before any customer showing), triaging the shared-memory
capture queue, and the day-7 sprint checkpoint write-up.

## 5. Risks

1. **Forge scope creep** — it can become a product. It is a *testing* module
   (M9/M10 class, not sold); ADR-0006 must say so and S3's gate keeps it harnessed
   to the suite.
2. **Owner time on feeder repos** — WS-A depends on RSA/ASA/r2g/AOE attention; the
   per-sprint hardening lane is sized at ≤20% and the workaround-stays rule means a
   slipped upstream fix degrades gracefully.
3. ~~**Snowflake account**~~ — resolved 2026-09-15: the account authenticates by
   key pair and the gate runs 20/20 live; no Snowflake case is excluded.
4. **Synthetic ≠ real** — forge evidence must never *replace* customer-shaped
   validation; the private corpus remains the demo/eval spine, the forge is breadth
   and publishability.
5. **Two roadmaps, one team** — ArGOS R0–R2 overlaps this window; the explicit
   coupling points are S1 (Q-10 decision) and S6 (contract versioning), nothing
   else.
