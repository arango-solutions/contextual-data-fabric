---
title: "AWS Context Ontology Accelerator vs the Contextual Data Fabric — comparison"
type:
  - internal
  - research
  - competitive-analysis
date: 2026-08-19
status: draft — for team review
related:
  - "docs/architecture/project-sota-scorecard.md"
  - "docs/contextual-data-fabric-product-strategy-prd.md"
  - "docs/contextual-data-fabric-north-star.md"
---

# AWS `context-ontology-accelerator` (COA) vs the Contextual Data Fabric (CDF)

> **Why this matters.** On 2026-07-31 AWS announced GA of an open-source
> "Context Ontology Accelerator" ([repo](https://github.com/aws/context-ontology-accelerator),
> Apache-2.0, ~530 stars in 3 weeks) and stated it **"will become a managed AWS
> Context feature."** It is the closest architectural sibling to this project
> that exists in public: ontology + auto-generated R2RML + Ontop + SPARQL + MCP
> + human-in-the-loop review. AWS independently chose our skeleton — which is
> both the strongest validation our category thesis has received and the
> clearest threat signal. This report compares the two on evidence.
>
> **Method.** COA facts come from its repo, docs site, package READMEs, release
> notes, the AWS What's-New announcement, and third-party deploy write-ups
> (sources at the end); claims the repo does not support are marked. CDF facts
> come from this repo at `main` (2026-08-19) and the SOTA scorecard v1.2
> (self-assessed evidence levels; no dimension above "internally benchmarked").

---

## 1. Executive summary

- **Same thesis, different center of gravity.** Both systems bet that agents
  need a governed, ontology-mediated semantic layer over enterprise sources.
  COA's core competence is **ontology induction** (LLM-assisted schema→OWL with
  steward review); CDF's is **federated answering** (multi-engine execution
  with cite-or-refuse provenance). Each is strongest exactly where the other is
  thinnest.
- **COA's "SPARQL federation" is a translation layer, not an execution layer.**
  Its Ontop runs against a schema-only in-memory H2 and only *emits SQL*; real
  cross-source execution is delegated to **Amazon Athena**. CDF executes
  federated legs itself across four engines (Postgres via Ontop, Snowflake and
  ClickHouse natively, ArangoDB via SPARQL→AQL) with bind-join pushdown and
  staged parallelism.
- **CDF's grounding discipline has no counterpart in COA.** COA returns
  generated SQL, a resolution trace, and a confidence score — but has **no
  per-fact citations, no refusal machinery, and no answer-correctness
  evaluation** (its only benchmark scores induced-ontology *structure*). CDF's
  cite-or-refuse envelope + 15 execution-graded goldens + adversarial set is
  the single largest capability gap between the systems, in our favor.
- **COA ships things we have only designed.** Enforced governance (Cedar
  action-level + a no-bypass table/column SQL firewall), a 6-tool MCP server,
  and a real steward console (Cloudscape: induction runner, proposal review
  with Turtle editing, class-graph explorer, metric editor). Our ADR-0004
  identity planes and M14 console are accepted designs, not running code.
- **Lock-in is the strategic wedge.** COA hard-requires Neptune, OpenSearch
  Serverless, Bedrock + AgentCore, DataZone, Athena, and Cognito; "no component
  runs outside AWS," us-east-1 only. CDF runs on Docker anywhere and treats
  the warehouse-of-record as a peer, not a destination.
- **Maturity is not comparable in either direction.** COA is 3 weeks old, a
  read-only mirror (no PRs accepted), 2 releases, with open issues clustered on
  its MCP tools returning empty results. CDF is ~6 weeks of code with a green
  15-case live gate — but zero public benchmark artifacts (our scorecard's own
  finding). Neither side gets to claim maturity; the race is open.

---

## 2. What COA is (factual sketch)

**Scan → Model → Serve** over AWS:

- **Scan:** Step Functions discover JDBC schemas (Postgres, Redshift, MySQL,
  SQL Server; v0.2.0 adds Oracle/Snowflake) and Glue catalogs; provisions
  Athena federated catalogs; documents go through a separate ECS pipeline into
  a **Neptune GraphRAG graph + OpenSearch Serverless vectors**.
- **Model:** LLM ontology induction (Bedrock Claude) — `table_to_ontology`
  (class-per-table, FK→object property) or the two-LLM `rigor_ontology`
  strategy — emitting **proposal OWL + auto-generated R2RML**; automated
  "grounding" aligns classes to FIBO/Schema.org/PROV-O/Dublin Core/FOAF via
  Cohere embeddings (emits `skos:closeMatch` only — **no hierarchy axioms, no
  N-way merge**); **HermiT consistency-checks proposals** (block-on-failure)
  with SHACL shapes generated from DB constraints; steward accepts/rejects in
  the console.
- **Serve:** a tiered router — T1 deterministic metric templates → T2
  NL→SPARQL constrained to R2RML-mapped classes → Ontop **translate-only** →
  JDBC/Athena execution → T2.5 direct NL→SQL fallback (confidence < 0.6) → T3
  GraphRAG synthesis. Six MCP tools on Bedrock AgentCore are the agent surface.
  Governance = Cedar (namespace roles: owner/maintainer/data-steward/
  data-analyst) + a no-bypass SQL firewall (table allowlist, column denylist,
  metric allowlist).

Positioning per AWS: an accelerator whose users "will be able to migrate their
ontologies" to a coming **managed AWS Context feature** — i.e., a seed, not a
supported product.

---

## 3. Head-to-head on the 12 SOTA-scorecard dimensions

CDF levels are the scorecard's self-assessed **evidence** grades (0–5 scale;
3 = internally benchmarked). COA is characterized from its repo; it publishes
no answer-level benchmarks, so evidence grades would be 0–1 on our scale for
most query-side dimensions — the table therefore compares *capability shape*,
with the evidence caveat where it bites.

| Dimension (weight) | CDF today | COA today | Who leads |
|---|---|---|---|
| Federated query correctness (12) | 4-engine live execution; 15 execution-graded goldens incl. adversarial/refusal; level 3 | Translation-only Ontop; execution via JDBC/Athena; **no answer-correctness eval at all** | **CDF, decisively** |
| Semantic/ontology depth (10) | Minimal mapping-fragment OWL (CC-12 naming, single-owner concepts); AOE's richer extraction unwired; level 2 | LLM induction + FIBO/Schema.org grounding + HermiT/SHACL proposal validation + steward review UI — but `closeMatch` only, no hierarchy/merge | **COA on acquisition; CDF on operational discipline** |
| Connector/source breadth (8) | Postgres, Snowflake (native), ClickHouse (native), ArangoDB; level 3 | Postgres, Redshift, MySQL, SQL Server, Oracle/Snowflake (v0.2), Glue; docs via GraphRAG | **COA on count; CDF on execution depth** (native legs vs Athena delegation) |
| Federation optimization/economics (10) | Bind-join pushdown, staged parallel legs, pooled connections, cost metering, M12 research done; level 3 | None visible — Athena does the joining; ~500–800 ms cross-source per their docs | **CDF** |
| NL/agent accuracy (8) | Registry + validated NL→SPARQL + syntax-directed editor; execution-graded NL eval (hard-corpus baseline); level 2 | Tiered router with confidence fall-through; few-shot examples; `isMapped`-constrained generation; **T2.5 bypasses the ontology to raw NL→SQL** | **Split** — their routing is production-shaped; our validation + eval discipline is stronger; their T2.5 bypass violates the translate-once principle |
| Answer provenance/grounding (10) | Per-claim citations (actual SQL/AQL + source objects + as-of), cite-or-refuse, declared partial; level 3 | Generated SQL + resolution trace + confidence; **no citations, no refusal** | **CDF, decisively** — our strongest differentiator per the scorecard, now confirmed against the nearest competitor |
| Entity resolution (8) | AER integrated in roadmap (M6), deterministic key joins today; level 2 | **Absent** (0 hits in repo); declared PK/FK only | **CDF** (roadmap + owned engine vs nothing) |
| Governance/security (10) | ADR-0004 accepted (identity planes, OBAC, citation redaction) — **design, not enforcement**; level 2 | **Shipped**: Cedar action-level + no-bypass SQL firewall (table/column/metric grain); delegated agent auth (OIDC/PKCE) | **COA** — they enforce today; note neither has concept- or row-level control |
| Reliability/operability (8) | Compose stacks, strict startup, CI + live gates; level 3 | 16 CDK stacks, ~1.5 h deploy, teardown "hours to days" (AgentCore ENIs), us-east-1 only; issues: MCP tools return empty payloads | **CDF at demo scale**; COA has cloud-ops scaffolding but reliability bugs |
| Scale/performance (8) | Demo-scale corpus; sub-second warm queries; no public scale evidence; level 2 | Athena-backed data plane scales by construction; no published benchmarks either | **COA structurally, unproven**; both lack evidence |
| Developer/agent interfaces (4) | Demo UI + catalog-scoped SPARQL editor; MCP **planned** (§10.2); level 3 | **Shipped 6-tool MCP server** (buggy per issues) + full Cloudscape console + playground + OSI import/export | **COA** — they shipped the two surfaces we've only specified |
| Evaluation rigor (4) | 15 goldens execution-graded, adversarial set, SOTA runner w/ machine-readable dimensions; level 3 | Ontology-structure P/R/F1 vs a golden reference only; its own docstring disclaims semantic correctness | **CDF, decisively** |

**Net shape:** CDF leads on everything that happens *after* the ontology
exists (execution, grounding, evaluation, optimization); COA leads on
*acquiring* the ontology (induction UX, foundational grounding, proposal
validation) and on **shipped** governance/agent/console surfaces. COA has no
answer-level trust story at all; CDF has no shipped enforcement or console.

---

## 4. Convergences — independent validation of our bets

AWS arrived, independently, at:

1. **Ontology as the agent-facing semantic layer**, with MCP as the agent door
   (their six tools ≈ our §10.2 semantic-layer plan).
2. **Auto-generated R2RML + Ontop** as the SPARQL↔SQL bridge (their induction
   emits R2RML exactly as our r2g does).
3. **Human-in-the-loop ontology review** (their proposal accept/reject ≈ our
   "confirm ~2%" step and AOE's curation).
4. **A deterministic tier before the LLM** (their T1 metric templates ≈ our
   prepared-question registry — and their motivation is the same: agents need
   reproducible answers).
5. **Constrained generation over the mapped vocabulary** (their
   `?class coa:isMapped true` requirement ≈ our catalog-scoped generation and
   validate-by-translation).
6. **OSI import/export** in their metric editor — the same open semantic
   interface r2g speaks. This is a genuine **interop surface** between the two
   systems (see §6).

When the largest cloud vendor ships your architecture three weeks after your
demo, the category thesis ("governed ontology layer for agents") is no longer
a thesis. The North Star's "Palantir space, done open and federated" now has a
second occupant — and their exit is a **closed managed service**, which
sharpens our open/portable positioning rather than blunting it.

---

## 5. Divergences that matter (and what they reveal)

1. **Translation vs execution.** COA treats SPARQL as a *query language
   veneer* over an Athena data plane. CDF treats the conceptual query as the
   *execution plan seed* — pushdown, bind-joins, per-leg budgets, provenance.
   Consequence: COA cannot cite what it did not execute; our envelope falls
   out of owning execution. This is an architectural moat, not a feature gap —
   they cannot bolt cite-or-refuse onto Athena delegation without rebuilding
   their data plane.
2. **T2.5 is the anti-pattern we named.** When ontology-mediated translation
   is low-confidence, COA falls through to **raw NL→SQL**, silently exiting
   the semantic layer — the exact "semantics re-interpreted per query" failure
   ADR-0002 and the North Star's translate-once principle exist to prevent.
   Our equivalent is a *refusal*, not a bypass. This is a talking point with
   teeth: their fallback un-governs precisely the queries that are hardest.
3. **Unstructured data: separate tier vs federated peer.** COA answers doc
   questions in a distinct RAG tier (Neptune+OpenSearch) that never joins
   structured rows. CDF's document graph is a *federated leg* joined on
   `account_id` in the same query with the same provenance ("green metrics,
   red sentiment" is unanswerable in COA as a single grounded query).
4. **Reasoning: they have some, we have none.** HermiT proposal-time
   consistency checking is real and useful (catches broken induced
   ontologies). It is not query-time reasoning (ELK is README-only), but it is
   a capability CDF lacks entirely — our ontologies are validated by naming
   convention and catalog integrity, not logic.
5. **Lock-in as product strategy.** 16 CDK stacks, six hard AWS service
   dependencies, one tested region — by design, feeding the managed-service
   funnel. Every enterprise with data outside AWS (or a multi-cloud mandate)
   is structurally out of their reach and inside ours: our Snowflake leg is
   native, our engine runs anywhere, and ArangoDB is the only stateful
   dependency.

---

## 6. What to borrow (concrete, mapped to our roadmap)

| Borrow | What they did | Where it lands here | Effort |
|---|---|---|---|
| **Foundational-ontology grounding** | Embed-and-match induced classes to FIBO/Schema.org/PROV-O; emit `closeMatch` for steward review | M3's cheapest real seed: ground CSI concepts the same way during onboarding; catalog stores the proposals | Small (AER already runs the embedding stack) |
| **Proposal-time consistency validation** | HermiT + DB-constraint-derived SHACL gate every ontology proposal | M2/AOE integration: run owlready2/HermiT over the synthesized ontology + r2g output in `catalog-integrity` | Small–medium |
| **Metric registry (their Tier 1)** | Named business metrics with stored SQL templates + synonyms — deterministic, governed | A "metric" concept class in the catalog; prepared questions generalize into a governed metric layer (dbt-semantic-layer territory) | Medium |
| **No-bypass SQL firewall** | Table/column/metric allowlists enforced below every role, admin included | Defense-in-depth *under* OBAC (ADR-0004): enforce at the leg compiler even if policy compilation has a bug | Small |
| **Delegated agent auth** | MCP agents act with the *user's* token (OIDC+PKCE), empty grant = deny | The M8/MCP GA design should adopt this posture verbatim | Design note now |
| **Steward console patterns** | Induction runner → proposal diff → accept/reject; Turtle editing with "your ontology is your data" | M14 console; validates our P3.6/P5 review-queue sequencing | Reference, not code |

And one **interop opportunity** rather than a borrow: both systems speak OSI
and R2RML. A demo that **imports a COA-induced ontology and federates it
beyond AWS** (Snowflake native + ArangoDB docs + cite-or-refuse) is the
sharpest possible counter-position: "keep their ontology, escape their
lock-in, gain provenance."

---

## 7. Threat assessment

- **Near term (this year): low direct threat, high narrative threat.** COA is
  a 3-week-old read-only mirror with reliability issues in its agent surface
  and no trust machinery. But "AWS has this" will now appear in every
  competitive conversation — sales needs a one-pager (see recommendations).
- **Medium term: the managed service is the real event.** AWS says COA becomes
  a managed **AWS Context** feature. When that lands, the induction UX,
  console, and governance will be polished, and distribution is unbeatable
  *inside AWS*. Our defensible ground, in order: (1) cite-or-refuse
  provenance across **real** federation, (2) multi-cloud/on-prem portability,
  (3) unstructured-as-federated-peer, (4) evaluation rigor as a trust
  product, (5) the graph-native hub (canonical entities, traversal,
  time-travel) which Neptune-as-GraphRAG-store does not attempt.
- **Watch items:** v0.3+ release cadence; whether execution moves inside
  (Ontop with data, or Athena provenance); any answer-eval harness appearing;
  the managed-service announcement; whether `closeMatch` grounding grows into
  real alignment.

---

## 8. Recommendations

1. **Write the sales/customer one-pager now** ("How is this different from
   AWS's Context Ontology Accelerator?") from §1/§5 — the question is already
   in the field; the §5.2 T2.5-bypass and translation-vs-execution points are
   the spine. *(Owner: Arthur; input: this report.)*
2. **Add the two small borrows to P3's backlog** (foundational grounding,
   HermiT proposal check in `catalog-integrity`) — both strengthen dimensions
   where the scorecard grades us 2.
3. **Prototype the OSI interop counter-demo** when a COA deployment is
   available to us (their ontology + our federation) — cheap if their OSI
   export is faithful; devastating positioning if it works.
4. **Freeze this comparison into the quarterly SOTA scorecard cycle** — COA is
   now a named competitor for the level-5 "independent bakeoff" rungs, and its
   ontology-structure benchmark is a public artifact we can actually run
   against (their golden_compare on our r2g output).
5. **Do not chase their surface area** (console, 6 MCP tools, 7 connectors) at
   the cost of the moat: provenance-bearing execution and evaluation rigor are
   the two dimensions they cannot reach from an Athena-delegating
   architecture without a rebuild.

---

## Sources

- https://github.com/aws/context-ontology-accelerator (repo, READMEs, `external-docs/content/*`, package docs, releases v0.1.0/v0.2.0, issues)
- https://aws.amazon.com/about-aws/whats-new/2026/07/aws-context--ontology-accelarator-generally-available/
- https://aws.github.io/context-ontology-accelerator/
- Third-party deploy/experience write-ups: Zenn (AWS Japan deploy guide), Serverworks overview, Zenn local-lab HermiT/Ontop/MCP experiment
- CDF: this repo @ `main` 2026-08-19; `docs/architecture/project-sota-scorecard.md` v1.2; `docs/contextual-data-fabric-product-strategy-prd.md`
