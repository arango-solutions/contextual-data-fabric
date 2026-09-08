---
title: "Repo Enhancement — ontology-extractor (AOE) — Structured Input + Alignment/Belief APIs"
repo: ontology-extractor
type:
  - internal
  - repo-enhancement-spec
status: draft
version: 0.1
owner: Arthur Keen
serves_modules: ["02-ontology-extraction", "03-ontology-alignment", "08-governance-obac"]
phase_intro: 1
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Repo Enhancement — ontology-extractor (AOE): structured input + alignment/belief APIs

> **Requirement (one line):** confirm/complete the **structured-data → ontology** path, and expose **alignment**, **belief-management**, and **time-travel** as APIs the fabric's M2/M3 (and later M8) can call.
>
> **This is the make-or-break dependency** flagged in the [[design-partner feedback summary]] — gate Phase-1 commitments on RE-1.

## 1. Current state (verified against `~/code/arango-ontoextract`, v0.2)
AOE is substantially further along than v0.1 assumed:
- **Structured→ontology: Done.** SQL schemas and ArangoDB collection schemas map to OWL/SHACL classes/properties/constraints. The ownership split is documented in AOE's README: *"AOE owns the SQL→OWL/SHACL mapping; `relational-schema-analyzer` is a read-only physical-schema introspector."*
- **Belief revision: Built** (PRD §6.16, ADR-008): per-concept touchpoint discovery, mechanical verdicts (REINFORCED / REFINED / GAP-FILLING / REDUNDANT / CONTRADICTED / UNCERTAIN), LLM revision only for contested verdicts, Levi-identity contraction/expansion on the temporal substrate, Revisions Inbox, consolidation passes, and **6 dedicated MCP tools**.
- **Time-travel: Done** — VCR timeline, point-in-time snapshots, diff endpoints; staging→production promotion with temporal versioning.
- **Multi-source alignment: Not built.** No primitive merges N source ontologies into a reconciled master. Building blocks exist (effective-graph union + import conflict flagging, cross-tier overlap-candidate finder, pairwise class merge) but no orchestration — this is the fabric's M3 gap and the subject of `arango-ontoextract/docs/multi-source-alignment.md`.
- Also shipped: LLM-as-judge scoring, SHACL, JWT+RBAC, observability (structlog/Prometheus/OTel), MCP server (18 workspace tools). AOE's internal ER is hand-rolled; full AER library integration is deferred (relevant to M6 — two ER implementations will coexist).

## 2. Why the change
Customer Ask #1 is "structured data in, ontology out" and auto-derivation across **both** structured and unstructured sources. The fabric needs a single extraction surface (M2) and an alignment surface (M3) that treat structured and unstructured uniformly, plus the belief/time-travel machinery for change management (M3/P3).

## 3. Required enhancements *(re-scoped v0.2 against verified state)*
- **RE-1 (P1) ~~★~~ RESOLVED → wiring:** structured→ontology **exists** (see §1). Remaining work: wire the **RSA metadata bundle → AOE SQL→OWL/SHACL mapping** handoff so M2 has one surface, and confirm element provenance flows through it.
- **RE-2 (P1 minimal / P2 full) ★ — the real build, now definitized (v0.3):** **Alignment API** — AOE has committed this as **PRD §6.17 (FR-17.1–FR-17.13, Stream 20, v1.2.2)** with a SOTA-grounded design: embedding candidate retrieval → multi-signal scoring (reusing the §6.7 ER scorer) → **selective LLM adjudication of borderline pairs only** (MILA/GenOM pattern), minimally-destructive incoherence repair, master materialization with `owl:equivalentClass` provenance, **bounded human confirmation (≈2%, per this fabric's M3)**, plus REST + MCP surfaces (`align_ontologies`, `materialize_master`, …) and an evaluation harness (P/R/F1 + human-effort curve). AOE's §6.17 P1 slice targets exactly the fabric's small use-case-scoped master; the hand-constructed master remains the fabric's schedule hedge. See also `arango-ontoextract/docs/IMPLEMENTATION_PLAN_ALIGNMENT_ABOX_CQ.md`.
- **RE-3 (P2):** Iterative/cyclic refinement across ≥2 structured + unstructured, callable programmatically (agent-curatable) — the loop around RE-2.
- **RE-4 (P3, narrowed v0.2):** Belief revision on *new evidence* is **built** (§1). The remaining gap is the **source-change cascade**: a source schema/document *update or deletion* propagating retractions/revisions through dependent ontology elements (today revisions trigger from extraction runs, not source lifecycle events). Spec: same AOE doc, Part B.
- **RE-5 (P3, narrowed v0.2):** Time-travel **exists** (VCR, snapshots, diffs). Remaining: **programmatic change-control hooks** — bless-before-release on ontology expansions, callable by agent or human per policy (the staging→production promotion flow exposed as an API gate).
- **RE-6 (future):** SHACL/constraint export for governance (M8) — SHACL handling exists in AOE; the export contract for M8 is the future work.
- **RE-7 (P1, new 2026-07-18):** **OWL naming convention (CC-12)** — extraction prompts instruct the W3C-community style (classes singular PascalCase, properties lowerCamel), and extracted/curated ontologies are **validated with the same `validate_csi` naming check** the analyzers use, so AOE-derived and analyzer-derived ontologies are uniformly named. Naming violations surface in the existing quality-judge/curation flow, not as silent output.

## 4. Interface contract (with M2 / M3)
- **Input:** metadata bundle (structured, from M1) or corpus handle (unstructured).
- **Output:** source ontology (OSI/YAML) + provenance; alignment ops return a master + change log/belief state.

## 5. Phase mapping
- **P1:** RE-1 (★), RE-2 (minimal).
- **P2:** RE-3.
- **P3:** RE-4, RE-5; RE-6 future.

## 6. Acceptance criteria (P1)
- Feeding the Phase-1 Postgres metadata bundle yields a reviewed source ontology with provenance; the alignment API merges it with the unstructured ontology into a small master.

## 7. Open questions / for Arthur
- ~~Does AOE already do structured→ontology, or is that r2g's job?~~ **Answered (v0.2):** AOE owns the SQL→OWL/SHACL mapping; RSA is the read-only physical-schema introspector; r2g is the reference application composing RSA (its Phase 10 adds LLM-assisted derivation + review UI). The split is documented in AOE's README.
- ~~Which repo copy to build against?~~ **Answered (v0.2):** `~/code/arango-ontoextract`.
- ~~Alignment algorithm choice for RE-2~~ **Answered (v0.3) by AOE PRD §6.17:** embedding-retrieval-then-selective-LLM (MILA/GenOM pattern) with classical matchers as optional ensemble anchors and modular incoherence repair — not greedy-vs-cluster; the borderline band goes to the LLM/human, the rest auto-resolves.
- **New AOE capabilities the fabric should exploit (v0.3):** **§6.18 A-box extraction** (schema-grounded, EDC-style, span-level provenance, per-domain routing) — a future upgrade path for the unstructured instance-extraction the fabric currently gets from `customer-context`; and **§6.19 competency-question-driven requirements** (ORSD/CQ methodology) — adopted by fabric M2 (use-case scoping via FR-19.4 scope injection) and M10 (CQ coverage validation ↔ golden set).
