---
title: "ADR-0002 — Snowflake Cortex (and agentic sources generally) as federation legs"
adr: 0002
module: 05-federated-query-engine
status: accepted
date: 2026-07-21
deciders: ["Arthur Keen", "PJ"]
related:
  - "[[ADR-0001-conceptual-query-language|ADR-0001]]"
  - "[[contextual-data-fabric-prd|PRD]] §2.3, §7.7, §10.2"
---

# ADR-0002 — Should the fabric use Snowflake Cortex when federating?

**Status:** Accepted (the standing written answer to a question customers ask
in almost every conversation). The core decision — no Cortex; Snowflake takes
SQL directly — is implemented as the native `SnowflakeExecutor` (Option B,
landed 2026-07-22; see Consequences).
**Trigger:** the Snowflake sprint (PRD §7.7) made the question concrete.

## Context

**First, the fact that dissolves most of the question: Snowflake is a SQL
database.** Plain SQL goes in through JDBC/ODBC, the Python connector, or the
REST SQL API — exactly like Postgres, and that is how the fabric's Snowflake
leg works: the native `SnowflakeExecutor` compiles the SPARQL partition to
Snowflake SQL and sends it over `snowflake-connector-python` —
deterministic, token-free, self-cited. **A query prompt is never required to
query Snowflake.**

The prompt only enters with **Cortex Analyst** — an optional AI service
*layered on top of* the warehouse. Its API does not accept SQL; it accepts a
natural-language question plus a customer-authored *semantic model*, generates
SQL internally, executes it, and returns results along with the SQL it
generated. (Siblings: **Cortex Search** — retrieval over unstructured data
inside Snowflake; **Cortex Agents** — orchestration across both. And note the
naming trap: Cortex *LLM functions* such as `SNOWFLAKE.CORTEX.SENTIMENT()` are
themselves called **from SQL** — only Cortex **Analyst** speaks prompts.)

Two doors into the same warehouse:

| Path into Snowflake | You send | Deterministic | Cost per question | Citation |
|---|---|---|---|---|
| JDBC / connector / SQL API | **SQL** | yes | warehouse compute only | the SQL *we compiled* (derived) |
| Cortex Analyst | **an NL prompt** | no — LLM in the loop | compute **+ LLM inference** | the SQL *Cortex says it ran* (attested) |

So the customer question *"shouldn't the Snowflake leg just be Cortex?"*
(Databricks Genie poses the identical question) is really: *"should we put an
LLM between the fabric and the warehouse when a SQL door is standing open?"*
Choosing Cortex would mean the fabric **renders a query prompt** from the
partition — the "agentic partition" leg sketched in the README diagram and
M5's scope ("agent calls where a source only exposes an agent") — a leg type
designed for sources whose SQL door is *closed*, which Snowflake's is not.

## Decision

**Default: no — the Snowflake leg is the native `SnowflakeExecutor` (SPARQL→SQL,
compiled from the same r2g R2RML), the same class of deterministic SQL leg as
every relational source. Cortex is supported *in principle* as an agentic
connector type with explicitly degraded trust semantics, built only when a
customer mandates it.**

Four reasons, in the order customers feel them:

1. **Cost & latency (the political one — PRD §2.2).** Our compiled leg costs
   ~zero tokens and milliseconds per question, forever. A Cortex leg bills
   LLM inference per question on top of warehouse compute. the data-platform lead's objection to
   Arango was *token cost*; an architecture that puts an LLM inside every
   federated leg concedes that argument at the design level. (B7/CC-6 exists
   to prove our number.)
2. **Determinism & the gate.** `make gate` before every demo requires
   reproducible legs. Cortex output can vary run-to-run; our SPARQL→SQL is
   deterministic from the mapping. An LLM leg can never sit on the mandatory
   gate path (the same reason the D1 NL front-end is pinned off in the gate —
   `CDF_NL_DISABLED`).
3. **Semantics stay in one place.** The fabric's whole argument (PRD §2.1) is
   *translate once* at the ontology. If each source's cortex re-interprets
   language independently, the N² re-interpretation problem returns wearing an
   AI badge — two cortices can legitimately read "revenue at risk" two ways
   and nothing catches the divergence. If a Cortex leg is ever built, the
   prompt is **rendered from the already-decomposed conceptual partition**
   (concepts, filters, join keys made explicit in words) — never the user's
   raw question.
4. **Provenance quality.** Our legs cite the exact executed SQL/AQL.
   Cortex Analyst does return the SQL it generates — which is precisely what
   makes it the *acceptable* agentic source, partially rescuing provenance —
   but the fabric did not compile that SQL from the mapping, so the citation
   is **attested by the source's agent, not derived by the fabric**. That is
   a different trust class and must be labeled as such.

## The agentic-connector contract (when a customer mandates Cortex/Genie)

A conforming agentic leg MUST return:
1. **structured rows** (not prose) aligned to the partition's variables,
   including the join key (`accountId`);
2. **the query it executed** (Cortex Analyst's generated SQL) for the
   citation, marked `attested` rather than `derived`;
3. **an as-of stamp** (CC-4).

Envelope semantics: attested legs carry a visible **`agent-attested` trust
class** (M7 badge); a claim supported *only* by an attested leg cannot
silently satisfy a load-bearing projection under the strict gate — concierge
mode may accept it, declared. Guardrails (CC-11) apply unchanged: row caps,
timeouts, circuit breaker; plus a token budget per leg.

Build shape (estimate, unscheduled — P3 or on customer demand): a prompt
renderer from the partition + a semantic-model mapping (the Cortex semantic
model must be *generated from the same CSI* so vocabularies can't drift) +
a response adapter + trust-class plumbing in the envelope. Roughly a
one-week package once a real customer environment exists to test against.

## What we tell customers (the two-sentence answer)

> Snowflake takes SQL directly, so the fabric queries it the same way it
> queries Postgres — deterministic, no LLM tokens, and the citation is the
> exact query we compiled from your ontology. Cortex Analyst is an *optional*
> NL layer on top; the fabric can federate through it when you require that
> (it's the best-behaved agentic source, since it returns the SQL it
> generated) — but we label those legs `agent-attested` rather than derived,
> because your auditors will ask the difference.

## Consequences

- Sprint 2 (PRD §7.7) shipped the Snowflake leg as the native
  `SnowflakeExecutor` (Option B, landed 2026-07-22); Cortex stayed out of scope.
- **Mechanism note — Option B superseded Option A.** The leg was first planned as
  a *second Ontop endpoint* over the `snowflake-jdbc` driver (Option A). A native
  `SnowflakeExecutor` (`src/cdf/adapters/snowflake.py`) superseded it: it compiles
  the E1 BGP + the same r2g R2RML straight to Snowflake SQL over
  `snowflake-connector-python`, wired per `kind` in `FederationService.from_env`,
  sidestepping the JDBC/Arrow/Java-17 quirk. The core decision here — no Cortex;
  Snowflake takes SQL directly — is unchanged; only the SQL transport differs.
- The README's agentic-partition lane and M5's "agent calls" scope line now
  have a governing ADR instead of an implicit design.
- Revisit trigger: a customer engagement that mandates Cortex/Genie, or the
  P3 multi-source planner work (G5) — whichever comes first.
