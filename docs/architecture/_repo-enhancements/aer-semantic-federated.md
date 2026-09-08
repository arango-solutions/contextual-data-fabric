---
title: "Repo Enhancement — arango-entity-resolution (AER) — Semantic + Federation-Aware ER"
repo: arango-entity-resolution
type:
  - internal
  - repo-enhancement-spec
status: active
version: 0.2
owner: PJ
serves_modules: ["06-entity-resolution"]
phase_intro: 2
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Repo Enhancement — AER: semantic + federation-aware entity resolution

> **Requirement (one line):** extend AER beyond deterministic matching to **semantic** matching, and toward **federation-aware** resolution (resolve against records fetched live from a source, not only pre-ingested ones), while keeping the guards/survivorship the fabric relies on.

## WP-13 delivery note (2026-08-05)

The precision-first canonical-hub service/API, native scoped Arango vector
provider, `fabric_canonical_hub` profile, and fixture gate are implemented as
new files in the local AER worktree. CDF's guarded adapter and evaluator are
also local. These additions are uncommitted and are **not** claimed to be on
either AER remote `main`; the release and CDF dependency pin remain pending.
M5 runtime-row integration is WP-14/P3, not part of this delivery.

## 1. Current state (verified against `~/code/arango-entity-resolution`, v3.5.1 — v0.2)
AER provides `CrossCollectionMatchingService` (blocking + Levenshtein/Jaro-Winkler → `resolvedTo` edges) and `WCCClusteringService`. In `customer-context` it is wrapped with a no-cross-account guard and per-domain authority-first survivorship + `whyLost` provenance (its own golden-record logic is deliberately not used). Matching in the current canonical-hub flow is largely deterministic/one-to-one.

**v0.2 correction — semantic matching largely exists already:** AER v3.5.1 ships **vector/ANN blocking** (sentence-transformers embeddings + native `APPROX_NEAR_COSINE`, ArangoDB 3.12+), phonetic + n-gram matching, geographic proximity, and **LLM match verification** that auto-escalates ambiguous pairs in the 0.55–0.80 confidence band — plus an MCP server (`[mcp]` extra) and a config-driven `ConfigurableERPipeline`. RE-1 is therefore mostly *configuration + demo-safety hardening*, not a build. Note also: AOE's internal ER is hand-rolled (full AER integration deferred on the AOE side), so the fabric will run two ER implementations until that converges — flagged in M6.

## 2. Why the change
Federated Customer 360 must resolve the same entity across heterogeneous sources where keys don't line up — deterministic exact-match isn't enough. Longer term, resolution must happen against data **fetched live** at query time (no pre-ingest), which is the federation model.

## 3. Required enhancements
- **RE-1 (P2, re-scoped v0.2):** ✅ **Implemented locally in WP-13** — precision-first semantic profile plus fixture evaluation with precision, recall, abstention, scope, and evidence metrics.
- **RE-2 (P2):** ✅ **API implemented locally in WP-13; release/pin pending** — clean read-only canonical-hub `resolve` service returning canonical ID or explicit abstention/refusal with evidence.
- **RE-3 (P3):** **Federation-aware ER** — resolve a record fetched live from a source against the canonical hub at query time, not only at ingest.
- **RE-4 (P2):** Preserve/first-class the guards + survivorship + `whyLost` provenance the fabric needs for citations (don't regress what `customer-context` added).

## 4. Interface contract (with M6)
- **Input:** candidate entity/record (attributes + source) + account/scope.
- **Output:** `canonical_id` (or abstain), plus match evidence/provenance for citation.

## 5. Phase mapping
- **P1:** reuse existing deterministic ER (no change required for the P1 demo).
- **P2:** RE-1, RE-2, RE-4.
- **P3:** RE-3.

## 6. Acceptance criteria (P2)
- Semantic matching resolves cross-source entities the deterministic path misses, at a precision bar safe for a live demo, with explainable evidence.

## 7. Open questions / for team
- Batch (pre-resolve) vs runtime (federation) placement — the industry-unsolved question; cost/latency implications. The PRD's B7 cost/latency baseline (P1) provides the first real numbers; loop in Kevin's when weighing runtime ER for P3.
- Precision threshold + evaluation harness before it's demo-safe.
