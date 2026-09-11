---
title: "Data-source identity mechanisms — what each platform offers a federation engine for authentication, delegation and least-privilege read"
type:
  - internal
  - research
  - reference
date: 2026-09-09
status: draft — for team review
related:
  - "docs/research/vendor-auth-access-control-survey.md (the synthesis this feeds)"
  - "docs/research/sql-federation-vendor-security-profiles.md"
  - "docs/research/graph-virtualization-vendor-security-profiles.md"
  - "docs/research/puppygraph-security-and-schema-profile.md"
  - "docs/architecture/module-05-federated-query-engine/adr/ADR-0004-identity-planes-and-policy-enforcement.md (service vs delegated source modes; DelegationBroker)"
  - "docs/contextual-data-fabric-prd.md §10.7 (CC-7 security floor and credential architecture)"
---

# What each data platform offers a federation engine

> **Scope.** The source side of the problem. For each platform CDF federates
> today (Snowflake, PostgreSQL, ClickHouse, ArangoDB) or is likely to be asked
> for (Databricks, BigQuery, object storage and Iceberg catalogs), this
> document records: the authentication methods available to a service, the
> delegation or on-behalf-of options, the minimum privilege set for read-only
> querying plus schema introspection, the source-native fine-grained controls
> and which identity they key off, and the per-identity cost and timeout
> guardrails. It closes with the standards the mechanisms rely on.
>
> **Why it matters for CDF.** ADR-0004 makes each source declare `service` or
> `delegated` mode and says CDF "does not implement or provision" the
> source-side pieces. This document is the checklist of what an operator has
> to provision, per platform, before selecting delegated mode, and of what the
> service-mode read-only role must contain.
>
> **Method.** Vendor documentation read on 2026-09-09. Claims found only in
> third-party coverage are marked `[secondary]`. "Not documented" means the
> cited pages were checked and say nothing. ArangoDB's rendered docs were
> Cloudflare-blocked to fetch tools, so its facts come from the docs-hugo
> source repository on GitHub.

---

## 0. The one-page comparison

| Platform | Service auth that will still work in 2027 | Delegation the source itself supports | Identity that RLS and masking key off | Per-identity guardrails |
|---|---|---|---|---|
| **Snowflake** | Key-pair (RSA JWT), PAT bound to one role, workload identity federation; **passwords blocked for `SERVICE` users by Oct 2026** | External OAuth: forward an IdP token whose mapped claim resolves to a Snowflake user and whose scope names the role | `CURRENT_ROLE()`, `CURRENT_USER()`, `IS_ROLE_IN_SESSION()`; mapping tables | `STATEMENT_TIMEOUT_IN_SECONDS` at user level; resource monitors only at account or warehouse; network policies per user |
| **Databricks** | OAuth M2M for service principals (PATs now labelled "legacy") | Account-wide token federation: RFC 8693 exchange of the user's IdP JWT for a Databricks token *as that user* | The authenticated principal; row filters and masks are UDFs on the SQL path; cross-engine ABAC now enforces server-side for external readers | `STATEMENT_TIMEOUT` at session, warehouse, workspace; no per-user timeout |
| **BigQuery** | Application Default Credentials, workload identity federation, service-account impersonation (keys discouraged) | Hold the user's own OAuth token, or domain-wide delegation with the user's email in the JWT `sub` | `SESSION_USER()` in row access policies; policy tags for columns | `maximumBytesBilled` per job; `QueryUsagePerUserPerDay` custom quota |
| **PostgreSQL** | `scram-sha-256`, certificate, GSSAPI, LDAP; `oauth` in PG18 with a validator module you supply | `SET ROLE` (membership with `SET` option) changes `current_user`; `SET SESSION AUTHORIZATION` is superuser-only and resettable by the client | `current_user` in RLS policies, so `SET ROLE` switches the RLS identity | `ALTER ROLE … SET statement_timeout`, `CONNECTION LIMIT`, per-database overrides |
| **ClickHouse** | `sha256_password`, `bcrypt`, LDAP, Kerberos, SSL certificate, SSH key, JWT (Cloud Enterprise) | None in open source; Cloud Enterprise JWT creates ephemeral users with roles from a token claim | Current user and enabled roles in row policies; users with no matching policy read all rows by default | Settings profiles with constraints on `max_execution_time`, `max_memory_usage`, `readonly`; quotas keyed by user |
| **ArangoDB** | JWT from `/_open/auth`; Basic | Any holder of the server JWT secret can mint a per-user token, which is impersonation with a root-equivalent credential | None: permissions stop at collection level; no document-level security | Server-wide `--query.max-runtime` and memory limits only; no per-user quota |
| **S3 / Glue / Lake Formation** | IAM roles via STS `AssumeRole`, 15 minutes to 12 hours, 1 hour under role chaining | Lake Formation credential vending: engine registered with a session tag, receives scoped-down credentials and is trusted to apply column and cell filters itself | Lake Formation grants to the principal; enforcement is distributed to the engine | Session duration; session policies |
| **Iceberg REST / Polaris / Open Catalog** | OAuth client credentials to the catalog; `X-Iceberg-Access-Delegation: vended-credentials` | Token exchange grant in the REST spec (`subject_token`, `actor_token`); the catalog's own `/v1/oauth/tokens` is deprecated in favour of an external server | `TABLE_READ_DATA` privilege on the catalog role; vended credentials are read-only and table-scoped | Credential `expires-at` |
| **ADLS Gen2** | Entra ID with Azure RBAC or POSIX ACLs; account and service SAS bypass identity entirely | User delegation SAS bounded by the signing principal's permissions; `suoid` makes storage do the ACL check as the end user | RBAC first, then ACLs; ACLs cannot restrict what RBAC granted | SAS expiry, at most 7 days for the delegation key |

---

## 1. Snowflake

### 1.1 Authentication methods

| Method | For | Lifetime and rotation | Notes |
|---|---|---|---|
| Password with MFA | `TYPE=PERSON` users only | Phased rollout: Sep 2025 to Jan 2026 MFA for Snowsight passwords; May to Jul 2026 all new humans MFA everywhere and new non-humans must be `TYPE=SERVICE`; **Aug to Oct 2026 all human users MFA with no exceptions and every `LEGACY_SERVICE` user must migrate to `SERVICE`, which cannot use passwords** | A service connecting by password stops working in this window |
| Key-pair (RSA, JWT) | Services | At least 2048-bit; `ALTER USER … SET RSA_PUBLIC_KEY` and `RSA_PUBLIC_KEY_2` for overlap rotation, or named key pairs with `ADD KEY PAIR` and `ROTATE KEY PAIR` (24-hour grace) | Needs `MODIFY PROGRAMMATIC AUTHENTICATION METHODS` or ownership of the user. CDF's Snowflake executor should be on this |
| Snowflake OAuth | Humans via browser | Access token about 10 minutes; refresh token default 90 days | No client-credentials flow; `ACCOUNTADMIN`, `SECURITYADMIN`, `ORGADMIN` blocked by default |
| External OAuth | Humans or services | Whatever the IdP issues | Okta, Entra ID, PingFederate, custom. Token-to-user mapping via `EXTERNAL_OAUTH_TOKEN_USER_MAPPING_CLAIM` onto `login_name` or email. **Scope must be `session:role:<ROLE>` or `session:role-any`; with no scope the connection fails.** This is the delegation door |
| Programmatic access token | `PERSON`, `SERVICE`, `SERVICE_AGENT` | Default 15 days, max 365, set by authentication policy | Service users must bind a PAT to exactly one role; presented as the password; user must sit under a network policy unless relaxed |
| Workload identity federation | `SERVICE` and `SERVICE_AGENT` only | Signing keys cached up to 60 minutes | AWS IAM role ARN, Entra ID, GCP service account, generic OIDC including SPIFFE, EKS/AKS/GKE. `ALTER USER … SET WORKLOAD_IDENTITY (…)`. The keyless option for a CDF deployment in a cloud |
| SPCS in-container token | Services inside Snowpark Container Services | Refreshed every few minutes, each valid up to an hour | Runs as the service user with the owner role. Ingress carries `Sf-Context-Current-User` but acting as that user is not documented |
| Authentication policies | Account or user | n/a | Restrict allowed `AUTHENTICATION_METHODS`, client types, integrations, MFA enrollment, PAT expiry |

### 1.2 Delegation

External OAuth is the only sanctioned path for a middle tier to act as a
user. The engine forwards an IdP-issued access token; Snowflake resolves the
mapped claim to a user and the `session:role:*` scope to a role and evaluates
every policy under that pair. Snowflake's docs say nothing about how the
engine obtains a per-user token; that is an RFC 8693 exchange at the IdP,
which is exactly what ADR-0004's `DelegationBroker` describes. There is no
SQL-level impersonation, no `SET ROLE` as another user. PuppyGraph's
session-variable trick (see its profile) is a workaround that runs under the
service identity and asserts a username in `GETVARIABLE()`.

### 1.3 Minimum read and introspection role

```sql
CREATE ROLE cdf_read_only;
GRANT USAGE ON WAREHOUSE cdf_wh TO ROLE cdf_read_only;
GRANT USAGE ON DATABASE d1 TO ROLE cdf_read_only;
GRANT USAGE ON SCHEMA d1.s1 TO ROLE cdf_read_only;
GRANT SELECT ON ALL TABLES IN SCHEMA d1.s1 TO ROLE cdf_read_only;
GRANT SELECT ON FUTURE TABLES IN SCHEMA d1.s1 TO ROLE cdf_read_only;
```

`SELECT` on a view suffices without access to its underlying tables.
`REFERENCES` allows `DESCRIBE` and `SHOW` of structure without data.
`INFORMATION_SCHEMA` returns only objects the current role holds a privilege
on, and `SHOW` requires at least one privilege on the object, so introspection
needs no extra grant beyond the read grants. `SNOWFLAKE.ACCOUNT_USAGE` needs
`IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE` or one of the `*_VIEWER` database
roles and lags 45 minutes to 3 hours. Direct-to-user grants only take effect
with all secondary roles enabled.

### 1.4 Fine-grained controls

Row access policies and masking policies are SQL expressions that may call
`CURRENT_ROLE()`, `CURRENT_USER()`, `IS_ROLE_IN_SESSION()` (recommended, covers
the role hierarchy), `IS_DATABASE_ROLE_IN_SESSION()`, `INVOKER_ROLE()`, and may
join mapping tables. Creating needs `CREATE MASKING POLICY ON SCHEMA`; attaching
needs `APPLY MASKING POLICY`. Under service mode every policy sees CDF's role,
so per-user scoping cannot come from Snowflake unless the policy reads a
session variable CDF sets.

### 1.5 Guardrails

`STATEMENT_TIMEOUT_IN_SECONDS` and `STATEMENT_QUEUED_TIMEOUT_IN_SECONDS` are
settable at account, user, session and warehouse level; when warehouse and
session both set them the lowest non-zero wins; default 172,800 seconds.
Resource monitors attach only to the account or to warehouses, so a per-CDF
cost cap means a dedicated warehouse. Network policies apply at account, user
or security-integration level, and `NETWORK_POLICY` is the only user parameter
`SECURITYADMIN` may set.

## 2. Databricks

### 2.1 Authentication methods

| Method | For | Lifetime and rotation | Notes |
|---|---|---|---|
| Personal access token, page now titled "(legacy)" | Users, service principals | Unused tokens auto-revoked after 90 days; admins can cap lifetime or disable PATs | "Databricks recommends using OAuth instead of PATs" |
| OAuth M2M (client credentials) | Service principals | Secret up to 730 days, five secrets per principal; access token 1 hour | Endpoint `/oidc/v1/token`; scope `all-apis` or narrower. The right service credential for CDF |
| OAuth U2M (auth code with PKCE) | Humans | Access token 1 hour; refresh token lifetime not stated | Third-party apps register a custom OAuth app |
| Token federation (RFC 8693) | Users under an account-wide policy, or service principals under a federation policy | `expires_in` 3600, bounded by the incoming JWT's lifetime | `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, `subject_token=<IdP JWT>`; policy fields issuer, audiences, `subject_claim` (default `sub`, must equal the Databricks username or principal application ID), JWKS |
| JDBC to SQL warehouses | n/a | n/a | `AuthMech=3` PAT; `AuthMech=11` with `Auth_Flow` 0 (token passthrough, accepts an IdP JWT the driver exchanges automatically), 1 (M2M), 2 (U2M) |

### 2.2 Delegation

Account-wide token federation is a first-class on-behalf-of mechanism: a
middle tier holding the user's IdP JWT exchanges it for a Databricks token *as
that user*. There is no actor claim; the docs do not describe delegation
semantics beyond impersonation. Service principals cannot impersonate users.
For CDF this means the `DelegationBroker` for Databricks is a thin RFC 8693
client against Databricks itself, with the IdP already trusted by an
account-wide policy.

### 2.3 Minimum read and introspection

```sql
GRANT USE CATALOG ON CATALOG my_catalog TO `cdf-sp@example.com`;
GRANT USE SCHEMA  ON SCHEMA  my_catalog.my_schema TO `cdf-sp@example.com`;
GRANT SELECT      ON TABLE   my_catalog.my_schema.my_table TO `cdf-sp@example.com`;
-- only for engines that read files directly through Iceberg REST or temporary credentials:
GRANT EXTERNAL USE SCHEMA ON SCHEMA my_catalog.my_schema TO `cdf-sp@example.com`;
```

`EXTERNAL USE SCHEMA` can be granted only by the catalog owner and is excluded
from `ALL PRIVILEGES`; the metastore's "External data access" switch is off by
default. `BROWSE` lets a principal see metadata without `USE`. Each catalog has
an `information_schema`, privilege-filtered, needing no separate grant. The
principal also needs `CAN USE` on a SQL warehouse. Credential vending is `POST
/api/2.0/unity-catalog/temporary-table-credentials` with `operation: READ`,
returning cloud-specific short-lived credentials and an `expiration_time`;
the duration policy is not documented.

### 2.4 Fine-grained controls

Row filters and column masks are SQL UDFs attached to tables, not views. They
have historically applied only on the SQL path, which is why PuppyGraph's
credential-vending reads bypass them. **Cross-engine ABAC** now enforces row
filters, column masks and tag policies server-side for external engines,
read-only, requiring external data access, `EXTERNAL USE SCHEMA`, managed
tables with catalog commits, OAuth M2M or PAT, and recent Iceberg or Delta
Spark readers. The identity is the authenticated principal. `current_user()`
is deprecated in favour of `session_user` and returns the principal's UUID for
service principals.

### 2.5 Guardrails and operations

`STATEMENT_TIMEOUT` defaults to 172,800 seconds with precedence session, then
warehouse, then workspace, then system; no per-user timeout exists.
`system.access.audit` records Unity Catalog actions with `user_identity`,
`service_name` and `action_name` for 365 days. Databricks recommends service
principals for integrations, with permissions through Unity Catalog grants and
groups.

## 3. Google BigQuery

### 3.1 Authentication methods

All methods yield roughly one-hour OAuth 2.0 access tokens. Application
Default Credentials are preferred, with the attached service account "the
preferred authentication method for code running on a Google Cloud compute
resource". Service-account keys work but Google recommends impersonation
(`roles/iam.serviceAccountTokenCreator`, default one hour, up to 12 with an
org policy) or workload identity federation (AWS, Azure, OIDC, SAML, X.509,
Kubernetes) with direct resource access recommended over impersonation.

### 3.2 Delegation

Domain-wide delegation lets a Workspace super-admin authorize a service
account to put a user's email in the JWT `sub` claim and act with *that
user's* permissions; propagation takes up to 24 hours. Otherwise per-user
on-behalf-of requires the user's own three-legged OAuth token.

### 3.3 Minimum read and introspection

`roles/bigquery.dataViewer` on the dataset or table, **plus**
`roles/bigquery.jobUser` on the project that runs and bills the job.
`roles/bigquery.metadataViewer` for schema only; `roles/bigquery.readSessionUser`
for the Storage Read API. `INFORMATION_SCHEMA.TABLES` and `COLUMNS` need
`bigquery.tables.get` and `bigquery.tables.list`; results are privilege-filtered
and each such query bills at least 10 MB. Authorized views let consumers read
without access to the source dataset while source RLS and column policies
still apply.

### 3.4 Fine-grained controls and guardrails

Row access policies (`CREATE ROW ACCESS POLICY … GRANT TO (…) FILTER USING
(…)`) evaluate against the querying principal, typically `SESSION_USER()`, do
not prune partitions, and are incompatible with the Storage Read API when they
contain subqueries. Column-level security is policy tags with "enforce access
control"; readers need `roles/datacatalog.categoryFineGrainedReader` or the
query errors. Cost: `maximumBytesBilled` per job and custom quotas
`QueryUsagePerDay` (default 200 TiB) and `QueryUsagePerUserPerDay`, on-demand
only, reset midnight Pacific.

## 4. PostgreSQL (docs version 18)

### 4.1 Authentication methods

`pg_hba.conf` methods: trust, reject, `scram-sha-256` (recommended), `md5`,
`gss` (Kerberos), `sspi`, `ident`, `peer`, `ldap`, `radius`, `cert`, `pam`, and
**`oauth`, new in PG18**. OAuth takes a bearer token, discovers the issuer, and
**requires a loadable validator module** (`oauth_validator_libraries`); none
ships with core. pg_hba options `issuer`, `scope`, `validator`, `map`, and
`delegate_ident_mapping`. Passwords expire only by `VALID UNTIL`.

### 4.2 Delegation

- `SET ROLE x`: the session user must be a member of `x` granted with the
  `SET` option (default true). It changes `current_user`, and RLS policies key
  off `current_user`, so **`SET ROLE` is a real per-user enforcement switch**
  for an engine that connects as a role with membership in every user role.
  `RESET ROLE` is always allowed, so the client must be trusted not to reset.
- `SET SESSION AUTHORIZATION`: superuser only, changes both identifiers, and
  the client can `RESET` it, so it is not a boundary against the client.
- Per-role `ALTER ROLE … SET` values apply at login only, not after `SET
  ROLE`, so statement timeouts must be set on the connecting role.
- `postgres_fdw`: non-superusers need password or delegated GSSAPI
  (`gssdelegation`); `use_scram_passthrough` on PG17+.

### 4.3 Minimum read and introspection

```sql
CREATE ROLE cdf_ro LOGIN;
GRANT CONNECT ON DATABASE d TO cdf_ro;
GRANT USAGE ON SCHEMA s TO cdf_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA s TO cdf_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA s GRANT SELECT ON TABLES TO cdf_ro;
-- column-level alternative: GRANT SELECT (c1, c2) ON t TO cdf_ro;
-- broad alternative: GRANT pg_read_all_data TO cdf_ro;  -- does NOT bypass RLS
ALTER ROLE cdf_ro SET statement_timeout = '30s';
ALTER ROLE cdf_ro CONNECTION LIMIT 10;
```

`information_schema` is per database and privilege-filtered; `pg_catalog`
shows all objects. This is the role CDF's Ontop container should be given
(PRD §10.7 already requires `statement_timeout` on the CC-7 role).

### 4.4 Fine-grained controls

`ALTER TABLE … ENABLE ROW LEVEL SECURITY` plus `CREATE POLICY p ON t TO role
USING (…)`. Superusers, `BYPASSRLS` roles, and owners (unless `FORCE ROW LEVEL
SECURITY`) bypass. Permissive policies OR together, restrictive ones AND.
`row_security = off` makes a query error rather than silently filter. Column
privileges via `GRANT SELECT (cols)`.

## 5. ClickHouse

### 5.1 Authentication methods

`CREATE USER … IDENTIFIED WITH` accepts `no_password`, `plaintext_password`,
`sha256_password`, `double_sha1_password`, `bcrypt_password`, `ldap SERVER`,
`kerberos`, `ssl_certificate CN|SAN`, `ssh_key`, `http SERVER`, and `jwt CLAIMS`;
multiple methods per user; `HOST` restrictions; `VALID UNTIL`; default roles;
settings profiles. ClickHouse Cloud JWT with a custom IdP (issuer, audience,
JWKS URL, roles claim default `clickhouse:roles`) is **Enterprise plan on
26.4 or later**; users are ephemeral (`JWT::<subject>::<hash>`).

### 5.2 Delegation

None in open source: no impersonation, no `SET ROLE` as another user. The
practical patterns are one ClickHouse user per principal, or Cloud Enterprise
JWT with roles carried in the token. Row policies apply "to the current user
and their enabled roles".

### 5.3 Minimum read and introspection

```sql
CREATE USER cdf_ro IDENTIFIED WITH sha256_password BY '…' HOST IP '10.0.0.0/8'
  SETTINGS readonly = 1, max_execution_time = 30, max_memory_usage = 4000000000;
GRANT SELECT ON db.* TO cdf_ro;   -- or SELECT(c1, c2) ON db.t
```

`SHOW DATABASES`, `SHOW TABLES` and `SHOW COLUMNS` are implicitly granted with
any privilege on the object. The `system` database is always readable, though
specific tables may need explicit grants and Cloud restricts some. SQL-driven
access requires `access_management = 1`.

### 5.4 Fine-grained controls and guardrails

`CREATE ROW POLICY p ON db.t USING cond [AS PERMISSIVE|RESTRICTIVE] TO …`,
`SELECT` only. **Users without a matching policy read all rows by default**
(`users_without_row_policies_can_read_rows`), a fail-open that mirrors
PuppyGraph's RLS. Settings profiles carry constraints (`MIN`, `MAX`, `READONLY`,
`CHANGEABLE_IN_READONLY`) on `max_execution_time`, `max_memory_usage` and
`readonly`; `readonly=1` forbids `SET`, `readonly=2` allows it; HTTP GET forces
`readonly=1`. Quotas are keyed by user, IP or client key over intervals with
caps on queries, rows read and execution time. This is the richest
per-identity guardrail set of the four current CDF sources.

## 6. ArangoDB 3.12

### 6.1 Authentication

HTTP Basic, or a JWT obtained from `POST /_open/auth` with username and
password, presented as `Authorization: Bearer`; lifetime
`--server.session-timeout`, default 3600 seconds. Server options
`--server.authentication` (true), `--server.jwt-secret-keyfile` or
`--server.jwt-secret-folder` for rotation, `--server.harden` (false).

### 6.2 Delegation

Anyone holding the server JWT secret can mint tokens. A **user token**
(`{"iss":"arangodb","preferred_username":"<user>",…}`) authenticates as that
user; a **superuser token** (`{"iss":"arangodb","server_id":"…"}`) bypasses
permissions. So a middle tier can impersonate any user, but only with a
root-equivalent credential. ADR-0004 lists "Arango token exchange" as an
external dependency; this is what it would be built on, and the secret must
never leave the trusted tier.

### 6.3 Minimum read and introspection

Database-level `ro` ("Access") plus collection-level `ro` ("Read Only");
wildcard `*` levels default to none for non-root users.
`PUT /_api/user/{user}/database/{db}` with `{"grant":"ro"}` and
`PUT /_api/user/{user}/database/{db}/{coll}` with `{"grant":"ro"}`; managing
users requires Administrate on `_system`.

### 6.4 Fine-grained controls and guardrails

Permissions stop at server, database and collection; **there is no
document-level security**, so any row scoping happens in AQL filters the middle
tier adds. Query limits are server-wide only: `--query.max-runtime` (0 means
none), `--query.memory-limit`, `--query.global-memory-limit`; no per-user
quota. For CDF's hub this means the fabric is the only enforcement point on
the graph side, in every mode.

## 7. Object storage and Iceberg catalogs

### 7.1 AWS

- **STS `AssumeRole`**: `DurationSeconds` from 900 seconds to the role maximum
  (1 to 12 hours, default 3600); role chaining caps at one hour; session
  policies intersect with the role policy; `ExternalId`, `SourceIdentity` and
  transitive session tags.
- **Glue Data Catalog read-only**: `glue:GetDatabase(s)`, `glue:GetTable(s)`,
  `glue:GetPartition(s)`, `glue:BatchGetPartition` on the catalog, database
  and table ARNs; ancestors required; table is the finest granularity.
- **Lake Formation credential vending**: register the S3 location with a role;
  grant the principal Lake Formation `SELECT` on the table plus IAM
  `lakeformation:GetDataAccess`. The engine calls
  `GetTemporaryGlueTableCredentials` with `SupportedPermissionTypes` such as
  `COLUMN_PERMISSION` and `CELL_FILTER_PERMISSION` and receives scoped-down
  keys with an expiration and a `VendedS3Path`. Third-party engines must be
  registered: assume an execution role carrying the session tag
  `LakeFormationAuthorizedCaller`, and the administrator must opt in to
  "Allow external engines to filter data". **Enforcement is distributed and
  fail-close: the engine is trusted to apply column and cell filters itself.**

### 7.2 Iceberg REST, Polaris, Snowflake Open Catalog

The REST spec lets a client send `X-Iceberg-Access-Delegation:
vended-credentials` and receive storage credentials in `LoadTableResult`
(`storage-credentials[]` with `expires-at`). The catalog's own
`/v1/oauth/tokens` endpoint is deprecated for removal in favour of an external
`oauth2-server-uri`, and supports the RFC 8693 token-exchange grant with
`subject_token` and `actor_token`. Polaris models principals, principal roles,
catalog roles and privileges; `TABLE_READ_DATA` "enables reading data from the
table by receiving short-lived read-only storage credentials", with
`TABLE_LIST`, `NAMESPACE_LIST` and `*_READ_PROPERTIES` for metadata. Snowflake
Open Catalog is managed Polaris with vending on by default. Unity Catalog's
Iceberg REST endpoint is covered in section 2.

### 7.3 Azure ADLS Gen2

Shared Key and account or service SAS bypass identity entirely: RBAC, ABAC
and ACLs do not apply. Entra ID paths: Azure RBAC (`Storage Blob Data Reader`
reads and lists everything), ABAC conditions, and POSIX ACLs (execute on each
directory plus read on the file). RBAC is evaluated first and ACLs cannot
narrow it. A **user delegation SAS** is signed with a key from `Get User
Delegation Key` (needs `generateUserDelegationKey`, for example `Storage Blob
Delegator`), lasts at most 7 days, is bounded by the signing principal's
permissions, and with `suoid` makes storage perform the ACL check as the end
user. This is the Hadoop and Ranger pattern and is revocable.

### 7.4 GCS

`roles/storage.objectViewer` (`storage.objects.get` and `list`),
`roles/storage.bucketViewer`, or `roles/storage.legacyBucketReader`, granted to
a service account or a workload-identity principal.

## 8. The standards underneath

- **OAuth 2.0 Token Exchange, RFC 8693.** `grant_type=
  urn:ietf:params:oauth:grant-type:token-exchange` with `subject_token`, optional
  `actor_token`, `audience` or `resource`, and `scope`. Impersonation is subject
  only; delegation adds the actor, expressed by the nested `act` claim and
  pre-authorized by `may_act`. Databricks token federation, Denodo pass-through,
  Stardog's Okta path and the Iceberg REST spec all implement this grant.
  ADR-0004 names it as the `DelegationBroker` contract.
- **OpenID Connect Core 1.0.** ID token with `iss`, `sub`, `aud`, `exp`,
  `iat`; authorization-code and hybrid flows; UserInfo. Snowflake External
  OAuth, Databricks federation policies, GCP workload identity, PG18 `oauth`
  and ClickHouse Cloud JWT all consume OIDC issuer and JWKS metadata. CDF's
  edge already validates OIDC JWTs (ADR-0004).
- **Kerberos constrained delegation (MS-SFU, S4U2self and S4U2proxy).** A
  service obtains a ticket to a back-end service in the user's name; the user
  need not forward a TGT and cannot detect it; targets are constrained by
  `msDS-AllowedToDelegateTo` or resource-based delegation. Denodo, Starburst
  and TIBCO rely on it; relevant to Postgres `gss` and ClickHouse `kerberos`.
- **SQL/MED user mapping (ISO/IEC 9075-9).** `CREATE USER MAPPING FOR {user |
  USER | CURRENT_ROLE | PUBLIC} SERVER s OPTIONS (…)` maps a local role to
  remote credentials per foreign server. Postgres and Db2 implement it;
  Denodo's `@{USER_NAME}` vault pattern and TIBCO's per-session credentials are
  the same idea.
- **SPIFFE.** `spiffe://<trust-domain>/<path>` identities as X.509 or JWT
  SVIDs obtained from a local Workload API without secrets. Snowflake and GCP
  workload identity federation accept JWT-SVIDs, which makes SPIFFE the keyless
  option for a CDF deployment in Kubernetes.

---

## 9. What this means for CDF, platform by platform

| Source | Service mode: what the read-only identity must be | Delegated mode: what the operator must provision before enabling it |
|---|---|---|
| **Snowflake** | A `TYPE=SERVICE` user with key-pair or workload identity, one role holding `USAGE` on warehouse, database and schema and `SELECT` on tables; `STATEMENT_TIMEOUT_IN_SECONDS` on the user; a dedicated warehouse if a resource monitor is wanted. **Password auth for this user stops working by Oct 2026** | An External OAuth security integration trusting the IdP; user mapping claim; `session:role:*` scopes; an RFC 8693 STS at the IdP that CDF's broker can call; row and masking policies written against `CURRENT_USER()` or `IS_ROLE_IN_SESSION()` |
| **PostgreSQL** (via Ontop) | The `cdf_ro` role above with `statement_timeout` and a connection limit; Ontop's `_FILE` secrets | Per-user Postgres roles with RLS keyed off `current_user`, with the `SET ROLE` issued **inside Ontop** per statement from its `QueryContext` (the `asserted` level; an upstream change to `JDBCStatementInitializer`), then an identity-keyed pool in Ontop for `delegated` (password passthrough, PG18 `oauth` with a validator module, or delegated Kerberos; Ontop discussion #884). Ontop stays the Postgres rewriter and executor — PRD §10.13, ADR-0001 amendment 2026-09-10 |
| **ClickHouse** | A `readonly=1` user with a settings profile capping time and memory, `HOST` restricted, `SELECT` on the mapped tables | One user per principal (open source) or Cloud Enterprise JWT with a roles claim; row policies per role, remembering the fail-open default for users without a policy |
| **ArangoDB** | A user with `ro` on the database and each collection | Minting per-user JWTs with the server secret held only by the broker, plus fabric-side AQL filters, because Arango has no document-level security. The hub is always fabric-enforced |
| **Databricks** (future) | A service principal with OAuth M2M, `USE CATALOG`, `USE SCHEMA`, `SELECT`, `CAN USE` on a warehouse; never `EXTERNAL USE SCHEMA` unless a file-reading leg exists | An account-wide federation policy trusting the IdP; the broker exchanges the user's JWT at `/oidc/v1/token`; row filters and masks apply on the SQL path |
| **BigQuery** (future) | Workload identity federation or an attached service account with `dataViewer`, `jobUser`, `readSessionUser`; `maximumBytesBilled` on every job | The user's own OAuth token, or domain-wide delegation; row access policies on `SESSION_USER()` |
| **Lakehouse files** (future) | Prefer catalog-vended credentials over stored keys | Understand that vending shifts enforcement to the engine (Lake Formation) or to a server-side filter (Unity cross-engine ABAC); a file-reading leg must declare which |

---

## Sources

Snowflake (all under https://docs.snowflake.com/): `en/user-guide/security-mfa-rollout` · `en/sql-reference/sql/create-user` · `en/user-guide/key-pair-auth` · `en/user-guide/oauth-snowflake-overview` · `en/user-guide/oauth-ext-overview` · `en/user-guide/oauth-intro` · `en/user-guide/programmatic-access-tokens` · `en/user-guide/workload-identity-federation` · `en/user-guide/authentication-policies` · `en/developer-guide/snowpark-container-services/spcs-execute-sql` · `en/developer-guide/snowpark-container-services/additional-considerations-services-jobs` · `en/user-guide/security-access-control-configure` · `en/user-guide/security-access-control-privileges` · `en/sql-reference/info-schema` · `en/sql-reference/account-usage` · `en/user-guide/security-row-intro` · `en/user-guide/security-column-intro` · `en/sql-reference/functions/is_role_in_session` · `en/user-guide/cost-controlling-controls` · `en/sql-reference/parameters` · `en/user-guide/resource-monitors` · `en/user-guide/network-policies` · `user-guide/opencatalog/overview`

Databricks (all under https://docs.databricks.com/aws/en/): `dev-tools/auth/pat` · `dev-tools/auth/oauth-m2m` · `dev-tools/auth/oauth-u2m` · `dev-tools/auth/oauth-federation` · `dev-tools/auth/oauth-federation-policy` · `dev-tools/auth/oauth-federation-exchange` · `integrations/jdbc-oss/authentication` · `admin/users-groups/service-principals` · `data-governance/unity-catalog/manage-privileges/privileges` · `external-access/admin` · `external-access/iceberg` · `external-access/cross-engine-abac` · `sql/language-manual/sql-ref-information-schema` · `compute/sql-warehouse/` · `data-governance/unity-catalog/filters-and-masks/` · `sql/language-manual/functions/current_user` · `sql/language-manual/parameters/statement_timeout` · `admin/system-tables/audit-logs` · https://docs.databricks.com/api/workspace/temporarytablecredentials/generatetemporarytablecredentials

Google: https://docs.cloud.google.com/bigquery/docs/authentication · https://docs.cloud.google.com/bigquery/docs/access-control · https://docs.cloud.google.com/bigquery/docs/information-schema-intro · https://docs.cloud.google.com/bigquery/docs/authorized-views · https://docs.cloud.google.com/bigquery/docs/row-level-security-intro · https://docs.cloud.google.com/bigquery/docs/column-level-security-intro · https://docs.cloud.google.com/bigquery/docs/best-practices-costs · https://docs.cloud.google.com/bigquery/docs/custom-quotas · https://docs.cloud.google.com/iam/docs/workload-identity-federation · https://docs.cloud.google.com/iam/docs/create-short-lived-credentials-direct · https://developers.google.com/identity/protocols/oauth2/service-account#delegatingauthority · https://docs.cloud.google.com/storage/docs/access-control/iam-roles

PostgreSQL: https://www.postgresql.org/docs/current/auth-methods.html · https://www.postgresql.org/docs/18/auth-oauth.html · https://www.postgresql.org/docs/current/sql-set-role.html · https://www.postgresql.org/docs/current/sql-set-session-authorization.html · https://www.postgresql.org/docs/current/sql-grant.html · https://www.postgresql.org/docs/current/predefined-roles.html · https://www.postgresql.org/docs/current/sql-alterrole.html · https://www.postgresql.org/docs/current/infoschema-schema.html · https://www.postgresql.org/docs/current/ddl-rowsecurity.html · https://www.postgresql.org/docs/current/runtime-config-client.html · https://www.postgresql.org/docs/current/postgres-fdw.html · https://www.postgresql.org/docs/current/sql-createusermapping.html

ClickHouse: https://clickhouse.com/docs/sql-reference/statements/create/user · https://clickhouse.com/docs/operations/external-authenticators/jwt · https://clickhouse.com/docs/sql-reference/statements/grant · https://clickhouse.com/docs/operations/access-rights · https://clickhouse.com/docs/operations/system-tables/tables · https://clickhouse.com/docs/sql-reference/statements/create/row-policy · https://clickhouse.com/docs/operations/settings/constraints-on-settings · https://clickhouse.com/docs/operations/settings/permissions-for-queries · https://clickhouse.com/docs/operations/quotas · https://clickhouse.com/docs/operations/settings/settings-profiles

ArangoDB (docs source): https://github.com/arangodb/docs-hugo/blob/main/site/content/arangodb/3.12/develop/http-api/authentication.md · https://github.com/arangodb/docs-hugo/blob/main/site/content/arangodb/3.12/operations/administration/user-management/_index.md · https://github.com/arangodb/docs-hugo/blob/main/site/content/arangodb/3.12/develop/http-api/users.md · https://github.com/arangodb/docs-hugo/blob/main/site/data/3.12/arangod.json

AWS, Azure, Iceberg, Polaris: https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html · https://docs.aws.amazon.com/glue/latest/dg/security_iam_id-based-policy-examples.html · https://docs.aws.amazon.com/glue/latest/dg/glue-specifying-resource-arns.html · https://docs.aws.amazon.com/lake-formation/latest/dg/access-control-underlying-data.html · https://docs.aws.amazon.com/lake-formation/latest/APIReference/API_GetTemporaryGlueTableCredentials.html · https://docs.aws.amazon.com/lake-formation/latest/dg/how-vending-works.html · https://docs.aws.amazon.com/lake-formation/latest/dg/register-query-engine.html · https://raw.githubusercontent.com/apache/iceberg/main/open-api/rest-catalog-open-api.yaml · https://polaris.apache.org/releases/1.1.0/access-control/ · https://polaris.apache.org/releases/1.1.0/getting-started/using-polaris/ · https://learn.microsoft.com/en-us/azure/storage/blobs/data-lake-storage-access-control-model · https://learn.microsoft.com/en-us/rest/api/storageservices/create-user-delegation-sas

Standards: https://www.rfc-editor.org/rfc/rfc8693.html · https://openid.net/specs/openid-connect-core-1_0.html · https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-sfu/3bff5864-8135-400e-bdd9-33b552051d94 · https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-sfu/bde93b0e-f3c9-4ddf-9f44-e1453be7af5a · https://github.com/spiffe/spiffe/blob/main/standards/SPIFFE.md

Gaps left as not documented: Snowflake External OAuth middle-tier token caching; SPCS acting as the ingress user; Databricks temporary-credential duration policy and U2M refresh-token lifetime; a project-wide default for BigQuery maximum bytes billed; any ClickHouse open-source impersonation; ArangoDB per-user quotas.
