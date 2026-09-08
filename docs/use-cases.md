---
title: Contextual Data Fabric — Use Cases & Competency Questions
type:
  - internal
  - requirements
status: draft
version: 0.1
date: 2026-07-14
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/module-10-evaluation/specification|M10 Evaluation]]"
---

# Use Cases & Competency Questions (ORSD-style)

> **What this document is:** the living operationalization of [PRD §4](contextual-data-fabric-prd.md) — the locked competency questions the golden set (M10), the prepared-question registry (`deploy/questions.json`), and AOE's §6.19 CQ program are authored from. **Owned by PJ**; questions lock/evolve here on their own cadence — changes to this file do not rev the PRD, and the PRD's requirements never live here.

> **Derivation, not invention.** PJ confirmed (2026-07-14) there are no formal use-case definitions — the system was designed around **12 locked questions** he authored, each proven to require both graphs. This document formalizes what exists into the ORSD/competency-question shape the fabric has adopted (AOE PRD §6.19): personas, interaction model, use cases, and a CQ table. Sources: `customer-context` (`agent/test/questions.eval.test.ts`, `agent/src/index.ts` anchor prompts, `docs/PROJECT-SUMMARY-INTERNAL.md`), PRD §4's portfolio questions, and PJ's answers below.

## 1. Recorded decisions (PJ, 2026-07-14)

| Question | Decision |
|---|---|
| Formal use-case definitions? | None — the 12 locked questions **are** the requirements; this doc formalizes them. |
| Who is the user? | **An internal Arango employee using an internal tool** (CSM/AE/SA reasoning about accounts). Customer-facing agents remain the North-Star persona (PRD §4), not the design point. |
| Interaction mode? | **Natural language** in → cited answer out. No dashboard-first workflow. |
| Does a user pick data sources? | **No.** Given the option, users would select everything anyway (the AutoGraph-slider lesson). **Source selection is the ontology's job** — the mappings route each question to the sources that hold the answer (PRD §5.2). A per-question source picker is explicitly a non-feature. |

## 2. Personas

- **P-1 Account owner (primary, P1):** internal Arango CSM/AE/SA. Asks free-form NL questions about accounts they own; needs answers they can *defend* — every claim cited, refusal over guess.
- **P-2 Enterprise agent (North Star, P2+):** a programmatic agent consuming the fabric via the semantic MCP surface (PRD §10.2). Same contract as P-1, machine-shaped.
- **P-3 Ontology curator (supporting):** human (or policy-driven agent) who confirms the ~2% of alignment decisions (M3 / AOE FR-17.8) and blesses ontology expansions. Uses AOE's curation workspace, not the query UI.
- **Non-persona:** a "source selector." No user chooses which systems to federate — see decision table.

## 3. Use cases

Grouped from the locked questions (account arcs from `PROJECT-SUMMARY-INTERNAL.md`: Northwind = healthy expansion / true positive; Meridian = hidden risk / the false positive we catch; Helio = honest churn).

| UC | Use case | Questions | The insight only federation gives |
|----|----------|-----------|-----------------------------------|
| **UC-1** | **Account health & renewal risk** | **Q2** (lead), Q13 | Structured metrics + NPS *score* look fine, but Slack escalations / QBR flags / NPS *verbatim* surface renewal risk — the federation catch. |
| **UC-2** | **Champion / relationship engagement** | Q9 | CRM contact record + renewal context joined with email/Slack/meeting-notes attendance → "has our champion gone quiet?" |
| **UC-3** | **Commitments & delivery** | Q8 | Contract SLA + usage telemetry reconciled against promises made in email/Slack — including one never logged in the CRM. |
| **UC-4** | **Expansion / upsell readiness** | Q5, Q7 (structured-only anchor) | Edition/whitespace/usage thresholds + documented triggers (scale pain, ops burden, RAG intent) in unstructured sources. |
| **UC-5** | **Contraction / churn** | Q13, Q14, Q15 (structured-only anchor) | Downgrade ladder + declining telemetry + slipped renewal, corroborated (Q13/Q14) by sentiment decline. |
| **UC-6** | **Portfolio prioritization (cross-account)** | PRD §4's four CSM questions (prioritize attention; C-suite visit; product-feedback nominees; missing-info stack-rank) | Ranking across N accounts — P2 (M9 FR-4 portfolio scale). |
| **UC-7** | **Trust boundary (refusal)** | adversarial/out-of-scope set (`agent/test/adversarial.ts`) | Uncitable → clean refusal, zero fabricated `_id`s. A first-class use case, not an error path. |

## 4. Competency-question table

Verbatim texts recovered from the repo where they exist. ⚠ = text exists **only in PJ's uncommitted** `locked-questions-and-data-map.md` / `locked-questions-expected-answers.md` (referenced by `.planning` verification and `PROJECT-SUMMARY-INTERNAL.md` but never committed) — see §6 action.

| CQ | Account | Graphs | Text | Status |
|----|---------|--------|------|--------|
| ~~Q12~~ | Meridian | dual | the "usage green / sentiment red" contradiction question | **dropped 2026-08-04** — the sentiment/entity extraction it needed is deprecated; Meridian's risk is now carried by Q2 |
| Q2 | Meridian | dual | "Is Meridian Logistics at risk at their upcoming renewal, and WHY? Use their contract renewal date and usage trend together with the CSM Slack notes, renewal emails, and QBR documents that explain any risk." | locked, in repo |
| Q9 | Meridian | dual | "Is our champion at Meridian Logistics still engaged? Use the CRM contact record and renewal context together with their recent emails, Slack notes, and meeting-notes attendance to judge whether they have gone quiet." | locked, in repo |
| Q8 | Meridian | dual | "What did we promise Meridian Logistics, and did we deliver? Reconcile the contract SLA and product scope and the usage telemetry that shows delivery against any promise made in emails, Slack, or meeting notes — including a commitment that was never logged in the CRM." | locked, in repo |
| Q5 | Northwind | dual | "Is Northwind Analytics ready for an ArangoGraph or GenAI upsell? Use their edition, product whitespace, and usage thresholds from the structured graph together with any documented trigger (scale pain, ops burden, or RAG intent) in their Slack notes, success plan, and exec emails." | locked, in repo |
| Q7 | Northwind | structured-only anchor | "For Northwind Analytics, show how they have adopted ArangoDB across the product ladder (Community to Enterprise to ArangoGraph) and the ROI we have delivered…" | locked, in repo (`Q7_ANCHOR_PROMPT`) |
| Q15 | Helio | structured-only anchor | "For Helio Retail, summarize their product-tier history, current contract status, and usage trend over time…" | locked, in repo (`QC_ANCHOR_PROMPT`) |
| Q1, Q3, Q4, Q6, Q10, Q11 | — | dual (per the 12× two-clause proofs) | ⚠ not in repo (Q11 noted as the "second signature moment") | recover from PJ |
| Q13, Q14 | Helio | dual | ⚠ not in repo | recover from PJ |
| CQ-P1…P4 | portfolio | multi-account | PRD §4's four CSM questions | P2 scope |

## 5. How these drive the fabric (the CQ contract)

1. **Scope extraction (M2 FR-3 / AOE FR-19.4):** the CQ term set (account, contract, renewal, usage, NPS, champion, promise/SLA, escalation, …) is the required-concept list injected into ontology extraction. This *is* the "use-case-driven, never boil-the-ocean" mechanism.
2. **Golden set (M10 FR-1):** each CQ above becomes a golden-set entry: expected answer facts, expected sources touched, expected join entity (the account's canonical entity), expected citation shape; UC-7 entries assert refusal. The envelope contract + faithfulness ≥ 0.6 gates from `questions.eval.test.ts` carry over as the baseline.
3. **P1 demo slice (PRD §7.4, M5 plan F1):** propose **Q2** as the lead P1 question — in the fabric retelling, its structured leg moves from the hand-modeled Arango graph to **live Postgres via SQL pushdown**, which is exactly the "what does the fabric add" story. Q7/Q15 serve as single-leg smoke tests. *(Q12, the former centerpiece, was dropped 2026-08-04.)*
4. **Coverage validation (AOE FR-19.5):** once the ontology is derived, every CQ's test query classifies answerable / partial / unanswerable — the ontology-side gate that complements M10's answer-side gate.

## 6. Gaps & actions

- ~~**Recover the missing question texts**~~ — **RECOVERED (2026-07-17):** both docs now sit in `docs/questions_answers/` (local reference, deliberately git-ignored). Key facts they add: the locked set was a **6-question demo arc** (Q7 anchor → Q2 → Q12★ → Q9 → Q5 → Q8) + the Helio arc (Q13/Q14/Q15); **Q12 was dropped 2026-08-04, leaving a 5-question arc** (Q7 → Q2 → Q9 → Q5 → Q8); Q13/Q14 prompts and all expected-answer narratives + **eval-lock contracts** are now known; the cross-graph join is **document-level `account_id`** (deterministic, no runtime fuzzy matching); and there are **6 adversarial/refusal cases** (UC-7). The golden contracts get encoded into `cdf.eval.golden` (committed code) so the ignored directory never becomes a hidden dependency. Canonical upstream home (`customer-context/docs/research/`) is still empty — PJ may still want to push them there.
- **Formalize in AOE once §6.19 ships:** this doc's table becomes the `ontology_requirements` spec (FR-19.1); until then it lives here as markdown.
- **Portfolio questions (UC-6) have no locked texts or data maps yet** — author them when P2's portfolio scale arrives (M9 FR-4).
- **Persona gap to keep visible:** everything above is the *internal-employee* framing (PJ's design point). The the design partner pitch is the *enterprise-agent* persona — before Phase 2 demos, re-check each CQ still reads correctly when "we/our" means the customer's CSM org, not Arango.
