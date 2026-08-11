---
title: "Module 09 — Demo Harness — Specification"
module: 09-demo-harness
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: PJ
building_block: "—"
depends_on_modules: ["05-federated-query-engine", "07-grounding-provenance"]
depends_on_repos: ["customer-context"]
requires_repo_enhancements: ["customer-context-expose-modules"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 09 — Demo Harness

> A thin agent UI to run the seed questions end-to-end and show the federated, cited answer + retrieval path. For demos and internal validation — **not a product we sell**.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
Make the fabric visible. Reuse the customer-360 Vercel app pattern: a free-form question box → the federated query engine → the cited envelope + traversal viz. Its job is to let the design partner (and us) *see it working*, which the customer explicitly demanded — not to be the customer's agent layer.

## 2. Scope
**In scope:** minimal question UI; wiring to M5 + M7; rendering citations (SQL + AQL + source objects) and the cross-source traversal; running the 1–3 seed questions.
**Out of scope:** being the sold agent application; complex agent orchestration (we show a thin layer we don't sell).

## 3. Interfaces (inputs / outputs)
- **Consumes:** M5 (federate) + M7 (cited envelope).
- **Produces:** the rendered answer + citations + traversal for a human.

### 3.1 Editor vocabulary — v1 (named contract)

The syntax-directed SPARQL editor (`deploy/demo/editor.js`, rendered into the
demo page by `deploy/demo/server.py`) is generic over one JSON payload:

```json
{
  "base": "urn:arango-sparql:concept#",
  "classes": {
    "Account": {
      "props": ["accountId", "accountName", "..."],
      "source": "postgresql:crm",
      "kind": "postgresql"
    }
  }
}
```

- **Producer:** the demo server builds it from `SourceCatalog.vocabulary()` —
  the *same* class-structured grounding `nl2sparql`'s system prompt uses. That
  identity is the point: the human's completions and the LLM's grounding must
  never disagree about which properties a class owns (the per-class grouping
  exists precisely so a property can't be attached to the wrong class and
  silently return an empty answer).
- **Consumer:** `editor.js` — highlighting (concepts tinted by owning source,
  unmapped concepts flagged as will-refuse), syntax-directed completion in
  both `c:Name` and full `<base…Name>` spellings, pairing, and
  `formatSparql()` (whitespace-only pretty-printer for the E1 subset).
- **Push-down plan** (agreed 2026-08-08, tracked as an `arango-sparql-py`
  issue): the editor stays app-side while it iterates at demo speed. The
  trigger to move it is a **second consumer** (lib workbench, nl2sparql
  playground, or an Arango AI Suite surface). Landing spot:
  `arango_sparql/static/editor.js` + an `editor_vocab()` helper on the
  resolver, versioned with the lib and consumed here through the CC-9 pin;
  the node-driven editor/formatter tests travel with it. Until then, any
  change to this payload's shape bumps the contract name (v1 → v2) here and
  in the issue.

## 4. Functional requirements
- **FR-1 (P1):** Free-form question → federated answer for the seed use cases, with citations and the cross-source retrieval path shown.
- **FR-2 (P1):** Pre-run/"in the interest of time" mode (avoid drawing attention to latency — per the sales feedback in [[2026-07-09 - C360 field-feedback review]]).
- **FR-3 (P2):** Show cost/latency panel when the customer is ready for that conversation.
- **FR-4 (P2):** Portfolio-scale / cross-account questions.

## 5. Non-functional requirements
Reliable in front of a customer (a confident wrong or flaky demo is the failure mode); reuse existing UI rather than rebuild.

## 6. Dependencies
- **Repos:** `customer-context` (the Vercel app + citation/traversal UI); see [[contextual-data-fabric/docs/architecture/_repo-enhancements/customer-context-expose-modules|enhancement]].

## 7. Phase mapping
- **P1:** 1–3 seed questions end-to-end.
- **P2:** cost/latency panel; portfolio-scale questions.
- **P3:** —

## 8. Acceptance criteria / demo (P1)
- Typing a seed question returns a federated, cited answer with the two-source retrieval path visible, reliably, in the demo environment — **defined in [[contextual-data-fabric/docs/architecture/deployment-p1|the P1 deployment topology]]** (four live processes on one host: Postgres + ArangoDB via docker-compose, the M5 engine, this UI; AOE/RSA/r2g are build-time only).

## 9. Open questions
- How much of the existing customer-360 app is reused vs trimmed for the fabric demo.
