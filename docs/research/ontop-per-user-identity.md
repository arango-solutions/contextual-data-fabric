---
title: "Modifying Ontop to carry the user's identity to Postgres — seams, precedent, options and an upstream plan"
type:
  - internal
  - research
  - integration-analysis
date: 2026-09-10
status: draft — for team review
related:
  - "docs/architecture/module-16-identity-delegation/specification.md (FR-5 holds the requirement)"
  - "docs/architecture/module-05-federated-query-engine/adr/ADR-0001-conceptual-query-language.md (amendment 2026-09-10)"
  - "docs/contextual-data-fabric-prd.md §10.13 (the ranked option set)"
  - "docs/research/vendor-auth-access-control-survey.md"
  - "docs/research/data-source-identity-mechanisms.md §4 (PostgreSQL)"
  - "src/cdf/adapters/ontop.py (the CDF side of the seam)"
---

# Modifying Ontop to carry the user's identity to Postgres

> **The ask (Arthur, 2026-09-10).** Could we modify Ontop to pass the user
> login through, instead of routing around it with a native Postgres executor?
>
> **Answer in one line.** Yes. Ontop already carries HTTP headers into a
> per-query `QueryContext`, its maintainer has said in public that user
> impersonation should be built on that object, and the two code seams a
> change needs are small, Guice-injected classes. The cheap step gives CDF the
> `asserted` level in days; the full `delegated` step is a design conversation
> Ontopic has already opened.
>
> **Method.** Ontop source read from the `version5` branch on 2026-09-10
> (release 5.5.0, 2026-02-14, Apache-2.0), plus Ontop PR #753 and discussion
> #884 on GitHub. File paths below are relative to the Ontop repository. The
> one step not verified against source is marked as such in §4.1.

---

## 1. What Ontop already does

| Fact | Where | Why it matters |
|---|---|---|
| The endpoint copies every HTTP header of a SPARQL request into the query | `client/endpoint-core/.../processor/SparqlQueryExecutor.java`: `extractHttpHeaders(request)` then `connection.prepareQuery(QueryLanguage.SPARQL, query, httpHeaders)` | The identity CDF sends arrives inside Ontop with no new plumbing at the edge |
| A per-query `QueryContext` carries username, roles, groups, all headers, a salt and a query id | `core/model/.../evaluator/QueryContext.java`: `getUsername()`, `getRoles()`, `getGroups()`, `getRolesOrGroups()`, `getHttpHeaders()`, `getQueryId()` | This is the object the maintainer wants impersonation built on |
| With `ontop.authorization=true`, Ontop reads `x-user`, `x-roles`, `x-groups` and exposes `ontop_user()` and `ontop_contains_role_or_group('r')` to mappings and lenses | Ontop PR #753, merged 2023-09-06 | Row filtering per user is already possible **inside Ontop's SQL rewriting**, without touching the database identity |
| Every SQL statement passes through a per-dialect initializer | `engine/system/sql/core/.../connection/JDBCStatementInitializer.java`; `DefaultJDBCStatementInitializer.createAndInitStatement(Connection)`; `PostgresJDBCStatementInitializer.init()` sets `autoCommit=false` and the fetch size, `closeStatement()` issues `COMMIT` | A natural hook to run `SET ROLE` on borrow and `RESET ROLE` on return |
| Connections come from one pool opened with one credential | `JDBCConnector.getSQLPoolConnection()` → `JDBCConnectionPool.getConnection()`; implementations `HikariConnectionPool`, `TomcatConnectionPool`, `DummyJDBCConnectionPool`; settings `jdbc.pool.maxSize`, `jdbc.pool.initialSize`, `jdbc.pool.connectionTimeout`, `jdbc.pool.keepAlive`, `jdbc.pool.removeAbandoned`, `jdbc.fetchSize` | This is the seam for real per-user connections |
| The SQL reformulation endpoint exists only in dev mode | `client/endpoint/.../controllers/ReformulateController.java`: `@ConditionalOnExpression("${dev:false}")`, `/ontop/reformulate` | Reformulate-then-execute-in-CDF depends on a demo-only switch |
| The endpoint has no inbound authentication of its own | Endpoint controllers; open issue #356 asks for HTTPS and Basic auth on the CLI | Whatever identity Ontop trusts must be asserted by a caller Ontop trusts by network position |

## 2. What the maintainer has said

Discussion #884 (opened 2025-07-23 by a user with a Trino bearer-token patch)
is this exact request. Benoît Cogrel's reply (2025-07-26):

- "Ontop is missing the right abstractions to handle it."
- A general solution should support: a middleware above Ontop ("that's what
  we do at Ontopic"); Ontop being aware of the user identity to apply access
  control and adapt SQL (PR #753); **exchanging OAuth 2 tokens because the
  audiences of Ontop and the database differ**; **reusing JDBC connections per
  user**; passing the token to each source in its own convention ("no standard
  here").
- Named targets: Postgres 18 OAuth, Databricks, Snowflake.
- "I think the `QueryContext` object should be used and extended to carry the
  token around with the query."
- On reformulation as a service: "the generated SQL query doesn't perfectly
  match the SPARQL query as an extra post-processing step is needed. We are
  planning to add this capability next year."

Three things follow. The direction we want is the maintainer's direction, so
an upstream contribution is plausible rather than a permanent fork. The
maintainer's requirement list is the design checklist for the `delegated`
step. And reformulate-then-execute, which the vendor survey's first draft
proposed as route A, has a correctness caveat from the people who wrote the
reformulator.

## 3. The options, ranked

| # | Option | Level it delivers | Where the change lives | Effort | Verdict |
|---|---|---|---|---|---|
| 1 | **`SET ROLE` per statement from `QueryContext`** | `asserted` | Ontop: statement initializer + threading + one config key. CDF: send `x-user` from the verified principal | Days | **Do first, upstream** |
| 2 | **Identity-keyed connection pool** | `delegated` | Ontop: a `JDBCConnectionPool` keyed by identity, token exchange, per-user lifecycle, DB-specific credential passing | Weeks, and a design conversation | **Do with Ontopic in #884** |
| 3 | **Lenses-only filtering with `ontop_user()`** | `asserted`, enforced by Ontop not Postgres | Ontop config only (`ontop.authorization=true`) plus lens definitions | Hours | Available today; duplicates policy into lenses (M8's replication concern); useful as a bridge |
| 4 | **Ontop reformulates, CDF executes** | `asserted` or `delegated` | CDF: Postgres driver, execute reformulated SQL with `SET ROLE` or as the user | Days | Demoted: dev-mode endpoint, and the SQL needs post-processing to match SPARQL results |
| 5 | **Native Postgres executor** | either | CDF: `add-source-kind` template | Days | Last resort: forfeits the aggregation pushdown ADR-0005 grants only to Ontop and Arango |

## 4. Design of option 1: `SET ROLE` per statement

### 4.1 Ontop side

1. **Config key.** Proposed `ontop.postgres.setRoleFromUser=true` (name to be
   agreed upstream), default off, in `OntopSystemSQLSettings`. A second key
   for an allowlist regex on the role name, default `^[a-z_][a-z0-9_]*$`.
2. **Threading.** `createAndInitStatement(Connection)` receives no query
   context today. The executable query, or the statement-creation call in the
   SQL execution path, has to carry `QueryContext.getUsername()` to the
   initializer. **Unverified step:** which class between `prepareQuery` and
   `createAndInitStatement` is the right carrier; the reformulation path has
   the context, the execution path needs it added. This is the one piece of
   the change that needs a code-read before an estimate is firm.
3. **Initializer.** In `PostgresJDBCStatementInitializer.init()`, after the
   existing fetch-size setup: if the key is on and a username is present,
   validate against the allowlist, quote it as an identifier, execute
   `SET ROLE "<role>"`. If the key is on and no username is present, **fail
   the statement**; never fall through to the pooled identity (this is
   ADR-0004 amendment point 2 applied inside Ontop). In `closeStatement()`,
   execute `RESET ROLE` before the existing `COMMIT` and `close()`, so the
   pooled connection returns clean.
4. **Failure semantics.** A `SET ROLE` error (role missing, no membership)
   surfaces as a query error to the caller with the role name, not as an empty
   result.
5. **Tests.** Ontop's `test/docker-tests` already runs Postgres; add a case
   with two roles, one RLS policy, and two `x-user` values expecting different
   rows, plus a case asserting the failure when the header is absent.

### 4.2 Postgres side (operator checklist, M16 FR-10)

```sql
-- the pooled service role Ontop connects as
CREATE ROLE ontop_svc LOGIN PASSWORD '…' NOINHERIT;
GRANT CONNECT ON DATABASE crm TO ontop_svc;
-- one role per principal (or per group), each with the read grants
CREATE ROLE "alice@example.com" NOLOGIN;
GRANT USAGE ON SCHEMA public TO "alice@example.com";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO "alice@example.com";
-- membership with SET so the service role may switch, without inheriting
GRANT "alice@example.com" TO ontop_svc WITH SET TRUE, INHERIT FALSE;
-- RLS keys off current_user, which SET ROLE changes
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
CREATE POLICY per_owner ON accounts USING (owner = current_user);
ALTER ROLE ontop_svc SET statement_timeout = '30s';
```

`NOINHERIT` on the service role matters: without it the pooled identity
already holds the union of every user's privileges before any `SET ROLE`.

### 4.3 CDF side

- `OntopExecutor` (src/cdf/adapters/ontop.py) becomes identity-aware: when
  the `SourceExecutionContext` level is `asserted`, the transport adds
  `x-user: <principal subject>` (and `x-roles`, `x-groups` from the request
  context for lens use). Under `service` no header is sent, and Ontop's key is
  off in that deployment.
- The envelope records `asserted` for the leg (M7 FR-8).
- The connector-registry guard refuses an `asserted` leg against an executor
  that has not declared support (M16 FR-2).
- Network: Ontop's port is reachable from CDF only. The header is trusted by
  position, exactly as PuppyGraph's Snowflake variable is.

### 4.4 What option 1 does and does not give

- **Gives:** Postgres row-level security and column privileges evaluated for
  the real user on every leg, with no IdP federation to Postgres, no new
  executor, and Ontop's rewriting and aggregation pushdown intact.
- **Does not give:** Postgres authentication of the user. `session_user`
  stays `ontop_svc`, so Postgres's own log names the service account. The
  envelope's `asserted` label is the honest record of that.

## 5. Design sketch of option 2: identity-keyed pool

For the `delegated` level, following the maintainer's checklist:

- A `JDBCConnectionPool` implementation keyed by the identity in
  `QueryContext`, holding a small pool per identity with idle eviction.
- Credential acquisition per identity: password passthrough (the header
  carries it; weakest), **Postgres 18 `oauth`** (Ontop forwards a bearer
  token; the database needs an operator-supplied validator module), or
  delegated Kerberos (`gssdelegation`).
- **Token exchange** in Ontop or in the caller. The maintainer notes Ontop
  and the database have different audiences; CDF's `DelegationBroker` already
  exists to do the RFC 8693 exchange, so the simplest split is CDF exchanges
  and Ontop forwards.
- Lifetime: connections for an identity are dropped when its token expires;
  a query whose token expires before completion is refused before it starts
  (M16 FR-8, the same rule Starburst documents).
- The `SET ROLE` path stays as a fallback for identities the pool cannot
  authenticate, provided the envelope records the level actually used.

This is a proposal to write into #884, not to build alone. Ontopic wants the
capability for their platform and holds the design authority.

## 6. Upstream and interim plan

1. **Comment on #884** with option 1 as a scoped first PR, referencing the
   maintainer's own `QueryContext` suggestion, and option 2 as the follow-on
   design. Ask which class should carry the username to the initializer.
2. **Fork and build** the Ontop image from a branch carrying option 1 while
   the PR is in review. Pin the fork SHA in `deploy/pins` under CC-9
   discipline; the `integrate-owned-lib` skill's "check both mirrors first"
   step applies even though Ontop is external.
3. **Land the CDF side** (header, guard, envelope) behind the `asserted`
   level so the gate stays green with Ontop's key off.
4. **Certification goldens** (M16 FR-11): two principals, different Postgres
   rows through Ontop; header absent, refusal; key on with no `x-user`,
   refusal from Ontop.
5. When upstream ships in an Ontop release, drop the fork pin.

## 7. Open questions

1. Who owns the upstream PR and the interim fork build (M16 open question 3).
2. Role naming: one Postgres role per principal, or per group with the
   principal carried only in the envelope? Per-group scales better; per-
   principal gives finer RLS. The operator checklist should allow both.
3. Should CDF send `x-roles` and `x-groups` as well, so customers can use
   Ontop lenses (option 3) alongside `SET ROLE`? Cheap to send; the policy
   replication concern then belongs to the customer's lens author.
4. Does Ontop's `duplicateForNewQueryWithSameSalt()` path (used for
   multi-statement evaluation) preserve the username to every statement?
   Needs checking during the code-read in §4.1 step 2.

## Sources

- Ontop repository, `version5` branch, read 2026-09-10: `client/endpoint-core/src/main/java/it/unibz/inf/ontop/endpoint/processor/SparqlQueryExecutor.java` · `client/endpoint/src/main/java/it/unibz/inf/ontop/endpoint/controllers/SparqlQueryController.java` · `client/endpoint/src/main/java/it/unibz/inf/ontop/endpoint/controllers/ReformulateController.java` · `core/model/src/main/java/it/unibz/inf/ontop/evaluator/QueryContext.java` · `engine/system/sql/core/src/main/java/it/unibz/inf/ontop/answering/connection/JDBCStatementInitializer.java` · `engine/system/sql/core/src/main/java/it/unibz/inf/ontop/answering/connection/impl/DefaultJDBCStatementInitializer.java` · `engine/system/sql/core/src/main/java/it/unibz/inf/ontop/answering/connection/impl/PostgresJDBCStatementInitializer.java` · `engine/system/sql/core/src/main/java/it/unibz/inf/ontop/answering/connection/impl/JDBCConnector.java` · `engine/system/sql/core/src/main/java/it/unibz/inf/ontop/answering/connection/pool/JDBCConnectionPool.java` (+ `impl/{Hikari,Tomcat,Dummy}…`) · `engine/system/sql/core/src/main/java/it/unibz/inf/ontop/injection/OntopSystemSQLSettings.java` · `engine/system/core/src/main/java/it/unibz/inf/ontop/answering/connection/DBConnector.java`
- Ontop PR #753 "Extract user, group and role information from HTTP headers" (merged 2023-09-06): https://github.com/ontop/ontop/pull/753
- Ontop discussion #884 "user impersonation / bearer token authorization" (2025-07): https://github.com/ontop/ontop/issues/884
- Ontop issue #356 (CLI HTTPS and Basic auth, open): https://github.com/ontop/ontop/issues/356
- Ontop release 5.5.0 (2026-02-14), Apache-2.0: https://github.com/ontop/ontop/releases
- Ontop CLI reference (`--dev` and `/ontop/reformulate`): https://ontop-vkg.org/guide/cli
- PostgreSQL `SET ROLE`, `GRANT … WITH SET`, RLS and `current_user`: https://www.postgresql.org/docs/current/sql-set-role.html · https://www.postgresql.org/docs/current/sql-grant.html · https://www.postgresql.org/docs/current/ddl-rowsecurity.html · PG18 OAuth: https://www.postgresql.org/docs/18/auth-oauth.html
