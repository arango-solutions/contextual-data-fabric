---
title: "Module 16 — Identity Delegation & Source Trust — Specification"
module: 16-identity-delegation
type:
  - internal
  - module-spec
status: draft
version: 0.1
owner: Arthur Keen
building_block: Query
depends_on_modules: [M1, M5, M7, M8, M10, M11, M12]
depends_on_repos: []
requires_repo_enhancements: []
phase_intro: 3
related:
  - "[[contextual-data-fabric-prd]]"          # §10.7 CC-7, §10.13 CC-13
  - "[[contextual-data-fabric/docs/architecture/README|Architecture Index]]"
  - "[[module-05-federated-query-engine/adr/ADR-0004-identity-planes-and-policy-enforcement|ADR-0004]]"
  - "docs/research/vendor-auth-access-control-survey.md"
  - "docs/research/data-source-identity-mechanisms.md"
---

# Module 16 — Identity Delegation & Source Trust

> Own the path from the authenticated asker to the identity each source
> actually sees: the per-source trust level, the brokers that produce a
> source identity, the operator contract for provisioning each source, and
> the goldens that certify a source as delegation-ready.
> **Reconciles against:** [[contextual-data-fabric/docs/architecture/README|the super-module index]].

## 1. Purpose & responsibility

ADR-0004 fixed the contracts: an immutable `RequestContext` at the edge, a
`DelegationBroker` protocol, a `SourceExecutionContext` delivered to aware
executors, and a per-source `service | delegated` auth mode that fails closed.
It deliberately left every concrete broker, every source-side provisioning
step, and every certification test as "operator integration". Three modules
each hold a piece: M1 holds credentials, M8 makes policy decisions, M5 carries
the context. Nobody owns the middle, and the vendor survey
(`docs/research/vendor-auth-access-control-survey.md`) shows that the middle is
where every mature federation product invests: Denodo, Starburst and Dremio
ship one delegation implementation per source, with documented preconditions
and documented failure modes.

M16 owns that middle. It exists as a module rather than a sub-spec because a
source is "delegation-certified" the same way an executor is "live-proven":
per source, with evidence, with an operator checklist, and with a named owner.

## 2. Scope

**In scope:**
- The **trust-level contract** each source declares: `service`, `asserted`,
  or `delegated` (§3), including how each level is recorded in the envelope.
- **Brokers**, one per source kind, in the order of §7: Snowflake External
  OAuth, Databricks token federation, PostgreSQL `SET ROLE` via a native
  executor, ClickHouse per-principal identities, ArangoDB per-user tokens.
- The **asserted-identity adapters** (Snowflake session variable, Postgres
  `SET ROLE` without membership checks) as a cheaper intermediate level.
- **Admission rules** that depend on identity: token lifetime versus leg
  deadline; refusal of anonymous principals on non-service legs.
- **Cache scoping**: entitlement scope as part of every leg and assembly
  cache key (with M12).
- **Operator provisioning contracts**: one checklist per source stating what
  must exist on the source side before a level may be enabled, derived from
  `docs/research/data-source-identity-mechanisms.md` §9.
- **Certification goldens** in M10: fail-closed refusal, per-user policy
  firing, no fallback to service credentials, audit completeness.

**Out of scope:**
- Service-credential storage and rotation (M1 `SecretResolver`, FR-6 to FR-8).
- Policy decisions and ontology-seated rules (M8). M16 delivers the identity
  a decision is enforced under; it never decides.
- Provisioning the customer's IdP, STS, or source-native RLS and masking. M16
  documents and tests them; it does not run them.
- Multi-tenant isolation (ADR-0004 open item; separate control-plane work).

## 3. Interfaces (inputs / outputs)

- **Consumes:** `RequestContext` and `AuthenticatedPrincipal` from the ADR-0004
  edge; per-source `auth_mode` and base-identity reference from the M11
  manifest; `BaseSourceIdentity` and the operator-owned service credential from
  M1; the leg deadline from M5 admission.
- **Produces:** a `SourceIdentity` (subject, scheme, `expires_at`, opaque
  material) inside a `SourceExecutionContext` for M5 executors; a
  `trust_level` and `source_subject` per leg for the M7 envelope; refusals
  with named reasons to M5 admission; provisioning checklists to the
  deployment docs; certification results to M10.
- **Contract:**

| Level | Who authenticates the user at the source | Who enforces row and column policy | Source audit shows | When to use |
|---|---|---|---|---|
| `service` | Nobody; CDF connects as itself | CDF (M8), or the source keyed to CDF's role | The service identity | Baseline; sources without a delegation door; local development |
| `asserted` | Nobody; CDF connects as itself and states the principal through a channel the source's policies read (Snowflake session variable; Postgres `SET ROLE` when the connecting role holds membership) | The source, trusting CDF's assertion | The service identity plus the asserted subject where the source records it | Customer wants source-native RLS per user without an IdP federation project |
| `delegated` | The source, from a token or ticket the broker obtained for that user (RFC 8693 exchange, token federation, Kerberos S4U2proxy) | The source, for the authenticated user; CDF re-checks as defense in depth | The user | Production; any source with an external OAuth or token-federation door |

`SourceAuthMode` in `src/cdf/connectors/delegation.py` currently admits
`service | delegated`; FR-2 extends it. Every level is recorded per leg in the
envelope, because the three carry different audit meanings and a reader of a
cited answer must be able to tell which applied.

## 4. Functional requirements

- **FR-1 (P3, shipped):** `service` mode with fail-closed `delegated` seams,
  per ADR-0004 and M1 FR-9. M16 adds the published **read-only role per
  source**: exact grant statements, per-identity timeouts and limits, and the
  network allowlist shape, taken from the data-source reference. A source is
  not "service-certified" until its role is published and a golden proves the
  role cannot write or read outside the mapped schemas.
- **FR-2 (P3.7):** **`asserted` trust level.** Extend `SourceAuthMode`; add a
  Snowflake adapter that sets a session variable to the principal's subject
  before each leg, and a Postgres adapter that issues `SET ROLE` for a role
  derived from the principal. Refuse any `asserted` or `delegated` leg whose
  principal is anonymous. The envelope marks the leg `asserted` and names the
  subject. This is the PuppyGraph pattern with honest labelling.
  **Guard (PJ, 2026-09-10):** the connector registry today refuses a
  non-identity-aware executor only when the mode equals `delegated`, and five
  other sites compare against the two literals. Adding `asserted` therefore
  inverts every guard to *refuse anything that is not `service`* unless the
  executor declares support for that level, so an unaware executor fails closed
  instead of silently running the leg as service (amendment point 2). The
  certification list catches this first; the guard is the backstop.
- **FR-3 (P3.7):** **Snowflake External OAuth broker.** An RFC 8693 client
  against the customer's IdP that exchanges the edge token for a Snowflake
  access token whose mapped claim resolves to the user and whose scope is
  `session:role:<ROLE>` or `session:role-any`. Preconditions the checklist
  must state: a security integration trusting the IdP, the user-mapping claim,
  allowed roles, row and masking policies written against `CURRENT_USER()` or
  `IS_ROLE_IN_SESSION()`, and a `TYPE=SERVICE` user for the service leg
  because password authentication for service users ends in October 2026.
- **FR-4 (P4):** **Databricks token-federation broker.** Exchange the edge JWT
  at `/oidc/v1/token` with `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`
  under an account-wide federation policy whose `subject_claim` equals the
  Databricks username. Service leg uses OAuth M2M; PATs are not offered.
- **FR-5 (P3.7 for `asserted`, P4 for `delegated`):** **PostgreSQL through
  Ontop, at every level.** Ontop remains the Postgres rewriter and executor
  (ADR-0001, amended 2026-09-10); per-user identity is delivered by changes in
  Ontop, contributed upstream. Ontop already carries HTTP headers into its
  `QueryContext` (`getUsername()`, roles, groups; Ontop PR #753) and its
  maintainer has asked for impersonation to be built on that object (Ontop
  discussion #884).
  - **`asserted` (P3.7):** extend Ontop's `JDBCStatementInitializer` (the
    Postgres subclass already sets fetch size per statement) to issue
    `SET ROLE "<role>"` derived from `QueryContext.getUsername()` on borrow and
    `RESET ROLE` on close, behind a config key. Postgres RLS then keys off
    `current_user` on the shared pool. Preconditions: the service role holds
    membership (with `SET`) in every user role; Ontop is reachable only from
    CDF; CDF sets the `x-user` header from the verified principal. Postgres's
    own log still shows the session user, which is why the envelope says
    `asserted`. This is the same pooling-safe answer for both `asserted` and
    the role-per-query case PJ raised.
  - **`delegated` (P4):** an identity-keyed `JDBCConnectionPool` in Ontop that
    opens connections as the user (password passthrough, PG18 `oauth` bearer
    with an operator-supplied validator, or delegated Kerberos), designed with
    Ontopic in #884 rather than alone.
  - **Fallbacks, named so nobody rediscovers them:** lenses-only filtering with
    `ontop_user()` (zero code, but policy duplicated into lenses); Ontop
    reformulates and CDF executes (demoted: the reformulation endpoint is
    dev-mode only and the SQL needs post-processing to match SPARQL results,
    per the maintainer); a native Postgres executor via `add-source-kind`
    (last resort: it forfeits the aggregation pushdown ADR-0005 grants only to
    Ontop and Arango).
  Until the `asserted` change merges upstream, CDF carries a fork build of the
  Ontop image under CC-9 pin discipline. Seams, maintainer position, design and
  upstream plan: `docs/research/ontop-per-user-identity.md`.
- **FR-6 (later):** **ClickHouse.** One ClickHouse user per principal in open
  source, or Cloud Enterprise JWT with a roles claim. The checklist must warn
  that users without a matching row policy read all rows by default.
- **FR-7 (P3.7):** **ArangoDB hub.** Per-user tokens are minted only inside
  the broker from the server JWT secret, which never leaves the broker
  process. Because ArangoDB has no document-level security, the hub leg is
  always fabric-enforced (M8) regardless of level; the envelope says so.
- **FR-8 (P3.7):** **Token-lifetime admission.** A `delegated` leg whose
  `SourceIdentity.expires_at` precedes the leg's absolute deadline is refused
  at admission with a named reason, never started. No broker may refresh a
  token mid-leg.
- **FR-9 (P4, with M12):** **Entitlement-scoped caches.** Every leg and
  assembly cache key includes the entitlement scope of the principal the
  result was produced for. A cache entry produced under `service` is never
  served to an `asserted` or `delegated` request.
- **FR-10 (P3.7):** **Operator provisioning checklists**, one per source
  kind, in the deployment docs, each ending with the commands that prove the
  source is ready. The M11 manifest records the certified level per source;
  a request for a higher level than certified is refused.
- **FR-11 (P3.7, with M10):** **Certification goldens.** For each certified
  source: two test principals receive different rows for the same question;
  an anonymous principal is refused with `refused: insufficient entitlement`;
  removing the broker makes the leg fail closed, never fall back to service;
  the envelope carries level, subject, scheme and expiry and never a secret.
- **FR-12 (P3.7):** **Audit.** Every leg records trust level, source subject,
  scheme and expiry in telemetry. Under `service` and `asserted` the source's
  own audit shows CDF, so CDF's record is the only per-user record and must be
  retained accordingly.

## 5. Non-functional requirements

Fail closed everywhere: no level ever degrades to a lower one silently. No
secret in `RequestContext`, envelope, telemetry, logs, or cache keys. Broker
exchanges are bounded by the leg deadline and counted against CC-6 latency
budgets. Determinism of the gate is preserved: certification goldens run with
an injected offline STS and IdP, and the live variants are optional legs as
with other live-proven executors.

## 6. Dependencies

- **Modules:** M1 (credentials, `BaseSourceIdentity`), M5 (admission, executors,
  `SourceExecutionContext`), M7 (envelope fields), M8 (decisions, hub
  enforcement), M10 (goldens), M11 (per-source auth mode and certified level),
  M12 (cache keys).
- **External:** the customer's OIDC IdP with an RFC 8693 token endpoint;
  Snowflake security integrations; Databricks federation policies; source
  RLS and masking policies. For demos, a CDF-run Keycloak may stand in for
  the IdP and STS; that choice is open question 2.
- **Evidence base:** `docs/research/vendor-auth-access-control-survey.md`,
  `docs/research/data-source-identity-mechanisms.md`,
  `docs/research/sql-federation-vendor-security-profiles.md`,
  `docs/architecture/access-control-research.md`.

## 7. Phase mapping

- **P3 (shipped):** `service` mode, ADR-0004 seams, fail-closed `delegated`.
- **P3.7 (this module's first increment):** FR-1 publication, FR-2 asserted
  level for Snowflake and Postgres, FR-3 Snowflake broker, FR-7, FR-8, FR-10
  for Snowflake, FR-11 and FR-12.
- **P4:** FR-4 Databricks, FR-5 Postgres native executor, FR-9 with M12.
- **Later:** FR-6 ClickHouse; multi-tenant isolation with the control plane.

## 8. Acceptance criteria / demo

The same seed question asked by two authenticated test users returns
different Snowflake rows, the envelope shows the Snowflake leg at `delegated`
with each user's subject, and Snowflake's own query history shows the two
users, not the service account. The question asked anonymously is refused
with `refused: insufficient entitlement`. Stopping the broker turns the leg
into a fail-closed refusal in the gate, with the service-mode golden still
green beside it.

## 9. Open questions

1. **Tier or default?** Every surveyed vendor gates SSO and delegation behind a
   paid edition. The product-strategy PRD should decide whether `delegated` is
   a tier.
2. **Demo STS.** Customers bring an IdP; demos need one. Keycloak in the
   compose stack, or an injected offline broker only?
3. ~~**Postgres route.**~~ Resolved 2026-09-10: Ontop stays; `asserted` via the
   statement-initializer change, `delegated` via an identity-keyed pool in
   Ontop (#884). See FR-5 and ADR-0001's amendment. Remaining sub-question:
   who owns the upstream PR and the interim fork build.
4. **Reconciliation with M8.** When the ontology policy and the source policy
   disagree, which wins and how is the disagreement surfaced? Denodo and
   Immuta treat this as a product feature; M8 open question 3 should become a
   section.
5. **ArangoDB secret custody.** The server JWT secret is root-equivalent. Does
   the broker run in the same process as the fabric or beside it?
