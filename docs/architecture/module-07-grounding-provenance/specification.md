---
title: "Module 07 — Grounding & Provenance — Specification"
module: 07-grounding-provenance
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: PJ
building_block: Query
depends_on_modules: ["05-federated-query-engine"]
depends_on_repos: ["customer-context"]
requires_repo_enhancements: ["customer-context-expose-modules"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 07 — Grounding & Provenance

> Wrap every answer in a **validated, cited envelope** whose retrieval path spans the whole federation — the actual SQL, AQL, and source objects — and **refuse** rather than guess when a fact can't be cited.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
Our differentiator, extended across the federation boundary. Takes the retrieval path from M5 and produces the `{answer, claims, citations[], retrievalPath[]}` envelope, with a **deterministic grounding gate** that refuses the LLM an answer if citations are missing. This is what separates us from pure A2A and from black-box RAG. Reuses the grounding gate + citation UI already built in `customer-context`.

## 2. Scope
**In scope:** validated answer envelope; per-claim citations; retrieval-path assembly spanning SQL + AQL + source objects; the refuse-if-uncited gate; the traversal/citation visualization.
**Out of scope:** generating the queries/plan (M5); resolving entities (M6).

## 3. Interfaces (inputs / outputs)
- **Consumes:** answer payload + retrieval path from M5 (every sub-query, its text, and source objects).
- **Produces:** the validated cited envelope for the UI/agent; a refusal when any claim is uncitable.

## 4. Functional requirements
- **FR-1 (P1):** Emit the validated envelope with per-claim citations for a federated (Postgres + Arango) answer.
- **FR-2 (P1):** Citations include the **actual SQL and AQL** and the source object (table/collection/doc) — across the federation boundary.
- **FR-3 (P1):** **Deterministic grounding gate** — refuse if any claim lacks a citation (reuse existing gate).
- **FR-4 (P1):** Retrieval-path visualization spanning both sources (reuse the customer-360 traversal viz).
- **FR-5 (P2):** **Cost/latency** annotations on the envelope (tokens, wall-clock, per source) — to answer the customer's cost objection with data.
- **FR-6 (P1):** **Partial-failure rendering** (PRD §10.5 / CC-5) — when M5 reports a failed leg, render the partial answer with the failed leg explicitly declared (the partially-grounded badge); render a clean refusal when the failed leg was load-bearing. Never present a partial answer as complete.
- **FR-7 (P1):** **As-of timestamps on citations** (PRD §10.4 / CC-4) — each citation shows when its leg was current (live query time vs last-ingest time), so temporal skew between federated sources is visible, not hidden.

## 5. Non-functional requirements
No confident-wrong-answers (refuse over guess); citation path complete across the federation; trust is structural.

## 6. Dependencies
- **Repos:** `customer-context` — the grounding gate, envelope schema, and citation/traversal UI; see [[contextual-data-fabric/docs/architecture/_repo-enhancements/customer-context-expose-modules|enhancement]] to expose these as reusable modules.

## 7. Phase mapping
- **P1:** cited envelope + gate + viz across 2 sources.
- **P2:** cost/latency annotations.
- **P3:** —

## 8. Acceptance criteria / demo (P1)
- A federated answer renders with citations that show the real SQL + AQL + source objects; asking something uncitable yields a clean refusal (the partially-grounded/refuse badge).

## 9. Open questions
- Envelope schema extensions needed for federated (multi-source) citations vs the current single-graph shape.
