# Contextual Data Fabric — Architecture & Module Index

> **This is the super-module.** It maps the whole system into modules, records how they depend on each other and on the existing Arango repos, and points to each module's own specification. Per our build process, **each module PRD/spec must reconcile against this index** (identify sub-modules → PR per sub-module → reconcile with the super-module → iterate). Arthur is the build gatekeeper.
>
> **Parents:** [[contextual-data-fabric-prd|PRD]] (near-term contract) · [[contextual-data-fabric-north-star|North Star]] (end-state vision).
> **Status (2026-08-06):** Current implementation index. P1 and the recommended
> P2/P3 code sequence are complete; production integrations and SOTA evidence
> remain explicitly tracked in the project scorecards.

---

## Repo layout

```
contextual-data-fabric/
  docs/
    architecture/
      README.md                          ← this file (super-module / index)
      deployment-p1.md                   ← Phase-1 demo topology: what runs where (v0.2.1)
      project-scorecard.md               ← current executable-gate evidence
      project-sota-scorecard.md          ← competitor-relative leadership evidence
      _TEMPLATE-module-spec.md           ← copy this to author a module spec
      module-01-connectors/
        specification.md
      module-02-ontology-extraction/
        specification.md
      module-03-ontology-alignment/
        specification.md
      module-04-mapping-layer/
        specification.md
      module-05-federated-query-engine/
        specification.md                 ← written (exemplar)
      module-06-entity-resolution/
        specification.md
      module-07-grounding-provenance/
        specification.md
      module-08-governance-obac/
        specification.md
      module-09-demo-harness/
        specification.md
      module-10-evaluation/
        specification.md                 ← added v0.2 (PRD §10.1)
      module-05-federated-query-engine/adr/
        ADR-0003-authoritative-catalog-manifest.md ← implemented M11 contract
      _repo-enhancements/                ← requirement specs for EXISTING repos
        r2g-federated-query.md           ← written (exemplar)
        ontology-extractor-structured.md
        aer-semantic-federated.md
        schema-analyzers-metadata-sampling.md
        customer-context-expose-modules.md
```

## The module set

Two headline building blocks from the [[contextual-data-fabric-prd|PRD]] — the **Onto Extract layer** and the **Federated Query layer** — decomposed into buildable modules. Each is intended to be independently ownable and, where it makes sense, independently publishable (LLM *or* deterministic implementations both valid).

| # | Module | Responsibility | Building block |
|---|--------|----------------|----------------|
| **M1** | **Connectors** | Source adapters + **metadata-sampling** connectors (Postgres, Snowflake, Databricks, unstructured-in-Arango). Provide schema/metadata to extraction and live query access to the query engine. | Both |
| **M2** | **Ontology Extraction** | Structured (schemas/catalogs) + unstructured (docs) → **per-source ontologies**. Wraps **r2g** + the **ontology extractor**. | Onto Extract |
| **M3** | **Ontology Alignment** | Per-source ontologies → **master ontology**: diff/deltas → accept/reject → iterative refinement; belief management, time-travel, change control, curation (agent or human). | Onto Extract |
| **M4** | **Mapping Layer** | **Functional mappings** — CSI v1 as the catalog/mapping hub, R2RML for SQL legs, and MappingBundle for AQL. The mapping drives the query. | Both |
| **M5** | **Federated Query Engine** | English → resolve concepts → **decompose** → per-source query gen (SQL pushdown / AQL / agent) → execute → **reassemble**. Loosely-coupled + assembled; LLM + deterministic. | Query |
| **M6** | **Entity Resolution / Canonical Hub** | Cross-source ER → canonical entities in Arango. Wraps **AER**. | Both |
| **M7** | **Grounding & Provenance** | Validated answer envelope + **cited retrieval path across the federation boundary** (actual SQL + AQL + source object); refuse if uncited. | Query |
| **M8** | **Governance / OBAC** | Implemented catalog/OpenFGA-compatible allow/rewrite/deny, governed seeds/rows, masking, citation disclosure, introspection filtering, OIDC context, and delegated-identity seams. External policy/identity services remain deployment work. | Both |
| **M9** | **Demo Harness** | Thin agent UI to run seed questions end-to-end (reuse the customer-360 Vercel pattern). Not sold. | — |
| **M10** | **Evaluation & Golden Set** | Live/fixture goldens, NL and CK25 evidence, resolution precision, optimizer oracle, interface parity, performance baseline, catalog integrity, authorization, and the unified evidence runner. Not sold. | — |
| **M11** | **Authoritative Fabric Catalog** | Implemented, content-hashed manifest over sources, concepts, mappings, statistics, join keys, entitlements, auth modes, and runtime-resolution bindings. | Both |

---

## Module → repo dependencies

Each module builds on one or more existing repos (see `_repo-enhancements/` for what each repo must add). Full repo details in [[contextual-data-fabric-prd]] §8.

| Module | Builds on repo(s) | Repo enhancement required |
|--------|-------------------|---------------------------|
| M1 Connectors | schema-analyzers, r2g, customer-context | `schema-analyzers-metadata-sampling` |
| M2 Ontology Extraction | r2g, ontology-extractor (AOE) | `ontology-extractor-structured` |
| M3 Ontology Alignment | ontology-extractor (AOE) | `ontology-extractor-structured` (alignment/belief APIs) |
| M4 Mapping Layer | r2g (CSI/R2RML export) | `r2g-federated-query` |
| M5 Federated Query Engine | r2g, **arango-sparql-py**, **arango-cypher-py**, **arangodb-schema-analyzer (CSI v1)**, Ontop (buy-vs-build), new code (federation layer) | `r2g-federated-query`; ADR-0001 + M5 implementation plan |
| M6 Entity Resolution | arango-entity-resolution (AER) | `aer-semantic-federated` |
| M7 Grounding & Provenance | customer-context | `customer-context-expose-modules` |
| M8 Governance / OBAC | M11 catalog, mapping layer, optional OpenFGA/IdP/STS | Production service provisioning and source-native policy evidence |
| M9 Demo Harness | customer-context | `customer-context-expose-modules` |
| M10 Evaluation | customer-context (corpus/questions), ontology-extractor (judge patterns) | — |
| M11 Catalog | CSI/R2RML inputs, RSA adapter, M1/M4/M5/M8 consumers | ADR-0003; future graph-backed control plane |

---

## Phase mapping and current state

Ladders to the [[contextual-data-fabric-prd|PRD §6]] phases.

| Module | Implemented through P3 | Remaining evidence or scope |
|--------|------------------------|-----------------------------|
| M1 Connectors | Postgres/Ontop, Snowflake, ClickHouse, ArangoDB; secret resolution and rotation | Additional source kinds; production delegated identities |
| M2 Extraction | Checked-in CSI inputs and optional RSA→CSI adapter | Full cross-repository automated extraction proof |
| M3 Alignment | Small authoritative concept ownership model | Public alignment/reasoning/temporal benchmark |
| M4 Mapping | CSI v1, R2RML, and MappingBundle runtime contracts | Broader transform/conformance suite |
| M5 Query Engine | Deterministic multi-source planning, bind joins, admission, virtual and assembled execution | Broader SPARQL expressiveness and public comparative workload |
| M6 ER | Guarded resolver protocol, budgets, fail-closed runtime, safety corpus | Clean released AER pin and live large-scale evaluation |
| M7 Grounding | Answer/leg-level citations, native queries, partial/refusal semantics | Field-level standards-compatible lineage |
| M8 Governance | Catalog/OpenFGA-compatible policy, OIDC context, masking, governed introspection | Live OpenFGA/IdP/STS and source-native policy |
| M9 Demo | Browser workflow, metrics, provenance, mandatory gate | Production UX is out of scope |
| M10 Evaluation | Unified evidence runner and current internal corpora | Public signed benchmarks and controlled bakeoff |
| M11 Catalog | Authoritative content-hashed manifest and integrity gate | Graph-backed control plane and OpenLineage export |

---

## Building-block version pins (CC-9)

The executable sources of truth are `pyproject.toml`, `Makefile`, the container
Compose files, and CI—not this prose table. Current integration state:

| Block | Current integration |
|-------|---------------------|
| Postgres / Ontop | `postgres:16`; `ontop/ontop:5.5.0` |
| ArangoDB | `arangodb:3.12` |
| ClickHouse | `clickhouse/clickhouse-server:24.8` |
| `arango-sparql-py` | CI and default demo installation share the reviewed full SHA in `deploy/pins/arango-sparql-py.txt` (`arango-solutions` main at review time). Editable siblings require explicit `CDF_USE_LOCAL_SIBLINGS=1`. |
| `arango-schema-analyzer` | Resolved through the pinned `arango-sparql-py[nl,analyzer]` dependency set. |
| AER | No runtime package pin. CDF exposes a guarded resolver protocol; a clean released AER integration is pending. |
| CK25 harness evidence | Generated against `arango-sparql-py@623aa24`; the checkout was dirty, while benchmark paths were clean. This is evidence provenance, not the runtime pin. |
| Python dependencies | Version ranges are declared in `pyproject.toml`; no lock file currently certifies a full transitive environment. |

---

## Repo enhancement specs

Requirement specs telling each **existing** repo what it must add to serve this project (author with the same template discipline):

- **`r2g-federated-query`** — the durable CSI/R2RML export contract is
  integrated; Ontop and native warehouse executors own runtime SQL generation.
- **`ontology-extractor-structured`** — confirm/complete **structured→ontology**; expose **alignment** + **belief-management/time-travel** APIs.
- **`aer-semantic-federated`** — **semantic** (non-deterministic) matching; **federation-aware** ER; canonical-hub API.
- **`schema-analyzers-metadata-sampling`** — metadata-sampling API for connectors + the mapping layer.
- **`customer-context-expose-modules`** — expose the unstructured graph + the grounding/citation envelope as consumable modules; ontology-driven AQL generation.

---

## Active decisions and integration gaps

1. **Owned dependency reproducibility:** reconcile the two
   `arango-sparql-py` mirrors, cut clean release tags, and pin a released AER;
   run CC-9 evidence before each bump.
2. **Production identity and policy topology:** provision and exercise
   OpenFGA/IdP/STS plus source-native delegation, RLS, and masking.
3. **Public evidence package:** freeze signed workloads and raw result artifacts,
   then run controlled correctness, performance, NL, ER, and governance bakeoffs.
