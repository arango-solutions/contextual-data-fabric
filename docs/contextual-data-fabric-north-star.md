---
title: Contextual Data Fabric — North Star
categories:
  - "[[Projects]]"
type:
  - internal
  - vision
date: 2026-07-13
org:
  - "[[Arango]]"
project:
  - "[[Arango Contextual Data Fabric]]"
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[Customer360]]"
  - "[[design-partner]]"
  - "[[2026-07-13 design-partner customer-context roadmap]]"
topics:
  - "[[Ontology]]"
  - "[[Graph]]"
status: draft
version: 0.2
---

# Contextual Data Fabric — North Star

> **Purpose:** define the *end goal*. The [[contextual-data-fabric-prd|PRD]] is phased and near-term; this document is the fixed point on the horizon that every phase should ladder toward. When a scope decision is ambiguous, check it against this.
>
> **Status:** Draft v0.1 for team alignment.

---

## The North Star (one sentence)

> **Every agent in the enterprise consults Arango first as its governed, semantically-normalized brain — one ontology that spans all systems, answers or routes any question with a cited retrieval path, enforces access and business rules in one place, and evolves itself as the data changes — without moving the data.**

---

## The end state

When the Contextual Data Fabric is fully realized:

- **One ontology, many sources.** A single master conceptual model spans every warehouse, lake, app, and unstructured corpus. `customer account`, `client account`, and `account` are the *same* concept everywhere. New sources are absorbed by extracting their local ontology and **aligning** it into the master — automatically, use-case-driven, never boil-the-ocean.
- **Ask anything, in English, across everything.** A question hits the ontology; the ontology's functional mappings decide which systems hold the answer, decompose the query, run the parts (SQL pushdown, AQL, agent calls), and reassemble — **without copying the data into Arango**. Arango holds the ontology, the resolved entities, and the linkages — not the raw data (the PubMed/NIH ~16 TB metadata-graph model).
- **Every answer is grounded.** The retrieval path spans every source touched — the actual SQL, AQL, and source objects — and the system refuses rather than guess. Trust is structural, not promised.
- **Governance lives in the ontology.** Business rules, security, and data access are defined once, on the ontology, and inherited by every agent — PII flagged in one place propagates across all 20+ systems (the Palantir pattern, via declarative mappings instead of bespoke scripts).
- **The ontology is alive.** It watches the sources: schema and document changes cascade through belief management, versions are time-travelable, and expansion is change-controlled — curated by agent or human per policy.
- **Composable, not monolithic.** Customers assemble the pieces they need (ontology extraction, alignment, federated query, governance) as building blocks — LLM or deterministic — rather than buying one rigid product.

---

## Why this is the goal (the strategic thesis)

- **The ontology is the product; the graph is where it lives and does its job.** Document ingestion on its own is low-value. Normalizing meaning, enforcing governance, and routing agents across a federation — *that* is the defensible value, and it needs a graph.
- **Agent-to-agent alone doesn't scale.** Direct A2A across N systems is an **N² translation problem** and forces every rule onto every edge. An ontology makes it a **wagon wheel** — translate once to a shared representation, govern once, in the center.
- **This is the Palantir/Cambridge-Semantics space, done open and federated.** Not "another data layer" — the **brain** every agent consults first. This is why the org named it the highest-value thing it can deliver ([[Project Vantage]]). Precedent already exists internally (the JLR "contextual data fabric" case-file assembly Arthur built).

---

## What "winning" looks like (how we'll know we're there)

- An enterprise points the fabric at a new source and gets a **reviewed, aligned ontology** with only a small human confirmation step — no hand-modeling.
- Agents across **multiple** teams stop re-defining the same metrics and instead consult **one** shared context layer.
- A cross-source question returns a **correct, fully-cited** answer whose path spans structured *and* unstructured sources, live — and it's **cheaper and lower-latency** than the fragmented ("Frankenstack") or naive-A2A alternative (directly answering the cost/latency objection that is currently our biggest customer headwind).
- Governance changes (e.g. flag an attribute as PII) take effect **everywhere at once** because they're defined on the ontology.
- New capabilities ship as **independently adoptable blocks**, and customers build their ~20% of agent logic on top of our ~80%.

---

## Guiding principles

1. **Don't move the data.** Federate by default; materialize only when analytics demand it.
2. **No black boxes.** Every answer is grounded and cited across the federation boundary, or it's refused.
3. **Use-case-driven ontology.** Scope to the questions that matter; never boil the ocean.
4. **Governance in the ontology, not in every agent.** Define rules once.
5. **Composable building blocks.** Independently publishable; LLM *or* deterministic; the deterministic path is the long-term target with the LLM as safety net.
6. **Cost and latency are first-class.** They are the difference between a demo and a deployment (and our current credibility gap).
7. **Standards-aligned.** OSI and the open semantic interface, not academic ontologies — conceptual schemas that agents can actually use.

---

## How the phases ladder to the North Star

See the [[contextual-data-fabric-prd|PRD]] for detail. In brief:

- **Phase 1** proves the *shape* at small scale — federated query across **one** database + the unstructured graph in Arango, ontology-derived and cited.
- **Phase 2** widens to the **highest-value** source (Snowflake), adds the **assembled** analytics pattern, and instruments **cost/latency**.
- **Phase 3** adds **governance** (ontology-based access control) and **surfaces change management in the fabric** — belief revision, time-travel, and curation already exist in AOE (v0.2 verification); Phase 3's work is the source-change cascade, programmatic change-control hooks, and wiring them into the fabric's ontology lifecycle. These are the capabilities that turn a query tool into the enterprise brain.
- Each step is a real, adoptable increment — never a big-bang toward the vision.

---

## What we are deliberately *not*

- Not the customer's agent application or orchestration layer — we are the **hub** they consult.
- Not a data-integration / warehouse-replacement play — the sources remain the systems of record.
- Not an academic ontology exercise — conceptual, agent-usable models only.
- Not a monolith — if a piece can't stand alone as a block, reconsider it.

*Sources: [[2026-07-13 design-partner customer-context roadmap]], [[design-partner feedback summary]], [[2026-07-10 - C360 design-partner demo]], [[2026-07-09 - C360 field-feedback review]].*
