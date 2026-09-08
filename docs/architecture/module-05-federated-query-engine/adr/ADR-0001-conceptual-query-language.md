---
title: "ADR-0001 — Conceptual-model query language / intermediate representation"
adr: 0001
module: 05-federated-query-engine
status: proposed
date: 2026-07-13
deciders: ["Arthur Keen", "PJ"]
supersedes: []
related:
  - "[[contextual-data-fabric/docs/architecture/module-05-federated-query-engine/specification|M5 spec]]"
  - "[[contextual-data-fabric/docs/architecture/module-04-mapping-layer/specification|M4 mapping layer]]"
  - "r2g PRD Phase 12 (federated query & runtime mappings)"
---

# ADR-0001 — Conceptual-model query language / intermediate representation

**Status:** Proposed (for team review)
**Resolves:** M5 spec §9 open question 3 — *"is the 'query graph over the ontology' the right intermediate?"*

## Context

M5 turns an English question into a query over the **conceptual model** (the
master ontology), then declarative **mappings** rewrite that query into
per-source queries (SQL pushdown to relational sources, AQL over the Arango
graph, agent calls), which execute **without moving data** and are reassembled
into one grounded, cited answer.

Three languages are easy to conflate; only the middle one is undecided:

1. **User-facing** — English (decided: LLM/deterministic decomposer).
2. **Conceptual-query IR** — the language the question is expressed in *against
   the ontology, before mapping to sources.* **← this ADR.**
3. **Per-source target languages** — SQL / AQL / agent calls (decided; the SQL
   leg is r2g PRD Phase 12).

Decision drivers (from the PRD + North Star):
- Bind to the conceptual model's **OWL/RDF semantics** (subclass, equivalence,
  `sameAs`) — this is what reconciles `customer account` vs `client account`
  and is the differentiator over agent-to-agent (PRD §2.3).
- **"The mapping is the query"** — a declarative mapping must rewrite the
  conceptual query into per-source queries deterministically.
- **No materialization**; **inspectable/deterministic** plans (cost & latency
  are political); **provenance/citation** native; **cross-source joins** on
  canonical entity keys (AER); **OSI**-aligned; **LLM-generatable**.

## Research summary (2026-07; deep-research pass, 75 confirmed findings, 0 refuted)

- **OBDA / Virtual Knowledge Graph is exactly this pattern and is mature.** The
  VKG specification is the tuple **`P = (O, M, S)`** — ontology *O* as the
  conceptual schema, mappings *M*, sources *S* (Xiao, Calvanese et al., *Virtual
  Knowledge Graphs: An Overview*, MIT Press *Data Intelligence* 1(3), 2019;
  "VKG … also known as Ontology-Based Data Access (OBDA)"). This is our mental
  model, formalized.
- **Ontop** and **Stardog Virtual Graphs** rewrite **SPARQL → SQL at runtime
  with no RDF materialization** ("query unfolding"), driven by **R2RML**
  mappings and OWL 2 QL / RDFS ontologies (github.com/ontop/ontop;
  docs.stardog.com/virtual-graphs; peer-reviewed: Ontop, *Semantic Web Journal*;
  Calvanese et al.). **Ontop ships native connectors for PostgreSQL, MySQL, SQL
  Server, Oracle, Snowflake (v5.0.0), Databricks, BigQuery, Redshift, Trino,
  DuckDB** — i.e. every relational source r2g supports. A 2025 paper demonstrates
  a **federated** VKG on Ontop over disparate sources with on-demand querying
  and no data migration (MDPI *Future Internet* 17(6):245, 2025).
- **Apache Calcite** is a real relational-algebra federation engine (relational
  algebra IR, planning rules, cost-based optimization, adapters that push
  filters/projection down and join across backends; SIGMOD 2018, arXiv
  1802.10233) — but it is **tabular and carries no ontology semantics**.
- **GraphQL Federation** (Apollo) resolves entity fields per subgraph and joins
  on `@key` (≈ a primary key) — a clean *resolver = mapping* model, but it
  carries **no ontology semantics / reasoning** (docs confirm field resolution +
  entity keys, nothing more).
- **The ArangoDB/AQL translation is OWNED IN-HOUSE — correction to the web-only
  research.** It is true that *ArangoDB core* ships no native SPARQL, that
  *ArangoRDF* is import/export only (github.com/ArangoDB-Community/ArangoRDF),
  and that no mature *third-party* SPARQL→AQL exists. **But we own two
  transpilers that close exactly this gap:**
  - [`arango-sparql-py`](https://github.com/ArthurKeen/arango-sparql-py) (v0.1)
    — **SPARQL 1.1 → AQL** via rdflib + an OWL/Turtle schema ontology; W3C DAWG
    *syntax* 100%, query-*evaluation* coverage still being ported from the
    legacy JS `arango-sparql`. i.e. the Arango leg for a SPARQL IR is
    **mostly-built-in-house, needs finishing** — not greenfield.
  - [`arango-cypher-py`](https://github.com/ArthurKeen/arango-cypher-py) (v0.2,
    the more mature) — **openCypher → AQL** over PG/LPG/hybrid **plus a mature
    NL → *conceptual* Cypher pipeline** (few-shot, fuzzy entity resolution,
    EXPLAIN-grounded self-healing retry, eval harness at 93–100% pattern-match;
    the LLM emits conceptual Cypher and never sees the physical mapping).
- **The owned analyzer/transpiler stack (the decisive context the first web
  pass missed).** Both AQL transpilers map from a conceptual model via
  **`arango-schema-mapper` (= `arangodb-schema-analyzer`)** — the **Arango-side
  sibling of `relational-schema-analyzer` (RSA)**, emitting the *same
  tool-contract bundle*. So the stack is coherent and already owned: **two
  analyzers** (relational RSA + Arango `arangodb-schema-analyzer`) → **one
  canonical bundle** (conceptual↔physical mapping) → **transpilers on both
  sides** (r2g / Ontop for SQL; arango-sparql-py / arango-cypher-py for AQL).
  M5 is therefore substantially an **integration of owned components**, not a
  from-scratch build.
- **LLMs generate structured queries better from a compact typed intermediate.**
  FRASE-style frame IR lifts text-to-SPARQL accuracy 30%→38% / F1 40%→50%; and
  arango-cypher-py's NL→conceptual-Cypher pipeline independently demonstrates
  the same principle in production (93–100% with an eval gate). This NL→IR
  engineering is **IR-agnostic** and should be reused whichever IR wins.

## Options considered

| Option | Ontology semantics | "Mapping-is-query" rewrite | Owned transpilers (relational / Arango) | Federation | Maturity for our use | Verdict |
|---|---|---|---|---|---|---|
| **(a) SPARQL IR + OBDA** | ★★★ (OWL 2 QL) | ★★★ (R2RML) | Ontop *(adopt — Apache-2.0 OSS)* / **arango-sparql-py** *(own, v0.1)* | ★★ (SERVICE) | relational ★★★ / Arango ★★ (eval coverage WIP) | **Recommended canonical IR — both legs owned/available** |
| **(b) Small typed graph-pattern IR → serializes to SPARQL** | ★★★ | ★★★ | via (a) | ★★★ (our planner) | small build over (a) | **Chosen IR *shape* under (a)** |
| **(c) Cypher (openCypher) IR** | ★ (no OWL reasoning) | ★★ (schema-mapper) | ✗ standard Cypher→SQL / **arango-cypher-py** *(own, v0.2, +NL engine)* | ★★ | Arango ★★★ / relational ✗ | **Live alternative — most mature *today*; weak relational leg** |
| (d) GraphQL federation | ✗ | ★★ (resolver=code) | — | ★★★ | ★★★ | Reject as IR; fine as *external* API |
| (e) Relational-algebra virtualization (Calcite) | ✗ | ★★ | ★★★ | ★★★ (cost-based) | ★★★ | Reject as IR; candidate for P3 planner |
| (f) TypeDB/TypeQL | ★★★ (+reasoning) | ★★ | ✗ | ★ | ★ (lock-in) | Reject (ecosystem lock-in) |

## Decision

1. **Internal conceptual-query IR = a small, typed graph-pattern IR over the
   ontology that serializes to SPARQL** (option b). Query-as-a-graph *is* the
   SPARQL basic-graph-pattern model; a compact typed subset is what the
   LLM/deterministic decomposer emits and validates against the ontology
   (FRASE evidence), and it serializes to standard SPARQL at the boundary for
   OSI/interop.
2. **Mappings are expressed as / exportable to R2RML** (the mature, standard,
   Ontop/Stardog-compatible mapping language). r2g/RSA already produce
   concept→table / property→column mappings; R2RML export (r2g P12.1) is the
   durable contract.
3. **Relational legs: adopt a VKG engine (Ontop) for SPARQL→SQL pushdown**
   rather than hand-rolling it — it is mature, non-materializing, and already
   covers every relational source we use. r2g/RSA supply the R2RML mappings and
   the conceptual model. *(This reframes r2g P12.2 — see Consequences.)*
4. **Arango leg: use the owned `arango-sparql-py` (SPARQL→AQL)** as the Arango
   transpiler under a SPARQL IR — and **finish its query-evaluation coverage**
   (the real remaining work). *(If the team picks the Cypher IR instead,
   `arango-cypher-py` already transpiles + ships the NL engine — see Open
   decisions.)* No greenfield AQL generator and no dependence on a third-party
   SPARQL→AQL.
5. **Reuse `arango-cypher-py`'s NL→conceptual-query engineering** (few-shot,
   fuzzy entity resolution, EXPLAIN-grounded self-healing retry, eval harness +
   regression gate) for the NL→IR step — it is IR-agnostic and already proven.
6. **Federation & reassembly: engine-side join on AER canonical entity keys**
   for P1; revisit a cost-based multi-source planner (Calcite-style) at P3.
7. **GraphQL/MCP is the *external* agent-facing surface** over the fabric
   (`federate(question) → cited envelope`), **not** the internal IR — keep the
   two layers separate.

> **Note (correction).** An earlier draft of this ADR — grounded only in a web
> research pass — concluded the Arango leg was a from-scratch build ("no
> off-the-shelf SPARQL→AQL"). That was wrong: `arango-sparql-py` and
> `arango-cypher-py` are owned, working transpilers built on the same
> analyzer/mapper family as RSA. The decision below is **integrate + finish**,
> not build; and the IR choice is genuinely reopened (see Open decisions).

## Consequences

- **M5 is mostly integration of owned components, not a build.** The stack
  already exists: RSA + `arangodb-schema-analyzer` (mappings) → r2g/Ontop (SQL)
  + `arango-sparql-py`/`arango-cypher-py` (AQL). The real remaining work is
  narrow: (a) **finish `arango-sparql-py` query-evaluation coverage** (if
  SPARQL IR), (b) **align the mapping artifacts** across RSA / arangodb-schema-
  analyzer / R2RML / the OWL-Turtle the transpilers consume, (c) the
  **cross-source planner + join**, (d) wire in `arango-cypher-py`'s NL engine +
  eval harness.
- **r2g Phase 12 is reframed.** P12.1 (R2RML/runtime-mapping export) becomes the
  primary, durable deliverable. P12.2 (bespoke pushdown SQL generation) is now a
  **adopt-vs-build decision** against Ontop (Apache-2.0 OSS, free — not a
  purchase) — RESOLVED: adopt Ontop; native generation demoted to "optional /
  only if a deployment declines to run a VKG engine."
- **Reasoning is materialized at ontology-build time** (M2/M3): fold
  `sameAs`/`equivalentClass` alignment into the master ontology so M5 query-time
  reasoning stays minimal, fast, and deterministic.
- **OSI/OWL alignment preserved**; the SPARQL serialization keeps interop with
  the OSI ecosystem.
- **Phase-1 pragmatics:** you do **not** need a triple store or a full SPARQL
  engine for the 1-week demo. The LLM decomposer emits the typed IR; the
  relational partition compiles to SQL (via Ontop, or r2g P12.2 as a stopgap);
  the Arango partition emits AQL directly; join on AER keys.

## Code-read findings (owned repos, 2026-07) — settle the open decisions

A direct read of the transpilers + analyzers (not web research) resolved the two
biggest open decisions:

- **`arango-cypher-py`** is the more mature transpiler (openCypher TCK *core*
  ~90%, 154 test modules) and owns the best asset in the stack: a **proven,
  IR-agnostic NL→query engine** (few-shot, fuzzy entity resolution,
  EXPLAIN-grounded self-healing, eval + regression gate; 93–100% pattern-match).
  Its "conceptual Cypher" is genuinely **source-neutral** — but its physical
  mapping vocabulary is **100% Arango** (`COLLECTION`/`LABEL`/edge styles); there
  is **no relational vocabulary and no Cypher→SQL leg**. Making Cypher the
  *cross-source* IR is a net-new build.
- **`arango-sparql-py`** has broad SPARQL→AQL *translation* coverage
  (BGP/FILTER/OPTIONAL/UNION/MINUS/aggregation/subqueries/paths), is clean and
  injection-safe (~781 tests) — but **evaluation correctness is not CI-gated**
  (live-Arango tests are all xfail'd/excluded; a documented variable-predicate
  bug binds `?p` to an attribute *name*, not an IRI, which would corrupt
  IRI-keyed joins). It accepts only a *full SPARQL string* — **no query-graph
  partition entry, no canonical-key join contract, no provenance** (all v1
  non-goals).
- **Neither transpiler is federation-shaped**, so the planner / partition /
  canonical-key / provenance layer is **net-new regardless of which IR wins** —
  it does not discriminate between SPARQL and Cypher.
- **A canonical interchange already exists: `CSI v1`**
  (`arango-schema-analyzer/schema_analyzer/csi/`) —
  `{conceptualModel, arangoPhysicalMapping, provenance{direction}}` — *designed*
  as the cross-tool hub and **explicitly naming r2g as the forward producer**,
  but only the reverse (Arango) side emits it today. RSA and the Arango analyzer
  share the `{conceptualSchema, physicalMapping, metadata}` envelope but
  deliberately diverge in `physicalMapping` (RSA `tableName`/FK vs Arango
  `collectionName`/edge). **r2g is the only component that knows both** (source
  tables + the collections it created), so it is the natural forward-CSI + R2RML
  producer.

## Open decisions

1. **~~IR = SPARQL vs Cypher~~ — RESOLVED (code-read): SPARQL is the canonical IR.**
   It is the only option that carries OWL semantics (the A2A differentiator),
   has a mature *relational* leg (Ontop), and has an owned *Arango* leg
   (`arango-sparql-py`). Cypher is the more mature transpiler and owns the best
   NL engine, but cannot federate to relational without a net-new Cypher→SQL
   transpiler + relational mapping vocabulary. **Decision: SPARQL IR, and
   harvest `arango-cypher-py`'s IR-agnostic NL engine to *generate* it** (swap
   the ~5 Cypher seams: system prompt, schema-card renderer, response extractor,
   parser, EXPLAIN-grounded validator). **Cost to accept:** finish
   `arango-sparql-py` (promote real-Arango evaluation to a CI gate; fix the
   variable-predicate IRI bug) and add a federation entry point (accept a
   query-graph partition; return a canonical entity key). Bounded —
   weeks-not-quarters. **→ PAID (2026-07-15, days not weeks): WPs A2/A3/C1/C2
   landed in `arango-sparql-py` (`b26f35d`); contract doc
   `docs/architecture/proposals/federation-entry-point.md`.**
2. **Relational engine: adopt Ontop (Apache-2.0 OSS, free) vs r2g P12.2 (build)? — RESOLVED: adopt Ontop.** Ontop is mature +
   covers all sources but adds a Java VKG service + R2RML discipline; r2g P12.2
   avoids new infra but reinvents a solved problem. **Recommendation: Ontop for
   the relational legs; keep r2g P12.1 (R2RML export) as the contract.**
   *(Still a team decision — but see #3: R2RML export is needed either way.)*
3. **~~Mapping-artifact alignment~~ — RESOLVED (code-read): adopt `CSI v1` as the
   canonical hub; build four adapters.**
   1. **r2g → forward `CSI v1` emitter** (highest value — r2g holds both the
      conceptual model and the Arango-physical mapping it created).
   2. **CSI → R2RML** for the SQL/Ontop side (nothing emits R2RML today; this is
      r2g P12.1's real content).
   3. **CSI → `MappingBundle`/OWL-Turtle** for the AQL transpilers (mechanical
      shim; both are `{entities,relationships}` with COLLECTION/edge styles).
   4. **Fix the `phys:` namespace mismatch** — `arango-sparql-py` accepts
      `https://arango.solutions/phys#` while the analyzers **and**
      `arango-cypher-py` emit `http://arangodb.com/schema/physical#`; align the
      accepted list (cheap) or add a rewrite. *(Real bug: analyzer-emitted
      Turtle is not consumable by `arango-sparql-py` today.)*
4. **Federation layer is first-class M5 work (net-new regardless of IR):** the
   partition planner, canonical-key join (on AER entities), and provenance/as-of
   surfacing exist in neither transpiler — scope them as M5 build, not a
   transpiler tweak.
5. **How much OWL reasoning at query time vs build time** (recommend: build
   time — materialize `sameAs`/`equivalentClass` into the master ontology).
