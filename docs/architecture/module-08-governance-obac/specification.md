---
title: "Module 08 — Governance / Ontology-Based Access Control — Specification"
module: 08-governance-obac
type:
  - internal
  - module-spec
status: implemented-with-deployment-gaps
version: 0.2
owner: TBD (research)
building_block: Both
depends_on_modules: ["03-ontology-alignment", "04-mapping-layer"]
depends_on_repos: ["ontology-extractor"]
requires_repo_enhancements: []
phase_intro: 3
related:
  - "[[contextual-data-fabric-prd]]"
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
---

# Module 08 — Governance / Ontology-Based Access Control

> Define business rules, security, and data access **once, on the ontology**, and
> have every interface inherit them. The P3 query-policy subset is implemented;
> production identity, policy, delegation, and source-native enforcement remain
> deployment work.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility
The governance layer that turns the fabric from a query tool into the enterprise brain. Policies live on ontology elements (e.g. a property tagged PII); because the ontology maps that property to its realization in every source, one policy propagates across all systems. When an agent asks, the ontology decides what it may see and hands back the (declarative) accessor. This is a high-value **partnership/land-grab** angle (agent IAM vendors), not just a feature.

## 2. Scope
**In scope:** catalog policy annotations; allow/rewrite/deny planning;
principal-bound row/seed scope; masking/drop; citation and introspection
filtering; OIDC request context; OpenFGA-compatible relationship checks; and
fail-closed delegated execution seams.
**Out of scope:** being the customer's IAM system; provisioning OpenFGA, IdP,
STS, or source-native RLS/masking.

## 3. Interfaces (inputs / outputs)
- **Consumes:** master ontology (M3) + mappings (M4) + agent identity/context.
- **Produces:** an allow/deny + scoped accessor decision the query engine (M5) enforces before federating.

## 4. Functional requirements (Phase 3 implementation)
- **FR-1:** Attach access/PII/business-rule policies to ontology elements; propagate to all mapped sources.
- **FR-2:** Enforce policy at query planning/execution (deny or scope results) with the decision cited in the retrieval path.
- **FR-3:** Declarative source accessors (replace per-source scripts).
- **FR-4:** Explore SHACL/constraint extraction from the ontology extractor for policy encoding.

### 4.1 P3 WP-15/WP-17/WP-18 query-policy layer (2026-08-05)

[ADR-0004](../module-05-federated-query-engine/adr/ADR-0004-identity-planes-and-policy-enforcement.md)
fixes the contracts that policy enforcement consumes:

- steward/build and asker/query identities are separate planes; this increment
  implements only the asker/query runtime plane;
- generic OIDC authentication terminates at HTTP/MCP edges and produces an
  immutable, bearer-free `RequestContext` with an `(issuer, subject)` principal,
  tenant-ready attributes, purpose, IDs, and absolute deadline;
- the PDP contract and production client are OpenFGA-compatible centralized
  ReBAC, with allow/rewrite/deny at both preflight and postflight, layered over
  source-native controls;
- citations and cross-source bind seeds are governed data, not policy-free
  provenance;
- source auth is explicitly `service` or `delegated`; delegated mode fails
  closed unless a broker and context-aware adapter are both present.

CDF now enforces the M11 catalog policy subset at query time: role/group/scope/
purpose checks, per-source/concept/property classification and masks,
principal-bound row scope, source-disclosure controls, governed bind seeds,
post-join masking, postflight re-evaluation, citation redaction, and policy-
filtered HTTP/MCP introspection. Service-mode fabric masking/row pushdown is
default-denied unless the manifest explicitly trusts that PEP operation.

CDF still does **not** provision an OpenFGA service/model/store/tuples, IdP, RFC
8693 STS, Snowflake external OAuth integration, Postgres impersonation mapping,
or source RLS/masking policy. The OpenFGA backend is a bounded fail-closed
client, and tests use an injected offline transport. Those external/control-
plane dependencies must exist and be tested before production enablement.

## 5. Non-functional requirements
Policy defined once; enforcement auditable/cited; no policy logic duplicated into agents.

## 6. Dependencies
- **Repos:** `ontology-extractor` (SHACL/constraint extraction); M3 + M4. Partnership evaluation with agent-IAM vendors.
- **Concrete starting points (v0.2, verified):** the research phase is not greenfield — **r2g Phase 9 (implemented)** already captures/propagates catalog classifications with a sensitivity lattice + mosaic rule, gates loads on entitlement thresholds with masking, and **emits** a classification manifest, suggested ArangoDB RBAC grants, and a default-deny OPA `policy.rego` stub ("emit, don't enforce" lane discipline). **AOE** ships JWT + RBAC (4 roles, org-scoped) and SHACL handling. M8's design work is largely lifting these patterns from load-time (r2g) to query-time (the fabric).

## 7. Phase mapping
- **P1/P2:** no runtime policy enforcement.
- **P3:** catalog annotations and query-time enforcement implemented; external
  control-plane and source-native integrations remain uncertified.

## 8. Acceptance criteria / demo (P3)
- Tagging one ontology property as PII causes agent queries to be denied/scoped across every mapped source, with the decision visible in the cited path.

## 9. Open questions
- Production OpenFGA model/store/tuple ownership and availability SLO.
- IdP/STS integration and source-specific delegated identity mapping.
- Reconciliation between fabric policy and source-native RLS/masking.
