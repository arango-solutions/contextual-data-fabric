---
title: "Module 06 — Entity Resolution / Canonical Hub — Specification"
module: 06-entity-resolution
type:
  - internal
  - module-spec
status: active
version: 0.2
owner: PJ
building_block: Both
depends_on_modules: ["01-connectors", "05-federated-query-engine"]
depends_on_repos: ["arango-entity-resolution", "customer-context"]
requires_repo_enhancements: ["aer-semantic-federated"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 06 — Entity Resolution / Canonical Hub

> Resolve the same real-world entity across sources into a **canonical entity** in Arango, so a federated answer can join structured + unstructured facts about one thing.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## Implementation status (2026-08-05)

**P2.3 WP-13 and CDF WP-14 are implemented locally.** AER has
a new precision-first, read-only semantic canonical-hub service, native Arango
vector candidate provider, named `fabric_canonical_hub` profile, and fixture
precision gate. CDF has an independent guarded wrapper, stable JSON-safe
contracts, lazy optional AER adapter, a versioned evaluation corpus/CLI, and
runtime source-row normalization before federation seeds, joins, telemetry row
counts, and assembled materialization.

The AER additions are not claimed to exist on either remote `main`: they are
uncommitted local additions. CDF therefore does not pin AER yet; the integration
pin remains pending a clean AER release. WP-14 depends only on CDF's injected
resolver protocol and is disabled unless strict per-source catalog bindings and
an operator-owned resolver factory/injection are both configured. The generated
demo manifest remains `mode: none`.

## 1. Purpose & responsibility
The join fabric. When the query engine pulls a fact from Postgres and a signal from the Arango graph, this module guarantees they're about the *same* account/person/contract via a **canonical entities** collection. The WP-13 read path wraps AER with independent scope, oracle, threshold, margin, deadline, and evidence guards in CDF. Existing survivorship and `whyLost` write-path behavior remains separate and is not invoked by this read-only API.

## 2. Scope
**In scope:** cross-source matching (blocking + similarity), clustering into canonical entities, no-cross-account guard, survivorship/provenance for citations, the canonical-hub collection the query engine joins through.
**Out of scope:** query decomposition (M5); ontology/mappings (M2–M4); the citation envelope format (M7).

## 3. Interfaces (inputs / outputs)
- **Consumes:** entities/records surfaced by connectors + the unstructured graph.
- **Produces:** canonical entities + `resolvedTo` linkages, and a `resolve(entity) -> canonical_id` surface the query engine uses to join across sources.

## 4. Functional requirements
- **FR-1 (P1):** Reuse AER + the `customer-context` canonical-hub pattern to resolve entities between the Postgres side and the Arango unstructured graph for the seed use cases.
- **FR-2 (P1):** No-cross-account over-merge guard (fail closed).
- **FR-3 (P1):** Survivorship + `whyLost` provenance available for M7 citations.
- **FR-4 (P2):** ✅ **WP-13 local/API-ready** — semantic canonical-hub lookup with precision-first abstention, no-cross-account enforcement, and complete score/margin/field/vector evidence. Clean AER release + CDF pin remain pending.
- **FR-5 (P3):** ✅ **WP-14 local** — federation-aware ER resolves allowlisted
  observations from live source rows, rewrites only the configured join binding
  to a pattern-validated canonical ID, and removes every unresolved native key
  before downstream federation.

## 5. Non-functional requirements
Precision-first (over-merge in front of a customer is the failure mode); every merge explainable (provenance for citations); no bulk data movement (resolve on the keys/attributes needed).

## 6. Dependencies
- **Repos:** `arango-entity-resolution` (current released baseline v3.5.1; WP-13 additions are local and unreleased) — see [[contextual-data-fabric/docs/architecture/_repo-enhancements/aer-semantic-federated|enhancement]]; `customer-context` (existing canonical-hub + survivorship). CDF deliberately has no dependency pin to the uncommitted API. Also: **AOE's internal ER is hand-rolled** (its full AER integration is deferred), so two ER implementations coexist in the fabric — AER for the canonical hub (this module), AOE's for extraction-time concept dedup. Acceptable short-term; converge on AER when AOE's integration lands.

## 7. Phase mapping
- **P1:** reuse existing deterministic ER + canonical hub.
- **P2 / WP-13:** semantic service/API, independent CDF guard, and offline quality gate (implemented locally; release/pin pending).
- **P3 / WP-14:** ✅ federation-aware invocation on M5 runtime rows, with
  bounded calls/batches/deadline, duplicate suppression, fail-closed scope
  guards, safe partial semantics, and value-free evidence/metrics.

## 8. Acceptance criteria / demo (P1)
- A seed question joins a Postgres account fact with an unstructured sentiment signal via a single canonical entity; the merge is guarded and explainable.
- WP-13 fixture gates hold 1.0 precision and zero cross-scope resolutions while recall and abstention are reported separately; no model or live ArangoDB is required for the unit gate.

## 9. Open questions
- Batch (pre-resolve) remains preferred where mappings can materialize canonical
  keys; WP-14 provides the bounded runtime path for sources that cannot.
- Threshold/quality bar for semantic matching before it's demo-safe.
