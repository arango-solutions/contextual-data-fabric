---
title: "Access Control for the Contextual Data Fabric — research & phased recommendation"
type:
  - internal
  - research
status: draft
date: 2026-08-03
related:
  - "[[contextual-data-fabric-prd]]"          # §10.7 CC-7, §10.8
  - "[[module-08-governance-obac/specification]]"
  - "[[contextual-data-fabric-product-strategy-prd]]"  # §4.1 two identity planes, P3.2/P3.3
audience: architecture / PRD (later phase)
---

# Access control for a federated NL query fabric

**Scope.** How the Contextual Data Fabric should handle access control once it moves
past the single-service-identity demo. Covers (1) the user asking a question needing
permissions on the federated sources — so queries can fail on missing entitlement —
and (2) whether a Palantir-style *ontology-unified* identity/access model applies here.
This is design research for a **later phase (M8 / P3)**; it validates and sharpens the
plan the PRD already commits to (CC-7 §10.7, Module-08 OBAC, product-PRD §4.1,
ADR-0004) rather than proposing greenfield.

> **Assumption flagged up front:** the primary deployment target is treated as
> **single-tenant enterprise** — the fabric runs inside one customer's environment
> behind their IdP; "the user" is that org's employee/agent; the sources are that
> org's systems. Multi-tenant SaaS is noted where it changes the recommendation. If
> the target is actually multi-tenant, revisit §8 (tenant isolation becomes the
> first-order concern).

---

## 0. TL;DR

- **Today** the fabric is a **trusted subsystem**: it connects to each of the four
  sources as *itself* with one read-only service credential per source, wired purely
  `from_env`; `POST /federate` is **anonymous** — no user identity reaches any leg.
  This is deliberate and documented (CC-7 §10.7: *"connects as itself … no per-user
  credential passthrough … until M8"*).
- The **ontology/CSI layer is the natural policy seat** — exactly where Palantir puts
  it. All routing flows through one concept→source index with single-source concept
  ownership ([`catalog.py`](../../src/cdf/query/catalog.py) `source_of_class` /
  `sources_of_property`), and the catalog already carries per-property structure fine
  enough for property-level entitlement/PII tags.
- The **new single-leg FILTER/OPTIONAL pushdown** ([`planner.py`](../../src/cdf/query/planner.py))
  creates three concrete access-control facts to design around: (a) a pushed predicate
  runs under the *service* identity, so any source row-level security keys off the
  fabric's role, not the asker; (b) there is **no column-masking guard** — a projected
  sensitive column flows straight into `bindings`; (c) the bind-join `VALUES … IN (…)`
  pushdown moves join-key values across sources **and into citations**.
- **Recommendation: a deliberate hybrid, phased.** Start **delegated** (authenticate
  the user at the edge, propagate identity to each source so source-native RLS/masking
  fires for the *real* user — this is what makes pushdown safe and eliminates the
  confused-deputy risk), then add **planner pre-flight authorization + partial-results
  disclosure**, then a **central ReBAC/policy decision surface** over the ontology
  graph, and finally **ontology-level markings + purpose controls with lineage
  propagation into the cited answer** (the Palantir end-state). Bind the LLM to the
  user's identity throughout — never a broad agent service account.

---

## 1. Where the fabric is today (grounded in the code)

### 1.1 One service identity per source; anonymous edge
`FederationService.from_env` ([`app.py`](../../src/cdf/service/app.py) L85-167) builds
one executor per source from environment variables only — one shared credential each:

| Source | Executor | Credential (from_env) |
| --- | --- | --- |
| `arango:cmf` | `ArangoExecutor` | one `ARANGO_USER`/`ARANGO_PASSWORD` handle |
| `postgresql:crm` | `OntopExecutor` | a single trusted Ontop SPARQL endpoint URL (no `Authorization` header) |
| `snowflake:telemetry` | `SnowflakeExecutor` | one `SNOWFLAKE_USER`/`_PASSWORD`/`_ROLE`, pooled+reused across callers |
| `clickhouse:analytics` | `ClickHouseExecutor` | one `CLICKHOUSE_DSN` (user embedded) |

`POST /federate` and `POST /nl-preview` carry no caller identity, tenant, scope, or key
— no auth middleware, no `Depends(...)`, no tenancy anywhere in `src/cdf/`. This is the
**trusted-subsystem** model and it is intentional (module docstring: *"Credentials stay
in the engine per CC-7 … the HTTP surface never sees a connection string."*).

### 1.2 The ontology layer is maximally central
Every routing decision goes through `SourceCatalog.source_of_class` /
`sources_of_property`, with the invariant that **a class is owned by exactly one
source** (guaranteed by the M3 alignment layer). The planner's `_route` touches nothing
else. A Palantir-style ontology-mediated access layer would sit **precisely here** — an
entitlement/marking annotation on a concept or property is known at plan time for every
leg that touches it. The north-star states the bet: *"Governance lives in the ontology …
security, and data access are defined once, on the ontology, and inherited by every
agent."*

### 1.3 The pushdown changes the access-control surface (new)
The planner now pushes single-leg `FILTER` conjuncts and `OPTIONAL` groups into each
leg's SPARQL, compiled to native SQL/AQL and run under the one service identity. Three
consequences, each verified in the adapters:

1. **Source RLS is not per-user.** A pushed `?v op literal` becomes a real `WHERE` in
   the *service* session (`clickhouse.py`/`snowflake.py` `compile_sql`). Any source
   row-access policy evaluates against the fabric's role — not the asker. Source RLS as
   wired today is uniform, not per-user.
2. **No column/masking guard.** Projected columns are emitted verbatim
   (`compile_sql` builds the SELECT list from the query projection with no policy
   check). A projected sensitive column (e.g. `email`) flows straight into `bindings`.
3. **Bind-join discloses keys across sources and into citations.** The executor seeds
   the graph leg with distinct join-key rows as a trailing `VALUES` clause
   (`executor._with_values` → `col IN (…)`, capped at `SEED_CAP=1000`). Those values
   also appear in the cited leg SPARQL (`RetrievalStep.seeded_vars`). Under a future
   per-user model this is a genuine cross-source disclosure channel that policy must
   scope, and a citation-side exposure that must be redactable.

Crucially, the planner's FILTER-injection machinery (`_serialize_filter`, `_serialize`)
is the **same seam** the product PRD names for compiling *entitlements* into legs — so
the mechanism already exists; it is concept-driven today and needs to become
identity-driven.

### 1.4 The cited envelope already declares shortfalls (the disclosure hook)
`FederatedResult` / `AnswerEnvelope` carry `failed_sources`, `unavailable_vars`,
`unresolved`, `partial`, `refusal_reason`, and `status ∈ {grounded, partial, refused}`
([`executor.py`](../../src/cdf/query/executor.py), [`grounding.py`](../../src/cdf/query/grounding.py)),
implementing FR-11 "never silent omission." An access denial that surfaces as a failed
leg already flows through refuse-vs-partial. **The one missing piece is a distinct
refusal class** ("refused: insufficient entitlement") and, likely, a `withheld_sources`
field — an *additive* extension to an envelope that already declares every shortfall.

### 1.5 This is designed-but-deferred, not undesigned
The corpus already commits to the end-state: **CC-7 §10.7** ("connects as itself … no
per-user passthrough until M8; least-privilege read-only role per source; secrets via a
`SecretResolver`, never in mappings"); **Module-08 Governance/OBAC spec** (policies on
ontology elements propagate across sources; FR-2 "enforce at planning/execution — deny
or scope — with the decision cited in the retrieval path"); **product-PRD §4.1** "two
identity planes: the steward and the asker" (users authenticate to the fabric, never to
sources; entitlements on the ontology *compiled into every leg and enforced again at
reassembly*; "citations are data … a user who cannot read a source object cannot
receive it as a citation" → the *insufficient-entitlement* refusal class); **ADR-0004**
(planned: where entitlements compile — leg vs reassembly vs both — citation redaction,
refusal classes). Load-time precursors exist to lift into the query path: **r2g Phase 9**
(sensitivity lattice, entitlement gating, masking, an OPA `policy.rego` stub) and
**AOE's JWT + RBAC** (4 roles, org-scoped).

---

## 2. The two enforcement models (industry)

### 2.1 Delegated / passthrough identity — each source enforces its own policy
The end user's identity is propagated to each backend; each enforces its native
RBAC/row/column policy. This is what federated SQL engines do:
[Trino](https://trino.io/docs/current/security/file-system-access-control.html) ships
impersonation rules ("run queries on behalf of other users … Trino verifies that the
administrator is authorized to run queries as the target user") and externalizes
fine-grained decisions to [OPA](https://trino.io/docs/current/security/opa-access-control.html)
or [Apache Ranger](https://trino.io/docs/current/security/ranger-access-control.html)
(column-masking, row-filtering); [Immuta](https://documentation.immuta.com/saas/govern/secure-your-data/authoring-policies-in-secure/data-policies/reference-guides/data-policies)
authors attribute/tag policies once and enforces across engines. The propagation glue:
**[OAuth 2.0 Token Exchange (RFC 8693)](https://datatracker.ietf.org/doc/html/rfc8693)**
— an STS mints a downstream token carrying the user as `subject_token` (and optionally
the fabric as `actor_token`), with explicit **impersonation** ("A is indistinguishable
from B") vs **delegation** ("A retains its own identity, acting for B") semantics and a
`may_act` claim — plus Kerberos constrained delegation and DB `SET ROLE`/impersonation.

**Tradeoffs:** enforcement is native, correct, already audited at each source; no policy
duplication; results returned are already scoped (the fabric never over-reads). But
policy is *fragmented* (no single surface), consistency depends on every source being
configured coherently, and you need working identity federation to each backend (harder
for warehouses reached via service connections).

### 2.2 Trusted subsystem + central policy engine — the fabric enforces
The fabric connects with a service account and authorizes the user itself at the
semantic layer via a policy engine ([OPA/Rego](https://www.openpolicyagent.org/),
[AWS Cedar](https://docs.cedarpolicy.com/), [Ranger](https://apache.github.io/ranger/),
[Immuta](https://documentation.immuta.com/)). **Tradeoffs:** one policy surface, uniform
audit, and you can express semantic/ontology-level rules the sources can't — but two
real risks. **(1) Confused deputy:** a privileged intermediary "is tricked … into
misusing its authority"
([Hardy 1988](http://cap-lore.com/CapTheory/ConfusedDeputy.html);
[Wikipedia](https://en.wikipedia.org/wiki/Confused_deputy_problem)). A fabric holding
broad service creds *is* a deputy — if it authorizes the wrong user (bug, injection,
missing check), sources can't catch it because they only see the trusted account.
**(2) Policy replication:** you must mirror source row filters / column masks / role
hierarchies centrally and keep them in sync; drift means grant-where-source-would-deny.

### 2.3 Hybrid (the usual real answer)
Either a **shared policy engine across both tiers** (e.g. Ranger tag-based policies
enforced by the fabric *and* the sources) or **central decision + delegated
enforcement** — the fabric makes the coarse allow/deny and query-shaping decision, then
*still* propagates the user identity so each source re-checks natively as defense in
depth. Hybrid gets single-surface authoring **and** confused-deputy protection.

The closest analog to CDF — **Denodo**, a data-virtualization/federation layer — makes
the choice **per source**: "the user can choose whether to make use of a *service
account* for the source … or to apply *pass-through authentication and authorization* …
[so] user credentials are directly used in the data source"
([Denodo Security Overview](https://community.denodo.com/kb/en/view/document/Denodo%20Security%20Overview)).
That per-source toggle is the pragmatic model for CDF: a mature source with native
RLS/masking gets pass-through (delegated); a source without gets the service account +
fabric-side policy — and the same engine (Trino) treats service-identity as the
*default* with end-user impersonation an opt-in
([Trino HDFS impersonation defaults false](https://trino.io/docs/current/object-storage/file-system-hdfs.html)),
which is exactly the posture CDF is in today and should graduate from. Note the
trusted-subsystem's intrinsic weakness the fabric must design around: "it is not
possible to detect if the trusted subsystem substituted one user's identity in place of
another" — the confused-deputy exposure in one sentence.

---

## 3. The Palantir ontology-mediated model (the user's question)

Palantir's thesis: enforce access **once, at the ontology/platform layer**, and let it
travel with the data via lineage — not re-implemented per source
([Foundry security overview](https://www.palantir.com/docs/foundry/security/overview)).
The pieces relevant to CDF:

- **Markings** — mandatory, classification-based, **propagate through derived data**:
  "markings are inherited along … direct dependencies and propagate through transform
  and analysis logic. All resources derived from a marked file … assume a Marking unless
  explicitly removed," and "markings travel with the data"
  ([Markings](https://www.palantir.com/docs/foundry/security/markings)). The
  government variant, [CBAC](https://www.palantir.com/docs/foundry/security/classification-based-access-controls),
  combines a resource's classification with *all upstream dependencies'*.
- **Object / property / cell security** — object security policies give **row-level**
  security "independently of the permissions on the backing data source"; property
  policies give **column-level**; together, **cell-level** (fail the object policy →
  row invisible; fail the property policy → "they will see a *null* value")
  ([Object & property security policies](https://www.palantir.com/docs/foundry/object-permissioning/object-security-policies)).
- **Restricted Views** = dataset-level RLS the ontology objects **inherit**
  ([Restricted views](https://www.palantir.com/docs/foundry/security/restricted-views)).
- **Purpose-Based Access Controls (PBAC)** — grant access to a *purpose* (a
  governance-approved use), not per-dataset: data minimization / need-to-know
  ([PBAC blog](https://blog.palantir.com/purpose-based-access-controls-at-palantir-f419faa400b3)).
- **AIP** applies the same boundary to LLMs: the agent can only touch what the invoking
  user can (the "act with the user's authority" principle — §7).

**The claim and its preconditions.** Because object/property policies are enforced by
the ontology *independently of the backing source's permissions*, the ontology becomes
the single policy surface — **benefits:** one place to author/audit; consistent
enforcement across every consumer (dashboards, APIs, AI); marking/lineage propagation so
derived/federated products inherit their sources' restrictions. **Preconditions:**
(a) data must be *governed under the ontology* (registered so lineage/markings attach),
and (b) **the ontology layer must be unbypassable** — if a user can reach the raw
dataset through a side channel, ontology policies that "don't require Viewer on the
backing dataset" become a *widening*, not a control, so source-level controls must still
hold underneath.

**Direct relevance to CDF.** Two ideas port cleanly: (1) attach entitlement/marking to a
**concept or property** in the catalog — CDF's single-source concept ownership makes the
"which legs are affected" question decidable at plan time; (2) **propagate markings into
the merged, cited answer** so each cited datum carries its source's restrictions — CDF
already asserts the citation-side version of this ("a user who cannot read a source
object cannot receive it as a citation"). The Palantir preconditions are the honest
catch: CDF's raw sources (Postgres, Snowflake, ClickHouse, Arango) are reachable
directly (and the `arango-solutions-mcp-server` raw-AQL path explicitly *bypasses the
ontology*), so ontology-level policy can only be the *primary* surface if the raw
sources keep their own least-privilege controls beneath it.

---

## 4. Pushdown × fine-grained security — the CDF-specific hazard

When the planner pushes a user predicate to a source, it can interact with RLS/masking
in ways that leak values the user shouldn't see. What the engines do:
**Snowflake** evaluates the row-access policy first, then rewrites the column mask "at
every place where the column appears (projections, join predicate, **where clause
predicate**, …)" — so `WHERE masked_col = 'x'` is evaluated against the *masked* value
([row access policies](https://docs.snowflake.com/en/user-guide/security-row-intro),
[dynamic masking](https://docs.snowflake.com/en/user-guide/security-column-ddm-use)).
**Databricks Unity Catalog** compiles filters/masks into a secure view under the scan
and, when forced to choose, "always makes the secure choice … users cannot view base
table values before filtering or masking"
([row filters & column masks](https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/)).

**The pitfall, precisely.** RLS/masking is only safe under pushdown if applied **at or
below the pushdown boundary for the requesting user's identity**. The dangerous
federated pattern — and exactly CDF's shape today — is: the fabric pushes the raw user
predicate to a source connected via a **service account** (so user-specific RLS/masking
doesn't fire), the source returns unmasked rows, and the fabric masks *afterward* — now
the pushed predicate already filtered on values the user can't see, and result
counts/pagination leak them. Even with source masking, predicate **inference
side-channels** remain (e.g. a divide-by-zero that only fires for filtered values —
called out for both
[BigQuery](https://cloud.google.com/bigquery/docs/row-level-security-intro) and
[SQL Server RLS](https://learn.microsoft.com/en-us/sql/relational-databases/security/row-level-security)).

> **Design rule for the fabric:** never push a predicate referencing a column the
> requesting user cannot see **unless** the source's masking/RLS is guaranteed to be
> enforced for that user's identity. This is the single strongest argument for the
> **delegated baseline** (§8 Phase A): with the user's identity at the source, RLS and
> masking fire correctly *before* the pushed predicate, closing the leak. A
> service-identity design cannot safely push predicates over restricted columns.

---

## 5. Failure, disclosure, and information-leak semantics

- **Pre-flight authorization beats fail-at-execution.** The XACML PEP/PDP model decides
  *before* access ([XACML 3.0](https://docs.oasis-open.org/xacml/3.0/xacml-3.0-core-spec-os-en.html)).
  For CDF this means a **per-leg capability check in the planner before dispatch** →
  decide **allow / rewrite (inject the source's row filter, drop a masked column) /
  deny** per leg, rather than discovering denial mid-scan. Pre-flight also lets the
  planner *reshape* the plan instead of erroring.
- **Silent row-filtering vs explicit denial.** Acknowledging a source/table/row exists
  can itself leak (the same reasoning as returning **404 not 403** when existence is
  sensitive —
  [OWASP A01 / 404-vs-403](https://www.insights.cgi.com/blog/when-should-you-return-404-instead-of-403-http-status-code)).
  RLS engines therefore *silently filter rows out* rather than throwing per-row
  ([SQL Server RLS](https://learn.microsoft.com/en-us/sql/relational-databases/security/row-level-security)).
  **CDF rule:** prefer silent row/column filtering (answer over what's permitted); use
  explicit "N sources withheld" disclosure only at **leg** granularity where existence
  isn't the secret, and keep it coarse (never reveal hidden-row counts).
- **CDF is close.** The envelope already declares `failed_sources` / `unavailable_vars`
  / `refusal_reason` / `status`. Add, additively: a distinct **`status="refused"` reason
  "insufficient entitlement"** (vs "ungrounded") and a **`withheld_sources`** field, so
  an entitlement shortfall is a first-class, testable outcome (this is exactly
  product-PRD §4.1 / M8 FR-2 "the decision cited in the retrieval path").

---

## 6. Standards & engines — when to use each

- **Authn at the edge:** [OpenID Connect](https://openid.net/specs/openid-connect-core_1_0.html)
  (authenticate the human) → **[RFC 8693 token exchange](https://datatracker.ietf.org/doc/html/rfc8693)**
  (propagate the user, and optionally the acting fabric, to sources) → **SCIM**
  ([RFC 7644](https://www.rfc-editor.org/rfc/rfc7644.html)) to keep identities/attributes
  in sync.
- **Policy models:** RBAC (stable coarse roles); **ABAC** (decisions on attributes —
  classification/region/clearance; NIST SP 800-162 frames RBAC/ACL as ABAC special
  cases, [SP 800-162](https://csrc.nist.gov/pubs/sp/800/162/upd2/final)); **PBAC**
  (purpose/need-to-know — the Palantir data-minimization layer).
- **Engines:** [OPA/Rego](https://www.openpolicyagent.org/) (general decoupled decision
  service; Trino-native), [AWS Cedar](https://docs.cedarpolicy.com/) (RBAC+ABAC in one
  analyzable language), [Ranger](https://apache.github.io/ranger/) (tag-based, spans the
  data stack), [Immuta](https://documentation.immuta.com/) (attribute/tag policies
  across engines).
- **ReBAC — the natural fit for an ontology.** An ontology *is* a graph of typed
  relationships, and authorization-as-graph-question is what **Google Zanzibar**
  ([USENIX ATC '19](https://www.usenix.org/conference/atc19/presentation/pang)) and its
  CNCF implementation **[OpenFGA](https://openfga.dev/docs/fga)** do — relation tuples
  `object#relation@user` with `tuple-to-userset` rewrites for inheritance. OpenFGA
  explicitly "takes the best ideas from Zanzibar for ReBAC and also solves RBAC and ABAC
  use cases" ([concepts](https://openfga.dev/docs/authorization-concepts)). Why it fits
  CDF: when a user's permission on an ontology object is *derived* from relationships
  ("can read a Vehicle if in the owning Org's fleet team"), the derivation is a graph
  traversal — the same shape as the ontology — so the authorization graph and the data
  graph share structure, and "which objects can this user see" becomes a reverse index
  the **planner can use for query shaping**.

---

## 7. The AI agent as confused deputy

Because an LLM sits between the user and the sources, it is the archetypal **confused
deputy** — broad tool/credential authority that **prompt injection** can redirect for a
party who shouldn't have it. [OWASP LLM06:2025 Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
names the root causes (excessive functionality/permissions/autonomy) and prescribes the
fix directly: *"Track user authorization and security scope to ensure actions taken on
behalf of a user are executed on downstream systems in the context of that specific
user, and with the minimum privileges necessary"* — explicitly warning against a
"generic high-privileged identity." **CDF verdict:** every source query the NL front-end
issues must run **inside the requesting user's permission boundary**, not the fabric's
service identity (RFC 8693 on-behalf-of); least privilege; human-in-the-loop for any
future write/action. A broad agent service account is the disfavored design — which is
precisely what CDF has today and what M8 must replace.

---

## 8. Recommendation — hybrid, phased (mapped to CDF's roadmap)

The comparison, for CDF's shape (LLM → conceptual query over the ontology → pushdown to
Postgres/Snowflake/ClickHouse/Arango):

| Dimension | Delegated (§2.1) | Ontology-mediated / trusted (§2.2, §3) |
| --- | --- | --- |
| Policy surface | Fragmented per source | Single, at the ontology |
| Enforcement correctness | Native, already audited | As correct as the central policy replica |
| Consistency across sources | Per-source config | Uniform by construction |
| Confused-deputy risk | Low (source authorizes the real user) | High if the agent holds broad service creds |
| Marking / lineage propagation | None built-in | Strong |
| Preconditions | Identity federation to every source | Governed data + unbypassable ontology |
| **Pushdown safety (§4)** | **Safe** (RLS/masking fire for the real user) | Risky unless the fabric re-applies per user |

Neither pure model wins; the phased hybrid does. Each phase maps to a plan CDF already
has.

- **Phase A — Identity + propagation (delegated baseline).** OIDC at the `/federate`
  edge; propagate the authenticated user to each source via RFC 8693 / Kerberos /
  `SET ROLE`; let each source enforce its native RLS/masking for the *real* user. This
  is the single highest-leverage step: it makes the new pushdown **safe** (§4) and
  eliminates the confused-deputy risk *before* any semantic policy is built. Bind the
  LLM/NL front-end to the user's token — never a shared identity (§7). *→ CDF
  product-PRD P3.2 "identity split"; supersedes today's CC-7 service-only stance for the
  query plane.*
- **Phase B — Planner pre-flight authorization + disclosure.** Per-source capability
  check before dispatch → allow / rewrite (inject the source's row filter, drop a masked
  column from the projection) / deny. Return authorized partial results with a coarse
  "N sources withheld"; silent row/column filtering over per-row denials (§5). Add the
  **`refused: insufficient entitlement`** class + `withheld_sources` to the envelope
  (additive). Redact citations a user can't read (product-PRD already requires it), and
  scope the bind-join `VALUES` to what the user may see (§1.3). *→ Module-08 FR-2;
  product-PRD §4.1 / P3.3 OBAC v1.*
- **Phase C — Central decision surface (ReBAC), delegated enforcement.** Introduce one
  policy engine for the semantic allow/deny and query-shaping decision — **OpenFGA/ReBAC
  is the recommended default** because the ontology is already a relationship graph
  (§6): "which concepts/objects can this user see" becomes a reverse index the planner
  consumes. *Still propagate identity to the sources* (Phase A) so they re-check natively
  — defense in depth against the confused-deputy risk a central engine reintroduces.
  Reuse the r2g Phase 9 OPA stub / AOE RBAC as the load-time inputs.
- **Phase D — Ontology markings + purpose controls + lineage propagation (Palantir
  end-state).** Once data is governed under the ontology: attach markings/classification
  to concepts/properties and **propagate them into the merged, cited answer** so each
  cited datum inherits its source's restrictions; add PBAC/purpose scoping. **Keep source
  RLS/masking live beneath it** so the ontology layer can never be a bypass (the
  Palantir precondition; note the raw-AQL MCP path must stay admin-only). *→ the
  north-star "governance in the ontology."*

**Cross-cutting invariants** for every phase: (1) the user's identity reaches
enforcement — no broad service-account-only path; (2) citations are data and pass the
same policy as bindings; (3) prefer scoping/filtering over hard denial to avoid
existence leaks; (4) source-native least-privilege controls remain in force beneath any
ontology-level policy.

---

## 9. Open decisions for ADR-0004

1. **Enforcement point: leg vs reassembly vs both.** Recommendation: **both** —
   inject entitlement predicates/column-drops at the *leg* (so sources filter and
   pushdown stays safe) *and* re-check at reassembly (citations, cross-source
   bind-join). Product-PRD already leans this way ("compiled into every leg … enforced
   again at reassembly").
2. **Delegated vs central-decision default.** Recommendation: **delegated baseline
   (Phase A) first**, central ReBAC decision layered (Phase C) — not central-only.
3. **Central engine: ReBAC (OpenFGA) vs ABAC (Cedar/OPA).** Recommendation: **ReBAC**,
   given the ontology graph; ABAC/OPA acceptable if attribute-rule expressiveness
   dominates relationship-derivation.
4. **Tenancy.** This report assumes single-tenant enterprise. If multi-tenant SaaS,
   tenant isolation (per-tenant identity brokering, per-tenant credential vaulting,
   hard row-scoping by tenant) becomes Phase A-0, before user-level entitlement.
5. **Bind-join & citation redaction mechanics** — how `VALUES` seeds and
   `RetrievalStep` SPARQL are scoped/redacted per user (§1.3).

---

## 10. PRD-ready section (paste-in for a later phase)

> ### Access control & identity (M8 / P3 — later phase)
>
> **Problem.** The fabric federates a user's question across multiple sources. The user
> asking the question must be entitled to the data each source returns; a query may be
> partly answerable (some legs permitted, others withheld). Today the engine connects to
> every source as one read-only service identity (CC-7) and the HTTP surface is
> anonymous — there is no per-user authorization, and the new single-leg FILTER/OPTIONAL
> pushdown runs under the service role, so source row-level security is not per-user and
> projected columns are unmasked.
>
> **Approach (hybrid, phased).**
> 1. **Delegated identity (baseline).** Authenticate the user at `/federate` (OIDC) and
>    propagate their identity to each source (RFC 8693 token exchange / DB `SET ROLE`),
>    so source-native RLS/masking enforces for the real user and predicate pushdown stays
>    leak-safe. The NL/LLM front-end acts strictly with the user's authority (OWASP
>    LLM06), never a shared service account.
> 2. **Planner pre-flight authorization.** Per-leg allow / rewrite (inject row filter,
>    drop masked column) / deny before dispatch; authorized partial results with a coarse
>    "N sources withheld" disclosure; silent row/column filtering over per-row denial.
> 3. **Central ReBAC decision surface.** One policy engine (OpenFGA/Zanzibar-style —
>    natural for the ontology graph) makes the semantic allow/deny and query-shaping
>    decision; identity still propagates to sources (defense in depth).
> 4. **Ontology-level markings & purpose controls.** Classification/PII markings on
>    concepts/properties propagate into the merged, cited answer (Palantir-style), with
>    PBAC purpose scoping; source least-privilege controls remain beneath.
>
> **Envelope changes (additive).** A distinct `refused: insufficient entitlement` status
> reason and a `withheld_sources` field; every access decision is cited in the retrieval
> path; a citation a user cannot read is redacted (citations pass the same policy as
> bindings).
>
> **Non-goals for the early phase.** No ontology-based access control (OBAC) in P1–P2;
> the service-identity model (CC-7) stands until this phase. Multi-tenant isolation, if
> required, precedes user-level entitlement.
>
> **Decision record:** ADR-0004 — Identity planes & OBAC enforcement points (leg vs
> reassembly, citation redaction, refusal classes, ReBAC vs ABAC).

---

### Sources
Palantir Foundry security docs (overview, markings, CBAC, object/property security
policies, restricted views) + the PBAC blog; RFC 8693 (token exchange); RFC 7644 (SCIM);
OpenID Connect Core; NIST SP 800-162 (ABAC); OASIS XACML 3.0; OWASP LLM06:2025 +
confused-deputy literature (Hardy); Trino / Snowflake / Databricks / BigQuery /
SQL Server / Apache Ranger / Immuta / OPA / AWS Cedar docs; Google Zanzibar (USENIX
ATC '19) + OpenFGA. Full inline URLs above. Items the research flagged as **not**
landed to an exact primary this session (mechanism described, cite pending): the
AIP-specific security-docs page, a Denodo passthrough-credentials doc, a concrete
partial-results/`_shards.failed`-style URL, and the Entra On-Behalf-Of doc.
