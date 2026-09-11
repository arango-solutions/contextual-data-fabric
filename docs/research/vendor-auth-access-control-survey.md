---
title: "Authentication and access control in graph virtualization and data federation products — survey and implications for the Contextual Data Fabric"
type:
  - internal
  - research
  - competitive-analysis
date: 2026-09-09
status: draft — for team review
related:
  - "docs/research/puppygraph-security-and-schema-profile.md"
  - "docs/research/graph-virtualization-vendor-security-profiles.md"
  - "docs/research/sql-federation-vendor-security-profiles.md"
  - "docs/research/data-source-identity-mechanisms.md"
  - "docs/research/trino-federation-engine-evaluation.md"
  - "docs/architecture/access-control-research.md (CDF's own model; this survey is the market evidence for it)"
  - "docs/architecture/module-05-federated-query-engine/adr/ADR-0004-identity-planes-and-policy-enforcement.md"
  - "docs/contextual-data-fabric-prd.md §10.7 (CC-7), §10.8, §10.13"
  - "docs/architecture/module-08-governance-obac/specification.md"
---

# How graph virtualization and data federation vendors authenticate to sources and control access

> **The ask (Arthur, 2026-09-09).** Which authentication and access-control
> methods do the dominant graph-virtualization and federation vendors use for
> the data sources they support, how are those methods implemented, and how
> are they operated? Starting point: a Gemini research note on PuppyGraph and
> its competitors.
>
> **What this document is.** The synthesis. Four companion documents hold the
> per-vendor and per-platform evidence with a URL for every fact:
> PuppyGraph (its own profile), the graph-virtualization tier (Stardog, Timbr,
> RelationalAI, Ontop and GraphDB, Altair, Neo4j, Neptune, TigerGraph, Hasura,
> Apollo), the SQL-federation tier (Trino, Starburst, Denodo, Dremio, Athena,
> BigQuery, TIBCO, IBM, Fabric, postgres_fdw, Databricks, Snowflake), and the
> source platforms themselves (Snowflake, Databricks, BigQuery, PostgreSQL,
> ClickHouse, ArangoDB, object storage and Iceberg catalogs). This document
> cites those, not the vendors directly, except where a quote carries the
> argument.
>
> **Method.** Primary vendor documentation read on 2026-09-09 by four parallel
> research passes, then cross-checked against the Gemini note. Where Gemini
> was wrong the correction is recorded in the relevant profile and summarised
> in section 8 here. No number or claim in this document is invented; gaps are
> marked "not documented".

---

## 1. Executive summary

1. **Every product in both tiers connects to sources with a stored service
   credential by default.** The credential lives in a catalog, data-source or
   connection object owned by the engine. The mature products externalise it
   to a vault or environment variable; the younger ones (PuppyGraph, Timbr,
   Ontop) store it inline. This is CDF's `service` mode today, and it is the
   industry baseline, not a deficiency.

2. **Per-user identity reaching the source is a tier-two feature that only
   the SQL federators have finished.** Denodo, Starburst, Dremio, TIBCO and
   Microsoft Fabric each offer an opt-in delegated path per source. In the
   graph tier only Stardog does (OAuth pass-through to Snowflake, Databricks,
   Aurora and REST). PuppyGraph has a partial answer for Snowflake only. Timbr,
   RelationalAI, Ontop, GraphDB, Altair and Neo4j Virtual Graph have none.
   CDF's ADR-0004 delegated mode, once brokers ship, would put it level with
   Stardog and ahead of every other graph competitor.

3. **Delegation comes in four implementation families, and the field uses
   all four.**
   - *Passthrough*: the engine forwards the client's own credential or token
     (Trino extra-credentials, Starburst password, OAuth2 and JWT passthrough,
     Denodo pass-through, TIBCO, Fabric same-tenant shortcuts, Power BI SSO,
     Stardog OAuth pass-through).
   - *Impersonation by a trusted service*: the engine authenticates as itself
     and names the user (Hadoop proxyuser in Trino and Dremio, Starburst
     auth-to-local, Dremio as a Snowflake OAuth issuer, Denodo Kerberos
     constrained delegation, Postgres `SET ROLE`).
   - *Per-user credential mapping*: SQL/MED `CREATE USER MAPPING` in
     postgres_fdw and Db2, Denodo's `@{USER_NAME}` vault pattern, TIBCO
     per-session credentials.
   - *Asserted identity*: the engine stays on its service connection and
     tells the source who the user is through a side channel the source's
     policies read (PuppyGraph's Snowflake `SET PG_PROXY_USER` session
     variable). Cheapest to deploy, weakest in audit terms.
   The standards underneath are RFC 8693 token exchange, OIDC, Kerberos
   S4U2proxy, and ISO SQL/MED. ADR-0004 already names RFC 8693 as the broker
   contract, which is the correct choice: Databricks, Denodo, Stardog and the
   Iceberg REST spec all implement it.

4. **Fine-grained policy sits in one of two places, and vendors are honest
   about the trade.** Delegated products let the source's own row access and
   masking policies fire per user. Trusted-subsystem products re-implement
   row filtering inside the engine (PuppyGraph entitlement tables, Timbr
   policy objects, Hasura session-variable filters, Trino file rules or OPA,
   Starburst built-in access control, Denodo row restrictions). Nobody has
   solved policy replication; Denodo and Immuta sell tag-driven "define once"
   layers as the mitigation. CDF's ontology-seated policy (Module 08 OBAC) is
   the same idea with a stronger anchor.

5. **Schema introspection runs as the service account even when queries are
   delegated.** Denodo says so explicitly; Stardog does the same; PuppyGraph
   and Dremio introspect under the catalog credential. Only Trino's base JDBC
   path opens metadata connections with the session identity. This matches
   ADR-0004's two identity planes: build-time introspection belongs to the
   steward identity, not the asker.

6. **Operational practices that recur across the field:** read-only source
   roles with stated grant lists (Athena, Dremio, Stardog for BigQuery, and
   the platforms themselves); secret externalisation to a vault with rotation
   owned by the security team (Denodo, Dremio, Athena); audit logs on by
   default only in the cloud products and off by default in the self-hosted
   ones (Stardog); token lifetime treated as a query-execution constraint
   (Starburst); caches and accelerations disabled or specially populated under
   delegation (Denodo, Dremio); fail-closed IAM around the engine so that
   policy cannot be bypassed by calling the connector directly (Athena).

7. **Two platform deadlines shape any recommendation:** Snowflake blocks
   password authentication for all service users by October 2026, and
   Databricks now labels personal access tokens legacy. Key-pair, OAuth
   client credentials, and workload identity federation are the durable
   service credentials.

8. **Recommendations for CDF** (section 9): keep service mode as the shipped
   default and document its exact read-only role per source; build the first
   `DelegationBroker` for Snowflake External OAuth because that is where
   delegated mode has the clearest customer pull; evaluate PuppyGraph's
   asserted-identity pattern as an interim for Snowflake row policies,
   labelled honestly in the envelope; keep introspection on the steward
   identity; state fail-closed behaviour as a differentiator against
   PuppyGraph's and ClickHouse's fail-open defaults; and add a token-lifetime
   bound to per-leg deadlines before delegated mode ships.

---

## 2. The two tiers and what they federate

| Tier | Products profiled | What they expose | Sources in common with CDF |
|---|---|---|---|
| **Graph virtualization** | PuppyGraph, Stardog, Timbr.ai, RelationalAI, Ontop and Ontotext GraphDB, Altair Graph Studio, Neo4j Virtual Graph and composite databases, Amazon Neptune SERVICE, TigerGraph (loading only), Hasura DDN and Apollo (GraphQL) | Gremlin, openCypher, SPARQL, or GraphQL over tables, files or other graphs | Snowflake, PostgreSQL, ClickHouse (PuppyGraph), Iceberg and Delta; none virtualizes ArangoDB |
| **SQL federation** | Trino, Starburst, Denodo, Dremio, Amazon Athena federated query, BigQuery connections, TIBCO Data Virtualization, IBM Data Virtualization and Db2, Microsoft Fabric shortcuts and Power BI SSO, postgres_fdw, Databricks Lakehouse Federation, Snowflake catalog integrations | ANSI SQL over many catalogs, joined in the engine | Snowflake, PostgreSQL, ClickHouse (Trino, Starburst, Denodo, Dremio) |

CDF differs from both tiers in one structural respect: its hub is a graph
database it also federates (ArangoDB), and ArangoDB has no document-level
security. That makes the fabric the only enforcement point on the graph leg in
every mode, which no competitor has to deal with because none of them
virtualize a graph store.

---

## 3. Outbound authentication: how engines connect to sources

### 3.1 Where the credential lives

| Storage pattern | Products | Notes |
|---|---|---|
| Inline in the catalog or schema definition | PuppyGraph (`catalog[]` in schema JSON, exportable "with Catalog"), Timbr (UI form), Ontop (properties), Stardog (properties file, masked in listings), Altair, TigerGraph (`CREATE DATA_SOURCE` JSON), Hasura (JDBC URL) | Encryption at rest is documented only by Neo4j (AES-GCM-256 for remote-alias credentials) and Hasura ("envelope encryption") |
| Environment or file indirection | Trino `${ENV:VAR}` and `credential-provider.type` FILE or KEYSTORE; Ontop `ONTOP_DB_*_FILE` Docker secrets; TigerGraph `${file:}`, `${env:}`, `${vault:}` | "No secret is stored in the Trino configuration files on the filesystem" |
| External vault reference | Denodo Credentials Vault (AWS Secrets Manager, Azure Key Vault, CyberArk, HashiCorp), Dremio (same three plus `dremio-admin encrypt`), Athena (Secrets Manager mandatory for new connectors), Databricks `secret()` | Denodo caches vault values in memory only and re-fetches on failure |
| Platform-managed connection object | BigQuery connection resource ("Connections are encrypted and stored securely"), Starburst Galaxy catalogs (no staff plaintext access), Databricks connection, Snowflake catalog and storage integrations | The engine never sees a long-lived secret in the keyless variants |
| Volume-mounted key material | PuppyGraph (RSA keys, keytabs, GCP key JSON), Trino and Dremio keytabs | Keytab permissions "similar to SSH private keys" (Trino) |

### 3.2 Which credential types are used, by source

| Source | Credential types the vendors actually document | Vendors on the durable path |
|---|---|---|
| **Snowflake** | Password (Trino OSS, Stardog, Timbr); key-pair (PuppyGraph, Starburst, Dremio, Athena, Neo4j Virtual Graph, TigerGraph); OAuth client credentials (PuppyGraph); OAuth passthrough or impersonation (Starburst, Dremio, Stardog, Denodo, Power BI, Databricks Lakehouse Federation) | All except Trino OSS, Stardog and Timbr, which document only passwords. Snowflake blocks `SERVICE` user passwords by Oct 2026 |
| **Databricks** | PAT (PuppyGraph, Starburst, Dremio); OAuth M2M (PuppyGraph, Databricks federation); Entra passthrough (Starburst, Stardog); credential vending for files (PuppyGraph, Starburst, Dremio) | OAuth M2M users; PATs are now "legacy" |
| **BigQuery** | Service-account key or ADC (PuppyGraph, Stardog, TigerGraph); OAuth2 passthrough (Starburst); web-identity federation (BigQuery Omni for its own outbound) | ADC on attached service accounts or workload identity |
| **PostgreSQL** | Password (everyone); Kerberos (Dremio, Trino via GSS); SQL/MED user mapping (postgres_fdw); AWS IAM database auth via STS (Stardog for Aurora) | Certificates or Kerberos where available; PG18 `oauth` needs a validator module |
| **ClickHouse** | Password (PuppyGraph, Trino); password passthrough (Starburst) | Cloud Enterprise JWT is the only token path |
| **Object storage and Iceberg catalogs** | IAM roles and instance profiles, OAuth client credentials to the catalog, vended short-lived storage credentials (PuppyGraph, Starburst, Dremio, Snowflake) | Vending everywhere it exists |

### 3.3 The privileges granted

The dominant vendors say "read-only" and most of them stop there. The ones
that publish an exact grant list are the useful references:

| Vendor | Source | Documented minimum |
|---|---|---|
| Athena federated | Snowflake | `USAGE` on warehouse and database, `SELECT ON ALL TABLES` |
| Stardog | BigQuery | `bigquery.datasets.get`, `bigquery.jobs.create`, `bigquery.readsessions.create`, `bigquery.tables.get`, `bigquery.tables.getData`, `bigquery.tables.list`, `resourcemanager.projects.get` |
| PuppyGraph | BigQuery | BigQuery Data Viewer, Job User, Read Session User |
| PuppyGraph | AWS S3 and Glue | `s3:GetObject`, `s3:ListBucket`; Glue `Get*` and `BatchGetPartition` |
| PuppyGraph | Databricks | metastore "External data access" on; `EXTERNAL USE SCHEMA` per schema |
| Dremio | S3 | `s3:GetBucketLocation`, `s3:ListAllMyBuckets`, `s3:ListBucket`, `s3:GetObject` |
| Fabric mirroring | Snowflake | `CREATE STREAM`, `SELECT`, `SHOW`, `DESCRIBE` |
| RelationalAI | Snowflake (install) | `EXECUTE TASK`, `EXECUTE MANAGED TASK`, `CREATE WAREHOUSE`, `CREATE COMPUTE POOL`, `BIND SERVICE ENDPOINT`, plus `SELECT` and change tracking on each streamed table |

For Postgres, MySQL, Oracle, ClickHouse, Redshift and SQL Server no vendor in
either tier publishes a privilege list. The platform documents do (see the
data-source reference), and CDF should publish its own per-source list rather
than inherit this gap.

Two vendor privilege requests are wider than read-only and worth noticing:
PuppyGraph's Polaris tutorial grants `TABLE_WRITE_DATA`, and its OneLake
tutorial requires Contributor on the Fabric workspace. RelationalAI's install
privileges are described as non-revocable.

---

## 4. Whose identity reaches the source

### 4.1 The scoreboard

| Product | Default | Delegated path | Family | Sources covered |
|---|---|---|---|---|
| **Denodo** | Service | Yes, per source | Passthrough (password, Kerberos S4U2proxy and protocol transition, OAuth via RFC 8693, Azure on-behalf-of, raw token); mapping via `@{USER_NAME}` vault secrets | Any JDBC source that accepts the resulting credential |
| **Starburst** | Service | Yes, per catalog | Passthrough (password, OAuth2, JWT, Kerberos); impersonation with auth-to-local rules | Snowflake with six impersonation types; a published matrix per connector |
| **Trino OSS** | Service | Yes | Passthrough (client extra-credentials); impersonation (Hadoop proxyuser) | JDBC connectors; Hive and HDFS |
| **Dremio** | Service | Yes, per source | Impersonation (Dremio as Snowflake OAuth issuer; Hive proxyuser) | Snowflake, Hive |
| **TIBCO TDV** | Passthrough when no credential saved | Yes | Passthrough (session credentials, Kerberos SSO) | JDBC sources |
| **Microsoft Fabric** | Passthrough same-tenant, delegated otherwise | Built in | Passthrough (OneLake); delegated fixed identity (external) | OneLake, S3, GCS, ADLS, Snowflake |
| **Power BI DirectQuery** | Service | Yes, tenant setting | Passthrough (Entra token to Snowflake External OAuth) | Snowflake and others |
| **postgres_fdw and Db2** | Mapping | Built in | SQL/MED user mapping; GSSAPI delegation | Postgres, Db2 |
| **Stardog** | Service | Yes, OAuth-only, opt-in | Passthrough (Entra on-behalf-of, Okta RFC 8693) | Databricks, Snowflake, Aurora, REST |
| **PuppyGraph** | Service | Snowflake only | Asserted identity (session variable read by row access policies) | Snowflake |
| **Neo4j composite DBs** | Service (stored alias credentials) | Yes, 2026.01 | Passthrough (OIDC credential forwarding) | Neo4j to Neo4j only |
| **Amazon Neptune SERVICE** | Passthrough | Built in | IAM identity forwarded | Neptune to Neptune only |
| **Athena federated** | Service (Lambda role) | No | Lake Formation governs instead | n/a |
| **BigQuery connections** | Service (connection SA) | No | n/a | n/a |
| **Databricks Lakehouse Federation** | Service (connection) | No | n/a | n/a |
| **Timbr, RelationalAI, Ontop, GraphDB, Altair, Neo4j Virtual Graph, TigerGraph, Hasura, Apollo** | Service | No | n/a | n/a |

### 4.2 How each family is implemented

**Passthrough.** The engine authenticates the user at its edge (OIDC, SAML,
LDAP, Kerberos) and forwards either the same credential or a token derived from
it. The derivation is where the engineering lives. Denodo lists three OAuth
strategies and recommends RFC 8693 token exchange as "more secure" than raw
token passthrough because the source token can carry a narrower audience and
scope. Stardog's Okta path is RFC 8693; its Entra path is Microsoft's
on-behalf-of flow. Starburst's OAuth2 passthrough forwards the inbound token
unchanged, which is why it cannot refresh it and requires "Each access token's
remaining lifetime must be longer than the query's execution time".
Preconditions on the source side are always the same: an external OAuth
security integration (Snowflake), a token federation policy (Databricks), or a
shared authentication backend (Starburst password passthrough: "the data
source and SEP must use the same authentication backend").

**Impersonation.** The engine authenticates as itself with a credential the
source trusts to act for others. Hadoop's proxyuser configuration is the oldest
form. Dremio's Snowflake design is the most elegant: Snowflake is told that
Dremio is an OAuth issuer with a known RSA public key, and Dremio mints
per-user tokens with the user in `sub`. Starburst's auth-to-local rules map the
engine user to a source user or role. Denodo's Kerberos constrained delegation
with protocol transition lets a user who arrived by OAuth be represented to a
Kerberos-only source. Postgres `SET ROLE` is impersonation at the SQL level:
the connecting role must be a member of each user role, and RLS then keys off
`current_user`.

**Mapping.** A table from local principal to remote credential. SQL/MED
standardises it (`CREATE USER MAPPING FOR user SERVER s OPTIONS (user,
password)`), Db2 requires the remote password, postgres_fdw allows GSSAPI
delegation instead. Denodo's vault pattern makes the vault the mapping table.
The cost is one remote credential per user to provision and rotate.

**Asserted identity.** PuppyGraph's Snowflake path is the only instance found.
The service-account connection calls a customer-installed procedure that runs
`SET PG_PROXY_USER = ?`; Snowflake row access policies read
`GETVARIABLE('PG_PROXY_USER')`. The source never authenticates the user, the
audit trail shows the service account, and the policy's correctness depends on
the engine never lying. It is the trusted-subsystem model with a per-user hint,
and it works only where a source lets policies read session state.

### 4.3 What breaks under delegation

The vendors document three casualties, all relevant to CDF's design:

- **Connection pooling.** Denodo keeps one pool per user and offers Oracle
  proxy authentication as a lighter alternative. Ontop's shared JDBC pool has
  no per-user hook today, which is why CDF's per-user Postgres leg is delivered
  by an upstream Ontop change (`SET ROLE` in the statement initializer, then an
  identity-keyed pool) rather than by routing around Ontop; see PRD §10.13.
- **Caching.** Denodo's cache "does not check which user populated it"; the
  documented mitigation is to populate it with an unrestricted scheduled user
  or not cache. Dremio disables reflections on impersonated sources. CDF's M12
  plan to key cached leg results on `(canonical sub-query, as-of, entitlement
  scope)` is the right answer and should be written into ADR-0004's
  consequences.
- **Token lifetime.** Starburst cannot refresh a passed-through token. CDF's
  per-leg deadline (`CDF_RUNTIME_WALL_TIME_MS`) must be bounded above by the
  delegated token's remaining validity, and the refusal must name the reason.

---

## 5. Inbound authentication and engine-side authorization

### 5.1 Inbound protocols

| Protocol | Products |
|---|---|
| OIDC | PuppyGraph (code flow with PKCE; Enterprise only), Stardog, Timbr, GraphDB, Altair (Keycloak), Neo4j, TigerGraph, Trino OAuth2, Dremio, Denodo, Hasura (JWT), Apollo (JWT via JWKS) |
| SAML | Denodo, TigerGraph; Stardog Cloud and PuppyGraph mention it without documenting it |
| LDAP | Stardog, GraphDB, Neo4j, TigerGraph, Trino, Dremio, Denodo |
| Kerberos | Stardog (discouraged in clusters), GraphDB, Neo4j, TigerGraph, Trino, Denodo, TIBCO |
| Cloud IAM | Neptune (SigV4), Athena, BigQuery |
| Local users with default admin | PuppyGraph (`puppygraph/puppygraph123`), Stardog (`admin/admin`), GraphDB, Ontop (none) |

CDF accepts OIDC JWT bearer tokens at the edge and discards them after
validation (ADR-0004). That is the modal choice and needs no change.

### 5.2 Authorization models inside the engine

| Model | Products | Granularity |
|---|---|---|
| Fixed roles | PuppyGraph (five roles, no custom roles) | Operation level |
| RBAC over resources | Stardog (db, named-graph, virtual-graph, data-source), GraphDB, Neo4j, TigerGraph (global, graph, type, attribute), Dremio, Denodo, Starburst built-in, Galaxy | Object level |
| Engine-side row filtering | PuppyGraph entitlement table, Timbr policies, Hasura session-variable filters, Trino file rules and OPA, Ranger (Trino, Starburst, Dremio), Denodo row restrictions, TIBCO SQL filter policies, Dremio UDF policies | Row |
| Engine-side column masking | Trino and Starburst (file, OPA, Ranger), Denodo, Dremio, IBM (results only) | Column |
| Property-level graph rules | Neo4j property-based access control (single-property predicates), GraphDB triple-pattern ACLs | Property or triple |
| Tag- or attribute-driven "define once" | Denodo Global Security Policies (Enterprise Plus), Galaxy ABAC, Immuta, Ranger tag policies | Cross-object |
| Field-level API directives | Apollo `@authenticated`, `@requiresScopes`, `@policy` | Field, no rows |

Two observations for CDF. First, no graph-virtualization product offers
column or property masking except Neo4j and GraphDB on their own stores;
PuppyGraph, Timbr and Stardog do not. The access-control research already
flagged CDF's missing column-masking guard as a gap; the market has the same
gap, so closing it is a differentiator rather than table stakes. Second, the
tag-driven layer that Denodo sells as its premium tier is what Module 08 OBAC
provides by seating policy at the ontology, where every concept and property
already carries structure.

### 5.3 Fail-open and fail-closed defaults

| Product or platform | Behaviour when no policy matches |
|---|---|
| PuppyGraph row-level security | Users with no entitlement rows bypass filtering (fail-open) |
| ClickHouse row policies | Users without a matching policy read all rows by default (fail-open) |
| Neo4j property rules | Documented `DENY` fail-open caveat |
| Trino file-based rules | Default deny (fail-closed) |
| Postgres RLS | Enabled table with no policy returns no rows (fail-closed) |
| Lake Formation vending | Fail-close when the engine cannot apply a filter |
| CDF ADR-0004 delegated mode | Fails closed if no broker exists; "must never fall back to service credentials" |

---

## 6. Schema discovery and the identity it runs under

| Product | Introspection mechanism | Identity used | Source privileges stated |
|---|---|---|---|
| PuppyGraph | Catalog tree via JDBC or metastore; mapping manual; optional LLM assistant that samples rows | Catalog service credential | Only for BigQuery, AWS, Databricks vending |
| Stardog | Catalogs, schemas, tables, columns, constraints and row-count estimates cached at data-source add | Service credential, even with pass-through | Only for BigQuery |
| Denodo | "Create base view" wizard | Data-source credential, explicitly, even with pass-through or vault | Not stated |
| Dremio | Metadata refresh (1 hour discovery, 3 hours expiry defaults) | Source credential | Not stated |
| Trino | `ConnectorMetadata`; base JDBC via `DatabaseMetaData` | Session identity in the base JDBC path | Not stated |
| Ontop and GraphDB | JDBC metadata; offline `extract-db-metadata` JSON | Service credential | Not stated |
| Timbr, Altair, Hasura | Schema browser or connector introspect | Service credential | Not stated |
| RelationalAI | CDC streams of tables the app can `SELECT` | The `RELATIONALAI` role | `SELECT` plus change tracking |

The field's practice supports ADR-0004's separation of steward and asker
planes: introspection is a build-time act under an operator-owned identity.
Two hygiene points come out of it. Schema metadata itself can leak (row-count
estimates, column names of sensitive tables), so the steward credential's
`information_schema` visibility should be scoped to the mapped schemas, which
Snowflake, Databricks and BigQuery all do by privilege-filtering their
information schemas. And PuppyGraph's AI Assistant shows that "schema
discovery" can quietly become "row sampling to an LLM provider"; CDF's r2g and
ontology-extraction path is schema-first and should say so.

---

## 7. Operational practice

What the documentation says operators actually do, grouped by concern.

**Credential provisioning.** A dedicated read-only source principal per
engine is universal. The durable credential types are key-pair for Snowflake
(PuppyGraph, Starburst, Dremio, Athena, Neo4j), OAuth M2M for Databricks
(PuppyGraph, Databricks federation), attached service accounts or workload
identity for Google Cloud (PuppyGraph, BigQuery Omni), and instance profiles
or IRSA on AWS (PuppyGraph: "The most secure and recommended approach for
EC2-based deployments"). Snowflake's rollout removes the password option for
`SERVICE` users by October 2026 and Databricks labels PATs legacy, so any
onboarding runbook written today should not offer passwords or PATs.

**Secret storage and rotation.** The vendors that take rotation seriously push
it to a vault and let the vault own the schedule: Denodo ("the security team
can now configure the vault to rotate the password"), Dremio, Athena
("highly recommend" rotation). Snowflake supports zero-downtime key rotation
with `RSA_PUBLIC_KEY_2` and named key pairs with a 24-hour grace; Databricks
allows five overlapping M2M secrets per principal. PuppyGraph, Timbr, Stardog
and Ontop document no rotation story beyond re-entering the value.

**Audit.** Cloud products log by default (Galaxy account actions, Databricks
`system.access.audit` for 365 days, Neptune caller ARN and query payload).
Self-hosted graph products default audit off (Stardog
`logging.audit.enabled`) or do not document where the log goes (PuppyGraph).
Under service mode the source's audit trail shows only the engine, so the
engine's own audit is the sole record of who asked; ADR-0004's envelope and
telemetry carrying the principal is what makes CDF auditable in that mode.

**Network.** Snowflake network policies at user or integration level (and
PATs require one by default), ClickHouse `HOST` restrictions per user, Athena
Lambda VPC placement with a Secrets Manager endpoint, Galaxy PrivateLink and
fixed egress IPs, PuppyGraph TLS only by an nginx front. The common shape is
a source-side allowlist naming the engine's egress.

**Guardrails per identity.** The platforms allow it where the vendors do not
mention it: Snowflake `STATEMENT_TIMEOUT_IN_SECONDS` on the user, Postgres
`ALTER ROLE … SET statement_timeout` and `CONNECTION LIMIT`, ClickHouse
settings profiles with constraints and quotas keyed by user, BigQuery
`maximumBytesBilled` and per-user daily quotas. CDF's PRD §10.6 and §10.7
already require these on the CC-7 role; the data-source reference now carries
the exact statements.

**Editions and gating.** Delegation and SSO are premium features across the
field: PuppyGraph SSO is Enterprise-only, GraphDB LDAP and OpenID need a
Standard or Enterprise license, Denodo's tag policies are Enterprise Plus,
Starburst built-in access control needs a license, Dremio SSO is reported
Enterprise-only `[secondary]`. Customers evaluating CDF will assume the same;
the product-strategy PRD should decide deliberately whether delegated mode is
a tier or a default.

---

## 8. Where the Gemini starter note was wrong

The note was directionally useful and specifically unreliable. Corrections
that matter for positioning:

| Claim | Finding |
|---|---|
| Timbr uses "user impersonation & token passthrough" | Not documented anywhere; one service credential and engine-side policies |
| Timbr has a Snowflake Native App variant | Not documented |
| RelationalAI "inherits host warehouse security … natively" | Runs as the single `RELATIONALAI` role; per-user row and masking policies explicitly unsupported |
| PuppyGraph inbound is "OIDC, OAuth2, JWT" with RBAC and RLS "filtering specific node properties" | OIDC only, Enterprise only; five fixed roles; row filtering only, no property masking; Snowflake asserted-identity path omitted entirely |
| PuppyGraph Snowflake privileges are `USAGE` plus `SELECT` | Asserted by the note, not by PuppyGraph's docs |
| PuppyGraph supports "Trino / Presto" | Trino yes, Presto not documented |
| Stardog maps with "R2RML" | Native format is SMS2; R2RML is a supported subset |
| Neo4j Fabric federates source systems | Composite databases federate Neo4j only; warehouse virtualization is a separate private preview on a service account |
| Federation vendors use "OAuth 2.0 Token Exchange, Kerberos Constrained Delegation, or Password Pass-Through" | Correct, and Denodo, Starburst and Dremio are the products that actually document all three |

---

## 9. Implications and recommendations for CDF

Mapped to the existing decisions in ADR-0004, the access-control research,
and the PRD.

### 9.1 Confirmations

- **Service mode as the shipped default is the market norm.** Every product
  starts there. CDF should stop describing it as a demo limitation and
  describe it as the baseline tier, with the exact per-source read-only role
  published (the data-source reference has the statements).
- **RFC 8693 as the broker contract is correct.** Databricks token federation,
  Denodo's recommended OAuth strategy, Stardog's Okta path and the Iceberg
  REST spec all speak it. Snowflake External OAuth consumes the exchanged
  token. No vendor has chosen a different standard for new work.
- **Steward and asker planes match the field.** Introspection under the
  operator identity is what Denodo and Stardog do explicitly. Keep it.
- **Fail-closed delegated mode is a differentiator.** PuppyGraph's RLS and
  ClickHouse's row policies fail open by default. State CDF's behaviour in the
  customer Q&A.

### 9.2 Recommendations

1. **Build the first `DelegationBroker` for Snowflake External OAuth.** It is
   the delegation door every SQL federator uses, the Snowflake sprint already
   proved the native leg, and the customer pull (PRD §2.2, the data-platform
   lead's cost and governance concerns) is on Snowflake. Preconditions to
   document for operators: a security integration trusting the IdP, the user
   mapping claim, `session:role:*` scopes, and row or masking policies written
   against `CURRENT_USER()` or `IS_ROLE_IN_SESSION()`.

2. **Evaluate the asserted-identity pattern as an interim for Snowflake.**
   A `SET` of the authenticated principal into a session variable before each
   Snowflake leg lets customer row access policies fire per user with no IdP
   federation project. It must be labelled in the envelope as asserted, not
   authenticated, and refused when the principal is anonymous. This is a
   small executor change and a one-paragraph ADR-0004 amendment.

3. **Keep Ontop and change it, rather than route around it.** Ontop already
   carries HTTP headers into its `QueryContext` and exposes `ontop_user()` to
   mappings and lenses (Ontop PR #753); its maintainer wants impersonation built
   on that object (Ontop discussion #884). The cheap step is a `SET ROLE` per
   statement in Ontop's `JDBCStatementInitializer` for the `asserted` level; the
   full step is an identity-keyed connection pool for `delegated`, designed with
   Ontopic. A native Postgres executor is a fallback only, because it forfeits
   the aggregation pushdown ADR-0005 grants solely to Ontop and Arango. Decided
   2026-09-10; PRD §10.13 and ADR-0001's amendment hold the option set.

4. **Add a token-lifetime bound to per-leg deadlines.** Starburst's
   documented constraint applies to any passthrough design. A delegated leg
   whose token expires before `CDF_RUNTIME_WALL_TIME_MS` should be refused at
   admission with a named reason, not fail mid-flight.

5. **Key the assembly and leg caches on entitlement scope** and write that
   into ADR-0004's consequences. Denodo and Dremio both document the failure
   mode; M12's cache key already includes it.

6. **Publish the per-source read-only grant lists and the network allowlist
   shape** in the deployment docs, since no graph competitor does. Include the
   Snowflake October 2026 password cutoff so onboarding never proposes a
   password credential.

7. **Close the column-masking gap** the access-control research identified.
   No graph-virtualization competitor has it; the SQL federators do. Under
   service mode the fabric is the only place it can live, and the catalog
   already carries per-property structure for PII tags.

8. **Position against the field in one sentence each.** Against PuppyGraph:
   authenticated delegation with fail-closed refusal versus asserted identity
   with fail-open RLS, and schema-first mapping that never ships row samples to
   an LLM. Against Stardog: parity on delegation, plus an ontology-seated
   policy layer instead of named-graph grants. Against Denodo and Starburst:
   they federate tables with mature delegation; CDF federates concepts, cites
   or refuses, and inherits their delegation patterns rather than competing
   on them.

### 9.3 Open questions for the team

- Is delegated mode a product tier or the default? The field gates it; the
  product-strategy PRD should choose.
- Which prospects run Snowflake External OAuth today? That decides whether
  recommendation 1 or 2 ships first.
- Does the M8 OBAC specification need a "policy replication" section
  acknowledging that ontology-seated rules and source-native rules must be
  reconciled, as Denodo and Immuta acknowledge?

---

## Sources

This synthesis cites the four companion documents, each of which carries a
full URL list:

- docs/research/puppygraph-security-and-schema-profile.md
- docs/research/graph-virtualization-vendor-security-profiles.md
- docs/research/sql-federation-vendor-security-profiles.md
- docs/research/data-source-identity-mechanisms.md

Quotations reproduced above come from: Trino secrets and HDFS pages, Starburst
password and OAuth2 passthrough pages, Denodo JDBC data-source and
pass-through considerations pages and Credentials Vault page, Dremio Snowflake
source page, Athena Snowflake connector pages, Stardog pass-through
authentication page, PuppyGraph Snowflake SSO impersonation tutorial and AWS
authentication reference, RelationalAI data-management page, Fabric Snowflake
mirroring page, and the Snowflake MFA rollout page. Exact URLs are in the
companion documents' source lists.
