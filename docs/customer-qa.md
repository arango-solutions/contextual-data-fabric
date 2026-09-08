---
title: Customer Q&A — design-partner questions, answered from the running system
type:
  - internal
  - customer-facing-reference
status: draft
version: 0.1
date: 2026-07-22
related:
  - "[[contextual-data-fabric-prd|PRD]] §2"
  - "[[architecture/module-05-federated-query-engine/adr/ADR-0002-snowflake-cortex-agentic-legs|ADR-0002]]"
  - "[[use-cases|Use cases]]"
---

# Customer Q&A (the customer's data-platform and engineering leads — 2026-07)

> Presenter reference. Every answer below is backed by the **running demo**
> (four live sources: Postgres CRM, Snowflake telemetry, ClickHouse analytics,
> ArangoDB documents — federated, cited, nothing copied) — say "let me show
> you" wherever possible instead of explaining.

---

## the customer's data-platform lead

### 1. "How do you define entities — especially for structured data and relationships — programmatically and automatedly?"

**Schema in, ontology out — deterministically first, LLM only as assist.**
For structured sources, the schema analyzers introspect the live catalog
(tables, columns, types, primary/foreign keys, row samples) and emit a
conceptual model mechanically: tables become entity classes, foreign keys and
join tables become relationships, and a naming layer produces the OWL-style
vocabulary automatically (`USAGE_METRICS` → `UsageMetric`, `account_id` →
`accountId`). No LLM is required for this baseline — an optional LLM refinement
pass improves names and models embed-vs-link choices, but it sees **schema
metadata only, never rows**. For unstructured sources, LLM extraction is used —
grounded in the source, scored by judges, and curated.

**The human stays in the loop at ~2%, not 100%:** automation proposes, a
curator confirms the small borderline set. That is the direct answer to "not
rely on someone's knowledge" without swinging to "trust a black box."

**Demo evidence:** every mapping file in the demo is machine-generated from the
live schemas (`r2g export-csi` / `export-r2rml`); the hand-authored versions
were deleted from git. The generation is re-runnable in front of you.

### 2. "Do we need to move all the data into Arango to run this?"

**No — that is the fabric's first design principle, and the demo proves it
live.** Queries are *pushed down*: the English question becomes one conceptual
query, the planner splits it by source, each fragment compiles to the source's
native language (SQL for Postgres/Snowflake/ClickHouse, AQL for ArangoDB) and
runs **where the data lives**. Only result rows travel — and the join keys from
the first leg are pushed into later legs (a bind-join), so no source ever
returns more than the accounts in play. The citations show the exact SQL each
source executed, so "no data moved" is inspectable, not asserted.

### 3. "How can we build the 'brain' on the Arango side, given all the data is in Snowflake?"

**The brain is metadata, not data** — the mental model is the PubMed/NIH
metadata graph: linkages, not payloads. Arango holds three things: the **master
ontology** (what the concepts are), the **functional mappings** (how each
concept is realized in each source — the mapping *is* the query), and the
**canonical entity linkages** (which rows across systems are the same
real-world thing). Building it from Snowflake: point the analyzer at
`INFORMATION_SCHEMA`, get the source ontology, align it into the master, store
ontology + mappings in Arango — and every query federates back to Snowflake
live. **We run exactly this today:** our usage telemetry exists *only* in a
live Snowflake; the fabric answers questions joining it with CRM and documents
without a single copied row.

### 4. "When we build the graph — the relationships and everything — is it saved on the Snowflake side or the Arango side?"

Separate the two things "graph" can mean:

- **The schema-level graph** — classes, relationships, concept→table mappings,
  cross-source equivalences — lives in **Arango**. That's the brain, it's
  small, and it's versioned (time-travel, change control).
- **The row-level relationships** are **computed at query time** by pushdown +
  join on business keys; they aren't persisted anywhere. Your data and its
  foreign keys stay in Snowflake, untouched.
- **Exception, on purpose:** when an analytics use case genuinely needs a
  persistent graph shape (PageRank, community detection), the fabric can
  **temporarily materialize a bounded subgraph** into Arango — deliberate,
  scoped, disposable (the "assembled" pattern). That's an explicit, budgeted
  act, never a default.

### 5. "How do we build this in Arango with multiple subject areas, without giving everything to the LLM?"

Four containment mechanisms, all in the architecture:

1. **Use-case scoping** — extraction is driven by competency questions (the
   questions the ontology must answer), so each subject area extracts what its
   use cases need, never boil-the-ocean.
2. **The structured path needs no LLM at all** — schema→ontology is
   deterministic introspection (Q1 above); at query time the deterministic
   planner is the target, with the LLM only as an optional NL front door.
3. **What the LLM does see is gated** — schema names and types, never bulk
   rows; classification-aware redaction keeps restricted/PII columns out of
   any prompt (the same governance lattice that gates data loading).
4. **Subject areas are tiered ontologies** — a shared domain tier plus
   per-domain local extensions, merged only where alignment finds real overlap
   (see the engineering lead Q2/Q4 below), with a human blessing the merge set.

---

## the customer's engineering lead

### 1. "What's the area of focus for the data Arango works on — is it customer data?"

The fabric is a **general, composable capability** (ontology derivation,
alignment, federated query, grounding — each independently usable). The
pressure-test domain is **Customer 360** because it forces the hard case:
structured metrics and unstructured sentiment about the *same* account, where
the truth lives in the contradiction between them. Two clarifications that
matter to a security buyer: all demo data is **100% synthetic** (invented
companies), and in production the fabric's focus is the **semantic layer over
your systems** — your customer data stays in your systems of record; Arango
holds the ontology, mappings, and entity linkages about it.

### 2. "How do you prevent overlap between local ontologies across domains and the shared domain, when using LLMs for extraction?"

Overlap is **prevented at extraction and resolved at alignment**:

- **Prevented:** extraction prompts are *import-aware* — the effective shared
  ontology is serialized into the prompt with explicit instructions to reuse
  existing classes (via `subClassOf` / `equivalentClass`) rather than mint
  duplicates; a conflict detector flags duplicate URIs/labels the moment they
  appear; and every LLM-proposed element must trace to a grounded source
  anchor or it's rejected (the hallucination gate).
- **Resolved:** a dedicated alignment stage finds cross-domain
  correspondence candidates by embedding retrieval + lexical/structural
  scoring; **the LLM adjudicates only the borderline band** (the
  research-backed pattern that cuts LLM comparisons ~94%); accepted
  equivalences are recorded as `owl:equivalentClass` with provenance; a human
  confirms the small contested set. Local tiers stay local; the shared tier
  grows only through that governed merge.

### 3. "What's the LLM extraction cost, especially with frequently changing unstructured data like Salesforce notes and emails?"

**Costs are incremental by design — change processing, not re-extraction:**

- New/changed documents flow through **belief revision**: the system first
  computes, mechanically, which existing knowledge the new evidence touches;
  the LLM is invoked **only** for genuinely contradicted or uncertain items —
  behind a rate-limiting circuit breaker. Unchanged knowledge costs nothing.
- The structured side costs **zero LLM tokens** at steady state (deterministic
  introspection + deterministic query compilation), and a federated *query*
  costs at most one small NL-translation call — the per-source legs are
  compiled SQL/AQL, not LLM calls (see ADR-0002 for why we keep LLMs out of
  the query legs).
- And we treat cost as a **first-class, measured requirement**, not a claim:
  the plan is instrumented (tokens, wall-clock, per source), so the answer to
  "what does it cost" is a dashboard number, not an estimate. *(This question
  is why that requirement exists.)*

### 4. "How would different entities like 'customer account' and 'client account' be merged or classified?"

At two levels, both governed:

- **Schema level (the concepts):** alignment proposes that `CustomerAccount`
  (source A) and `ClientAccount` (source B) are the same concept — embedding
  similarity + shared-property structure + lexical signals score the pair; if
  it's borderline, an LLM judges it with evidence; if accepted (auto above
  threshold, human otherwise), the master ontology records
  **`owl:equivalentClass` with provenance**, and the equivalence is
  **materialized into the master** — so at query time `c:Account` simply *is*
  both, with zero runtime reasoning and zero per-query LLM cost.
- **Instance level (the rows):** entity resolution links records across
  systems into **canonical entities** — blocking + similarity scoring +
  clustering, with explainable survivorship (every merge records why, and what
  lost). In the current demo the join runs on deterministic business keys —
  **no fuzzy matching on the query path**; fuzzy/semantic resolution happens
  at curation time, where it can be reviewed, not at answer time, where it
  can't.

The one-line version: **names are reconciled once, in the ontology, with
provenance — not re-guessed by an LLM on every query.**

---

## the customer's data-platform lead — follow-ups (2026-07-22)

### 5. "We have thousands of tables in Snowflake — can we put policies/information on the CDF to focus the ontology extraction on the relevant ones? Maybe express the purpose of the integration, e.g. Customer 360?"

**Yes — and "express the purpose" is exactly the fabric's existing scoping
contract.** An integration carries a declared **purpose**: an ORSD-style
requirements spec (purpose statement + the competency questions the ontology
must answer — for you, the Customer-360 questions). That purpose then focuses
extraction two ways:

- **Today (concept-level):** the CQ term set is injected into extraction as
  required/priority concepts — extract what the use cases need, never
  boil-the-ocean.
- **Planned (table-level, the thousands-of-tables case — schema-analyzers
  RE-6):** rank tables by relevance *before* introspecting them, using four
  signals: semantic similarity between the purpose/CQ terms and table/column
  names **and their Snowflake `COMMENT`s**; FK-neighborhood expansion from
  seed tables; **your own query history** (`ACCOUNT_USAGE.ACCESS_HISTORY` —
  the tables your org actually queries and joins are the relevant ones); and
  governance **tags** as include/exclude policy. A curator confirms the ranked
  candidate set — the same "automation proposes, human confirms ~2%" pattern,
  applied to table selection.

The one-liner: *you don't tell us the 40 relevant tables — you tell us the
questions, and the fabric proposes the 40 tables, with your query history as
evidence.*

### 6. "Snowflake doesn't define primary keys and foreign keys — does your extractor infer them?"

**Yes — declared keys are read, undeclared keys are inferred.** Precision
matters here: Snowflake *supports declaring* PK/FK/UNIQUE constraints but
doesn't *enforce* them — and many teams declare them anyway as documentation.
Our connectors read those declarations from the catalog (`SHOW PRIMARY KEYS`,
declared FKs/uniques), so anything you've declared is used directly. Where
nothing is declared, the extractor **infers**: name-convention heuristics
(`account_id` ↔ `accounts`) propose candidate keys, and a **value-overlap
sampler** confirms them statistically (what fraction of the child column's
distinct values exist in the candidate parent — bounded sampling, no bulk
reads). Inferred keys carry confidence and go through the same human-confirm
step as everything else. *(Roadmap honesty: the value-overlap confirmation
stage is live for Postgres/MySQL/SQL Server/CSV and lands for Snowflake as
RE-7 — today's Snowflake inference is name-heuristic + declared keys.)*

### 7. "Does Snowflake have its own catalog? Are you using it?"

**Yes, and yes — the catalog is precisely what we read.** Snowflake's catalog
surfaces are `INFORMATION_SCHEMA` (tables, columns, types, declared
constraints — our introspection source today, via the same connector that
runs the federated leg), plus the richer account-level views we plan to
exploit for relevance scoping (RE-6): `ACCOUNT_USAGE` (**`ACCESS_HISTORY`**,
`QUERY_HISTORY`, `OBJECT_DEPENDENCIES` lineage), object **`COMMENT`s**, and
governance **`TAG`s**. For customers running an external enterprise catalog
(OpenMetadata, Atlan, Glue), r2g already has a catalog-provider integration
layer — the same discover-then-connect flow works there. The principle: **we
never ask you to re-describe what your catalog already knows** — declared
keys, comments, tags, and usage history are all extraction inputs.

---

## The in-house-fabric question (2026-08-09)

> Context: the design partner's own publicly-marketed
> security-data-fabric white paper arrived 2026-08-09. Its thesis — entities
> over event logs, a semantic layer that separates how data is stored from
> how it's queried, metadata-driven integration — **is our thesis**. Open
> with agreement, not contrast: the people in the room built an
> entity-centric fabric and won the argument internally. The difference is
> *where the data lives* and *what an answer must carry*.

### 8. "We already built our own data fabric. Why do we need this?"

**Because their fabric and the CDF make opposite default choices about data
movement, and the CDF makes it a per-source choice instead of an
architectural identity.**

- **Their fabric materializes**: ingest → format → enrich → resolve/dedupe → group,
  into one curated entity store. That is the right shape for security-ops
  telemetry at volume — continuous entity resolution and pre-joined entities
  make heavy analytics fast.
- **The CDF's default is federate-in-place**: the *ontology* moves, the data
  does not. A question is partitioned by concept ownership, each leg runs
  natively on the system of record (SQL on Snowflake and Postgres, AQL on
  ArangoDB), results join on the business key at query time — and every
  answer carries the exact per-source queries that produced it, or refuses.
  *Let me show you* — the Provenance & Execution panel in the demo is this
  contract running live. Nothing in the materializing pipeline produces an
  answer-level receipt like that: its lineage ends at the pipeline.
- **We are not federation-only.** When copying a leg is the right call
  (analytics-heavy workloads, offline sources), r2g is our ETL path — the
  same schema→ontology→R2RML toolchain that generates the federation
  mappings also loads relational sources into the graph. Today that is
  per-source; assembling multiple disparate sources into one materialized
  fabric is on the roadmap, not on the truck. Materialize vs. federate
  becomes a deployment decision per source, made under one ontology —
  not a religion.
- **Complementary, not competing**: a curated entity store like theirs can
  simply be *a source in the fabric* — federate over it alongside Snowflake
  and the document graph, and the CDF adds what the white paper doesn't
  cover: the natural-language front door and the grounded, cite-or-refuse
  answer contract (golden-gated, adversarially tested).

**Honest asymmetries** (say them before they do): continuous entity
resolution at ingest and connector breadth are theirs today; ours are
deterministic business-key joins, standards-based mappings (OWL/SPARQL/
R2RML rather than a proprietary schema), zero-copy freshness, and
answer-level provenance.
