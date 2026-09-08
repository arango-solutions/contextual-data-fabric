---
title: "Leveraging Google's Open Knowledge Format (OKF) in the Contextual Data Fabric"
type:
  - internal
  - research
  - integration-analysis
date: 2026-09-08
status: draft — for team review
related:
  - "docs/research/aws-context-ontology-accelerator-comparison.md"
  - "docs/contextual-data-fabric-prd.md (§10.12 CC-12, RD-4)"
  - "relational-schema-analyzer (OSI + dbt catalog connectors — the precedent)"
---

# Leveraging Google's Open Knowledge Format (OKF)

> **The ask (Arthur, 2026-09-08):** OKF is reportedly supported by many SQL
> systems and convertible to OWL — could it give us an alternative way to
> produce an ontology, or enhance ontology extraction, implemented in RSA and
> wired through?
>
> **Method.** Facts below come from the primary sources — Google Cloud's OKF
> announcement blog and the spec repo
> (`GoogleCloudPlatform/knowledge-catalog/okf`, v0.2) — read 2026-09-08.
> Claims that appear only in third-party coverage are marked as such.

## 1. What OKF actually is (and two corrections to the ask)

OKF formalizes the "LLM wiki" pattern: a knowledge bundle is **a directory of
markdown files with YAML frontmatter**, one file per *concept* (dataset,
table, metric, playbook, API…), cross-linked with ordinary markdown links.
The spec is deliberately minimal: **`type` is the only required field**;
everything else — which types exist, what fields or body sections a concept
carries — "is left to the producer." v0.2 adds provenance-flavored
frontmatter: `generated`, `verified`, `status`, `stale_after`, `sources`.
Reference tooling: a BigQuery **enrichment agent** that walks datasets and
drafts a concept doc per table/view, "enriched with schemas and join paths";
sample bundles exist for three BigQuery public datasets (GA4, Stack Overflow,
Bitcoin).

Two corrections to the framing we started from:

1. **"Supported by a large number of SQL systems" is not (yet) evidenced.**
   The primary sources show exactly one producer integration: the BigQuery
   enrichment agent. Third-party blog coverage claims broader storage/SQL
   support; the spec and Google's post do not. Treat breadth as roadmap, not
   fact — and re-check quarterly.
2. **"OKF can be converted to OWL" is a category error — usefully so.** The
   spec defines *no* semantics: no classes, no typed relationships, no
   RDF/OWL mapping; the "graph" is untyped markdown links, and schemas live
   as prose/code blocks in the body. There is nothing to *convert*. What an
   OKF bundle actually is, from our perspective, is **unusually well-curated
   extraction INPUT with provenance attached** — and extraction is precisely
   the machinery we own.

## 2. Where OKF fits the fabric — three lanes

### Lane A — RSA: an OKF catalog connector (the ask, correctly sized)

RSA already has the exact pattern: the **OSI data-catalog connector** and the
**dbt-manifest connector** consume external catalog metadata as *evidence*,
passed through on Column/Table via consumer-metadata fields. An OKF connector
is the third sibling:

- **Read** a bundle: frontmatter (`type: table|dataset|metric`, `resource`,
  `tags`, `verified`, `sources`) plus body-parsed schema blocks and the
  cross-link graph.
- **Use as evidence, not truth**: table/column descriptions feed
  purpose-scoped relevance (RE-6's embedding inputs); documented join paths
  become *candidate* declared references (the same overlay seam the CRM
  join-key work used) — subject to CC-14-style verification (value-overlap
  sampling) before the catalog admits them, because OKF content is
  producer-arbitrary by design.
- **Wiring**: RSA → CSI enrichment (descriptions, sample values, reference
  candidates) → r2g → the fabric, unchanged downstream. No new contract —
  OKF is an *input adapter* to the pipeline we have.

### Lane B — AOE: OKF bundles as premium extraction corpora

An OKF bundle is a pre-curated, provenance-stamped document corpus — the
best-case input for AOE's LLM extraction: the ontology comes out of the
*prose* (what a "weekly active user" means, how orders relate to customers),
with `verified`/`sources`/`stale_after` mapping naturally onto AOE's
belief/confidence machinery. This is the honest version of "OKF → OWL":
**extraction over OKF, not conversion of OKF** — and it directly strengthens
the dimension where we grade ourselves 2/5 (semantic/ontology depth).

### Lane C — the sharpest angle: the fabric as an OKF *producer*

Nobody needs OKF to get knowledge *into* systems we already introspect
directly. The strategic play is the reverse: **emit an OKF bundle from the
catalog manifest + CSI** — one concept doc per entity/source/metric, with
`resource` pointing at the physical object, join keys as cross-links, CSI
provenance filling `generated`/`verified`/`sources`, refreshed by the same
build that regenerates the manifest. Then any OKF-consuming agent stack
(Gemini-ecosystem or otherwise) can read the fabric's semantic layer for
free. It is the OSI-counter-demo move from the AWS/COA report, played toward
Google: *their format, our governed content — with cite-or-refuse execution
underneath that a markdown wiki cannot offer.*

## 3. Concept mapping (what lines up, what does not)

| OKF (v0.2) | CDF artifact | Fit |
|---|---|---|
| `type: table` / `dataset` concept | CSI entity / source | direct |
| `resource` link | physical mapping (`arangoPhysicalMapping`, R2RML `rr:tableName`) | direct |
| markdown cross-links | join keys / declared references | **untyped** — needs inference + verification |
| `metric` concepts | certified metrics (scorecard dim. 2 threshold) | future — we have no metric layer yet |
| `generated` / `verified` / `sources` / `stale_after` | CSI provenance, CC-14 probe posture, `lastValidatedAt` | strong philosophical match |
| body prose/schemas | AOE extraction input | direct (Lane B) |
| — (no classes, no OWL, no constraints) | OWL ontology, SHACL, CC-12 naming | **we supply this**; OKF cannot |

## 4. Risks and limits

- **v0.2 volatility** — the spec moved 0.1→0.2 within weeks of GA; build
  behind an adapter seam, pin the version we parse (CC-9 spirit).
- **Producer-arbitrary content** — only `type` is guaranteed. The connector
  must be defensive: everything beyond frontmatter is *candidate* evidence.
- **Untyped links** — the "graph" carries no relationship semantics; treating
  links as join hints without value-overlap verification would import
  garbage into the catalog.
- **Ecosystem bet** — if OKF adoption stalls, Lane A/C effort strands.
  Mitigation: Lane C is a half-day emitter over artifacts we maintain
  anyway; Lane A rides RSA's existing connector seam.

## 5. Recommendations

1. **Do NOT build an "OKF→OWL converter"** — nothing in the spec supports
   it; the correct verbs are *enrich* (Lane A) and *extract* (Lane B).
2. **File RSA RE-8: OKF catalog connector** (behind the OSI/dbt seam,
   evidence-only, version-pinned parse) — small, high-optionality. *(Owner:
   RSA lane; spec first per SOP §4.)*
3. **Run the Lane B experiment cheaply**: feed Google's GA4 sample bundle
   through AOE extraction and compare the resulting ontology to the same
   dataset's schema-only extraction — a measurable answer to "does OKF
   enhance extraction," and forge-adjacent (known corpus, comparable
   output). *(Half-day + credentialed run.)*
4. **Prototype the Lane C emitter** (`cdf-catalog export --okf` or a small
   `deploy/` tool): manifest + CSI → OKF bundle. Positioning demo, and the
   cheapest of the three. *(Half-day.)*
5. **Add OKF to the quarterly competitive/standards watch** next to OSI and
   COA — re-verify the "many SQL systems" claim and the version churn each
   cycle.

## Sources

- https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing (primary — announcement, bundle anatomy, BigQuery enrichment agent)
- https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf (primary — spec v0.2, frontmatter fields, sample bundles)
- Third-party coverage (claims marked unverified above): MindStudio, Wavect, Medium overviews of OKF
