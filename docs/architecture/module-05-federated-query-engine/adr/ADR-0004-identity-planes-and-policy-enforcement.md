---
title: "ADR-0004 — Identity planes and policy enforcement"
adr: 0004
module: 05-federated-query-engine
status: accepted
date: 2026-08-05
deciders: ["Arthur Keen"]
related:
  - "[[ADR-0001-conceptual-query-language|ADR-0001]]"
  - "[[contextual-data-fabric-prd|PRD]] §10.13"
  - "[[../../module-08-governance-obac/specification|M8 specification]]"
---

# ADR-0004 — Identity planes and policy enforcement

**Status:** Accepted and implemented through the P3 WP-17/WP-18 query-policy
layer. External identity, OpenFGA service operation, tuple synchronization, and
source-native policy provisioning remain operator integrations.

## Context

The fabric has two different actors. A steward or build agent publishes
ontology, mapping, catalog, and policy artifacts. An asker or query agent uses
those published artifacts to retrieve data. Conflating these identities would
let query-time callers inherit build-time authority.

The current runtime connects to every source with a read-only service identity
and historically accepted anonymous HTTP/MCP requests. That remains useful for
local development, but it cannot safely support per-user source policy or
future ontology-based authorization. In particular, bind-join seeds and
citations can disclose data across source boundaries.

## Decision

### Identity planes and request contract

Steward/build and asker/query are separate identity planes. This ADR implements
only the asker/query runtime baseline; steward authentication and publication
authorization are separate control-plane work.

HTTP and authenticated MCP transports accept generic OIDC JWT bearer tokens.
The verified `(issuer, subject)` pair is the principal identity; client ID,
scopes, roles/groups, tenant, and an allowlisted safe claim subset are
attributes, not alternate identities. Tokens are validated at the edge and
then discarded. They are never stored or serialized in the immutable
`RequestContext`, answer envelope, telemetry, logs, exceptions, or temporary
assembly data.

Every query receives an explicit immutable `RequestContext` containing a
principal, normalized request and trace IDs, policy-controlled purpose, an
absolute deadline, and the `query` identity-plane marker. Service, planner,
executor, and connector calls carry that context explicitly rather than using
thread-local or process-global identity. Anonymous/dev is a named principal,
not a missing value. Development remains anonymous by default; production can
require authentication.

The deployment default is one enterprise tenant. A normalized tenant claim is
supported now so a future multi-tenant deployment cannot silently ignore it,
but this ADR does not claim tenant isolation. A multi-tenant service requires
separate tenant-bound IdP configuration, policy stores, credential brokering,
catalog isolation, and tests before enablement.

### Authentication

The verifier supports operator-configured issuers, audiences, allowed
algorithms, JWKS URI/cache bounds, and clock-skew policy. It validates
signature, issuer, audience, `exp`, `nbf`, `iat`, and non-empty `sub`, and
strictly normalizes tenant, scopes, roles, and groups. Tests inject keys or a
decoder and do not use the network.

**External dependency:** an operator must provision and configure the OIDC IdP,
issuer metadata/JWKS endpoint, audience, signing algorithms, key rotation, and
claim mappings. CDF does not provision an IdP or infer trust settings.

### Authorization and enforcement

The centralized policy-decision point is an OpenFGA-compatible ReBAC service,
because ontology and organization permissions are relationship-shaped. It is
layered over source-native RLS, masking, and least-privilege controls, not a
replacement for them. The policy contract supports `allow`, `rewrite`, and
`deny` both before source dispatch and after reassembly:

- preflight may remove a projection, inject an authorized row predicate, scope
  bind seeds, withhold a leg, or deny the request;
- postflight rechecks bindings, source objects, citations, and disclosure
  metadata before returning the envelope.

The runtime now provides frozen JSON-safe authorization contracts, a
deterministic catalog PDP, and an OpenFGA-compatible check client with an
explicit store, model, relationship, bounded timeout, and fail-closed
unavailability behavior. Preflight evaluates source/concept/property and
filter/join/projection uses before optimization or admission; it can inject
principal-bound `VALUES`/`FILTER` row scope, register post-join masks, withhold
optional dropped data, or deny before any source call. Returned rows are
scope-verified before resolution, assembly, joins, or bind-seed creation.
Postflight repeats the PDP decision, refuses policy/context drift or missing
evidence, applies redact/HMAC/drop after joins, and governs citations and
introspection.

`CDF_POLICY_BACKEND=none` is retained only as an explicit development/legacy
mode. `catalog` requires the authoritative M11 manifest; `openfga` composes the
same catalog rewrites with remote relationship checks. `CDF_POLICY_REQUIRED`
forbids the none backend.

Citations are governed data. A caller may not receive a citation, native query,
source object, row count, or as-of detail that policy would withhold. Bind-join
`VALUES` seeds are also governed data: only authorized keys may cross into a
later source, and retrieval/citation rendering must redact seed values unless
explicitly allowed. Policy decisions happen before seed generation and again
before citations leave the service.

### Source delegation

Each source declares `service` or `delegated` authentication mode. Service mode
preserves the existing least-privilege connector behavior. Delegated mode uses
a `DelegationBroker` protocol to exchange the authenticated principal, logical
source, and operator-owned base identity for a short-lived, secret-safe
`SourceIdentity`, following RFC 8693 where supported or a source-specific
adapter contract where required. Delegated source identity is passed in an
explicit `SourceExecutionContext`.

Delegated mode fails closed if no broker exists or the selected adapter cannot
consume delegated identity. It must never fall back to service credentials,
because that would recreate a confused deputy.

**External dependencies:** CDF does not implement or provision an RFC 8693 STS,
Snowflake external OAuth/security integrations, Postgres role/impersonation
mapping, ClickHouse user mapping, Arango token exchange, or source-native
RLS/masking policy. Operators and source adapters must supply and test those
systems before selecting delegated mode.

## Consequences

- Identity is authenticated once and propagated as immutable, secret-free
  context through concurrent execution legs.
- The runtime can remain backward compatible for local development while
  production deployments can require authentication.
- Service-mode restricted-field masking or row scoping is refused unless the
  manifest explicitly authorizes the fabric as trusted PEP for that operation.
- The OpenFGA client is implemented, but CDF does not provision or operate an
  OpenFGA service, model, store, or tuples.
- A delegated configuration cannot silently obtain broader service-account
  authority.
- Build-plane identity, multi-tenant isolation, external IdP/STS deployment,
  source policy configuration, and OpenFGA policy operation remain explicit
  dependencies rather than simulated capabilities.

## Amendment 2026-09-09 — trust levels, admission and cache invariants, ownership

**Trigger:** the vendor authentication survey
(`docs/research/vendor-auth-access-control-survey.md`) and the data-source
reference (`docs/research/data-source-identity-mechanisms.md`).

1. **Three trust levels, not two.** `SourceAuthMode` gains `asserted` between
   `service` and `delegated`. Under `asserted` the fabric connects as itself and
   states the authenticated principal through a channel the source's own
   policies read (Snowflake session variable read by row access policies;
   Postgres `SET ROLE`). The source enforces per user but has not authenticated
   the user and its audit records the service identity. The level is recorded
   per leg in the envelope so a reader can tell which applied. Anonymous
   principals are refused on `asserted` and `delegated` legs.
2. **No silent degradation.** A request for a higher level than the source is
   certified for is refused; a level never falls back to a lower one. This
   generalizes the existing rule that `delegated` never falls back to service.
3. **Token-lifetime admission.** A `delegated` leg whose `SourceIdentity.expires_at`
   precedes the leg's absolute deadline is refused at admission with a named
   reason. Brokers do not refresh mid-leg (Starburst documents the same
   constraint for OAuth passthrough).
4. **Entitlement-scoped caches.** Leg and assembly cache keys include the
   entitlement scope of the principal the entry was produced for; an entry
   produced under `service` is never served to an `asserted` or `delegated`
   request (Denodo and Dremio document this failure mode).
5. **Hub is always fabric-enforced.** ArangoDB has no document-level security;
   the hub leg is enforced by the fabric at every level and the envelope says so.
6. **Ownership.** Brokers, the `asserted` adapters, per-source provisioning
   checklists and certification goldens are **M16 (Identity Delegation & Source
   Trust)**. M1 keeps service credentials and `BaseSourceIdentity`; M8 keeps
   decisions; M5 carries `SourceExecutionContext`. The "operator integrations"
   this ADR left open now have a module and a first increment (P3.7: Snowflake
   External OAuth broker; asserted level for Snowflake and Postgres).

