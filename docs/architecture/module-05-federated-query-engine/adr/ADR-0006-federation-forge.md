---
title: "ADR-0006 — The Federation Forge: generated federations with ground truth attached"
adr: 0006
module: 05-federated-query-engine
status: proposed
date: 2026-09-05
deciders: ["Arthur Keen", "PJ (review)"]
related:
  - "[[ADR-0005-cross-leg-aggregation-and-capability-registry|ADR-0005]]"
  - "docs/roadmap-2026H2.md §WS-B (commission), §S1–S4, S7 (consumers)"
  - "docs/contextual-data-fabric-prd.md §12 RD-4 (readiness gate this serves)"
  - "docs/architecture/project-sota-scorecard.md (level-4 evidence rule)"
---

# ADR-0006 — The Federation Forge (M15)

**Status:** proposed — commissioned by roadmap WS-B (idea recorded 2026-08-31);
acceptance target is the S1 roadmap review. **Scope guard up front:** the Forge
is a *testing-class* module (M9/M10 class). It is never sold, never on a live
path, and ships no service endpoints. The S3 gate keeps it harnessed to the
test suite (roadmap risk #1).

## Context

Every correctness claim the fabric makes is currently validated against **one
hand-built corpus**: three invented accounts, four hand-assigned sources, one
partitioning, one set of naming conventions, keys always declared. The gate's
20 golden cases are strong *contracts* but weak *coverage* — the planner has
never seen a topology we didn't hand-craft, catalog-integrity has never seen a
collision we didn't plant by hand, and the scale program (S4) has nothing to
turn a knob on. PRD §12 names this readiness gap RD-4; the SOTA scorecard's
level-4 rungs additionally require **publicly reproducible** workloads, and our
real corpus is customer-shaped and private.

The insight (Arthur, 2026-08-31): the estate already owns the ontology→schema
mapping machinery in the *forward* direction (RSA/ASA introspect physical
schemas; r2g maps them to CSI/R2RML; the fabric federates over the result).
**Run it in reverse.** From one ontology, *generate* physical schemas for
different engine kinds, *partition* synthesized data across systems, and
*apply controlled denormalizations* — producing unlimited federation shapes,
each born with its ground truth attached:

- the generating ontology **is** the expected aligned ontology;
- the partition map **is** the expected catalog ownership;
- the injected denormalizations **are** the expected collision/synonym report;
- queries composed against the ontology have **computable expected answers**
  (evaluated once against the pre-partition dataset).

No oracle problem, no hand-written fixtures, and — because everything is
generated — every shape is publishable by construction.

## Decision

### D-1 · The shape descriptor is the contract (and the oracle)

One versioned artifact fully determines a generated federation and its
expected outcomes:

```yaml
forgeShapeVersion: 1
seed: 421            # every stochastic choice flows from this
ontology: shapes/o1/ontology.ttl        # the source of truth (OWL, CC-12 names)
scale: {rowsPerEntity: 1000}            # S4 turns this knob
partitionMap:                            # concept -> engine assignment
  Account:    {dialect: postgres,   system: pg1}
  UsageEvent: {dialect: clickhouse, system: ch1}
  Document:   {dialect: arango,     system: ar1}
denormLog:                               # applied transformations, WITH intent
  - {op: embed_1n,  child: Address, into: Account, expect: "collision report row"}
  - {op: rename,    entity: Account, prop: accountName, to: acct_nm,
     expect: "synonym mapping in the aligned CSI"}
  - {op: strip_constraints, system: pg1, expect: "keys recovered by inference OR
     reported absent — never silently wrong"}
expected:
  catalog:  shapes/o1/expected-catalog.json   # ownership, join keys, collisions
  goldens:  shapes/o1/goldens/*.json          # questions + computed answers
```

Descriptor + seed reproduce the schemas, the data, and the expectations
byte-identically. The descriptor is the only interface between the three
generators and every consumer (gate, scale program, join-intelligence eval,
NL corpora). Anything a test wants to assert must be derivable from it.

### D-2 · Three generators, strict order, one data pass

1. **Schema generator (reverse mapping):** ontology + dialect → DDL /
   collection definitions per system, plus loaders. Inverts the exact pipeline
   r2g/RSA/ASA run forward.
2. **Partitioner:** assigns concepts→systems respecting single-owner concept
   ownership and declared join keys; samples shape families the roadmap names
   (2–6 legs, hub-heavy, chain joins, wide/narrow entities).
3. **Denormalizer:** controlled transformations with recorded intent — embed a
   1:N into its parent, duplicate a column across entities (a known collision),
   split/merge tables, rename to synonyms, and **strip declared constraints**
   (the no-PK/FK Snowflake reality, so the *inference* path is what gets
   tested). Inverts `r2g analyze-denorm`'s smell catalog into a smell injector.

**Data is synthesized once, against the ontology, before partitioning** —
then rows are projected into each system's physical shape. This is the load-
bearing ordering decision: join-spine values must agree *across* systems, and
per-system independent generation can never guarantee that. Synthesis honors
declared keys, cardinalities, and (where the CSI carries them) sample-value
vocabularies; expected answers are computed on this pre-partition dataset, so
the oracle never depends on any engine's behavior.

### D-3 · The roundtrip property is the core correctness test

```
introspect(generate(O)) ≡ O      — per dialect, through the REAL analyzers
```

with `≡` defined honestly, not naively: equality **up to** CC-12 naming
normalization and declared-constraint availability. For constraint-stripped
variants the contract is two-branch: the analyzers either *recover* the keys
by inference or *report them absent* — recovering a wrong key, or silence, is
the failure. The roundtrip runs through the real RSA/ASA/r2g releases (CC-9
pins), never through forge-internal shortcuts — the Forge exists to test the
estate, so the estate must be in the loop.

### D-4 · Home split: generation in r2g, orchestration in CDF

- **r2g owns the generator core** (it owns the mapping machinery in both
  directions): schema generation, dialect plugins, data synthesis, the
  denormalizer. New dialects arrive as plugins behind one seam
  (`generate(ontology, dialect, seed) -> {ddl, loader, rows}`); Postgres,
  Snowflake-SQL, ClickHouse-SQL, and Arango collections are the launch set.
- **CDF owns M15 orchestration** under `deploy/forge/` + `cdf.eval`: shape
  sampling, descriptor emission, expected-catalog/goldens computation,
  `make forge-suite`, and CI wiring (fixture mode per-PR, live mode nightly).
- Question/golden composition reuses the estate's existing query-shape
  template machinery (the NL-GEN-01 template catalog in the query libs)
  rather than inventing a second generator.

### D-5 · Determinism and publishability are requirements, not hopes

Seeded RNG end to end; a descriptor re-run must be byte-identical. Generated
shapes contain no customer-derived values, so the S8 evidence sprint can
publish full shape suites — the scorecard's level-4 ladder ("publicly
reproducible workloads") is reachable *only* through this property, which is
why it is a design requirement rather than a nice-to-have.

### D-6 · What the Forge is NOT

Not a benchmark replacement (BSBM/LUBM/TPC don't exercise federation
partitioning, ownership, or denorm smells — but we don't compete with them);
not a data anonymizer; not a demo generator; not a product. And not a
substitute for reality: the **reference-database corpus** (RD-4b — Northwind
first, then Chinook/Sakila-class) rides alongside precisely because generated
shapes inherit our assumptions — real schemas written by real humans keep the
Forge honest (roadmap risk #4).

## Consequences

- **S1 exit:** this ADR accepted + the walking skeleton green — one ontology →
  Postgres DDL + naive synthesis + load + `introspect(generate(O)) ≡ O`
  through real RSA/r2g. **S2:** the other three dialects. **S3:**
  partitioner + auto-generated goldens, first 10-shape suite in CI. **S4/S7:**
  the scale knob and join-intelligence eval consume descriptors unchanged.
- WS-A hardening gets labeled test beds instead of anecdotes: every feeder-repo
  fix lands with a forge shape that would have caught it.
- The gate grows a second population: hand-authored goldens keep pinning the
  demo arc's *contracts*; forge goldens supply *coverage*. Neither replaces
  the other.
- New failure surface: bugs in the Forge itself masquerade as estate bugs. The
  roundtrip property plus the reference-DB corpus are the two independent
  checks that keep the Forge falsifiable.
- Cost: r2g takes a new module and a plugin seam it must maintain; accepted —
  r2g already owns both directions' semantics, and the alternative (a separate
  generator repo) would drift from the mapping rules it must invert.
