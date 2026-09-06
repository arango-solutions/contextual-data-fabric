---
title: "RD-5 — Deployment-Requirements Interview Kit"
type:
  - internal
  - discovery-instrument
status: draft (S1 deliverable; memo due by S4)
date: 2026-09-06
owner: Arthur (interviews); findings feed PRD §12 RD-2/RD-5 and CC-8
related:
  - "[[contextual-data-fabric-prd|PRD]] §12 (readiness ladder), §10.8 (CC-8 owner-side topology)"
  - "[[customer-qa|Customer Q&A]] (the the design partner engagement — first datapoints)"
---

# RD-5 — Deployment-Requirements Interview Kit

> One conversation per prospect, ~45 minutes, same questions every time so
> answers are comparable. The output is a filled **answer sheet** (template at
> the bottom), accumulated into the RD-5 deployment-requirements memo (due S4).

## What we are trying to falsify

**H1 (owner-side extraction, CC-8 §10.8):** data owners will not let a central
application sample their databases and schemas; extraction (RSA/AOE/r2g) must
run at the owner's side, under their credentials, shipping only curated
contracts to the fabric. *The a federal prospect signal says yes; one signal is an
anecdote.*

**H2 (curation is mandatory, RD-1/RD-2):** owners will insist on reviewing and
editing the extracted ontology and controlling which entities are offered at
all — an uncurated auto-extraction will be rejected regardless of quality.

**H3 (evaluation shape):** customers will evaluate in an isolated sandbox with
a copy or subset of real schemas, not synthetic data and not production.

## The questions

### A. Who owns what (10 min)
1. Who owns the source systems you'd federate — one team or many? Who would
   have to say *yes* for us to read a schema? For the data?
2. Is there a data-platform / governance team that gates every integration?
   What do they require today (reviews, DPIAs, security questionnaires)?
3. Have you ever rejected a tool because it wanted to introspect your schemas
   from outside? What was the objection, verbatim?

### B. Topology & connectivity (10 min)
4. Where do the sources live (cloud/on-prem/VPC-peered SaaS)? Is there any
   network path from a central app to all of them, even in principle?
5. If a component had to run *inside* your boundary next to a source, who
   deploys and operates it — your team or ours? Container, VM, k8s?
6. What leaves your boundary today, for any vendor? (Telemetry? Metadata?
   Nothing?) Where is the line between "schema" and "data" for you?

### C. Ontology & curation (10 min)
7. Who in your org would review an extracted ontology — a data architect,
   a domain owner, a steward team? Would they want to edit it or approve it?
8. When two systems disagree about an entity (customer vs client vs account),
   who arbitrates today? Is there a canonical model anywhere (dbt, catalog,
   MDM) we should treat as ground truth?
9. Are there entities/properties that must never be visible to a query layer
   at all (not masked — absent)? Who maintains that list?

### D. Credentials, secrets & audit (5 min)
10. How do service credentials get issued and rotated (Vault, cloud secrets
    manager, manual)? Would you grant a read-only service identity per source?
11. What audit trail do you need for a system that reads across sources —
    per-query provenance, access logs, both?

### E. Scale, freshness & evaluation (10 min)
12. Ballpark: how many sources, tables, and rows are in scope for a first
    real use case? How fast do schemas change?
13. How stale can an answer be before it's wrong for your use case —
    seconds, hours, "as of last night"?
14. If we gave you a sandbox build tomorrow, what would YOU point it at to
    judge it — and what result would make it a yes?
15. Who pays and who operates, if this goes to production — your platform
    team, a business unit, us as a service?

## Answer sheet (copy per interview)

| Field | Answer |
|---|---|
| Org / interviewee / role / date | |
| H1 verdict (owner-side extraction required?) | supports / contradicts / n.a. — quote: |
| H2 verdict (curation mandatory?) | supports / contradicts / n.a. — quote: |
| H3 verdict (sandbox evaluation shape) | supports / contradicts / n.a. — quote: |
| Hard constraints heard (verbatim) | |
| Deployment shape they described | |
| Named blockers to a pilot | |
| Surprises (things no hypothesis covered) | |

## Rules of use

- Record **verbatim quotes** for anything that sounds like a requirement —
  paraphrases become fiction by the third retelling.
- A hypothesis needs **three independent supports** before it graduates into
  the PRD as a requirement; one contradiction is enough to open a question.
- File each sheet under `docs/research/interviews/` (git-ignored if the
  prospect asks; summarized into the memo either way).
