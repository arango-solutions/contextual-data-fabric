---
title: "Repo Enhancement — customer-context — Expose Unstructured Graph, Grounding Envelope & AQL Generation"
repo: customer-context
type:
  - internal
  - repo-enhancement-spec
status: draft
version: 0.1
owner: PJ
serves_modules: ["01-connectors", "05-federated-query-engine", "07-grounding-provenance", "09-demo-harness"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Repo Enhancement — customer-context: expose the unstructured graph, grounding envelope & ontology-driven AQL

> **Requirement (one line):** refactor the [[Customer360]] v3 pipeline so its **unstructured graph**, its **grounding/citation envelope + gate**, and its **traversal UI** are consumable as **modules** by the fabric — and add **ontology-driven AQL generation** so the query engine can federate over it.

## 1. Current state (verified against `~/code/customer-context`, cloned from `arango-solutions/customer-context` — v0.2.1)
> **Step zero is done:** the repo is public, cloned, and actively developed (latest commits: phase 04.3 coref/leak-gate hardening). Verified:
> - **v3 ingestion pipeline exists** (`ingestion/`): `span_gate.py`, person-coref (+tests), `er_match.py` / `er_cluster.py` importing the **actual AER library** (`entity_resolution.services.cross_collection_matching_service`, `WCCClusteringService`), survivorship + authority tables (`gold_source_key.py`, `test_survivorship.py`), `resolve_run.py`, `pipeline.py`. The over-merge guard exists as the **`account_scope_key` scope stamp** ("RESOLVE-05 over-merge foundation") derived from observable source metadata only.
> - **Grounding + envelope exist as named modules** (`agent/src/{grounding,envelope,retrievalPath,rrf,sanitize,embed,db}.ts`): a **pure-code grounding gate** ("every citation is a real `_id`"), a **typed zod envelope** (`ClaimSchema`, `CitationSchema`, `NodeDetail`, edge kinds), one agent factory feeding both request/response and streaming paths.
> - **UI exists** (`web/`, Next.js): `RetrievalPipeline`, `SourcingRail`, `GraphViz`, `WhatChangedBanner` components + tests; `DEPLOY.md` covers deployment.
> - **Agent**: AI SDK 6 `ToolLoopAgent`, temperature 0, curated read-only bind-parameterized AQL tools — matching the "deterministic traversals, no black box" story.
> - **Confirmed limitation for M7:** the envelope's `GraphEnum` is `['structured','unstructured']` — a *single-deployment, two-graph* shape. Multi-source federated citations (actual SQL + external source objects) require the schema extension RE-2 describes; M7's open question is real, not hypothetical.
> - As assumed by RE-5: the structured graph is **hand-modeled** (deliberately; AutoGraph builds the unstructured KG at build time only).

`arango-solutions/customer-context` is the v3 Customer 360 pipeline: connectors/chunking → LangGraph extractor (A-box against a hand-authored T-box) → span gate → person coref → embeddings (BM25 + vector) → **AER** entity resolution → survivorship, plus a grounded/cited answer envelope, a deterministic grounding gate, and a Vercel citation/traversal UI. Today it is a self-contained app with a hand-modeled structured graph.

## 2. Why the change
The fabric reuses three assets from this repo — the **unstructured graph** (as a federation source, M1/M5), the **grounding envelope + gate** (M7), and the **demo UI** (M9). They need to be callable modules rather than app-internal, and AQL over the unstructured side must be **driven by the master ontology/mappings** rather than curated per question.

## 3. Required enhancements
- **RE-1 (P1):** Expose the **unstructured graph** as a federation source with an `execute(AQL)` surface (consumed by M1/M5).
- **RE-2 (P1):** Extract the **grounding gate + validated envelope schema** as a reusable module (M7), extended to carry **multi-source** citations (SQL + AQL + source object).
- **RE-3 (P1):** Extract the **traversal/citation UI** for the demo harness (M9), able to render a cross-source retrieval path.
- **RE-4 (P1):** **Ontology-driven AQL generation** — generate the unstructured-side AQL from the master ontology/mappings (M4) instead of hand-curated queries.
- **RE-5 (P2):** Retire/parameterize the hand-modeled structured graph in favor of the federated Postgres/Snowflake path (the structured side becomes federated, not mirrored).

## 4. Interface contract
- **Provides:** AQL execution over the unstructured graph; `groundedEnvelope(retrievalPath) -> {answer, claims, citations[], retrievalPath[]}`; a UI component for cross-source paths.
- **Consumes:** master ontology + mappings (M4) for AQL generation.

## 5. Phase mapping
- **P1:** RE-1..RE-4 (the demo runs on this + Postgres).
- **P2:** RE-5.

## 6. Acceptance criteria (P1)
- The query engine federates over the customer-context unstructured graph via generated AQL, and answers render through the reused grounding envelope + UI with cross-source citations.

## 7. Open questions / for PJ
- How much of the current app is refactored into modules vs wrapped in place for P1 speed.
- Envelope schema changes for multi-source citations (coordinate with M7).
