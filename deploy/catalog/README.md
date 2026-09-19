# Catalog manifest v1

`manifest.json` is generated from the existing deploy CSI/R2RML artifacts:

```sh
cdf-catalog build --output deploy/catalog/manifest.json
cdf-catalog validate deploy/catalog/manifest.json
```

Use `CDF_CATALOG_MANIFEST=deploy/catalog/manifest.json` to make it authoritative
at runtime. Artifact paths are repository-root-relative and their bytes are
SHA-256 pinned.

An optional build overlay has this shape:

```json
{
  "sources": {
    "postgresql:crm": {
      "joinKeys": ["accountId"],
      "uniqueConstraints": {"Account": [["accountId"]]},
      "entitlements": {
        "classification": "confidential",
        "allowedRoles": ["csm"],
        "allowedGroups": ["customer-success"],
        "allowedScopes": ["fabric.read"],
        "allowedPurposes": ["customer support"],
        "mask": "none",
        "rowConstraints": {"accountId": "tenant"},
        "discloseSource": true,
        "policyIds": ["policy:crm-account"],
        "allowFabricRowPushdown": true,
        "allowFabricMasking": false,
        "properties": {
          "email": {
            "classification": "restricted",
            "mask": "hmac",
            "allowFabricMasking": true,
            "policyIds": ["policy:crm-email"]
          }
        }
      },
      "runtimeResolution": {
        "mode": "canonical_hub",
        "joinVariable": "account_key",
        "canonicalKeyRegex": "^canonical/[a-z0-9_-]+$",
        "canonicalKeyPrefix": "canonical/",
        "scopeBindingVariable": "account_scope",
        "observableBindings": {
          "name": "account_name",
          "email_domain": "email_domain"
        },
        "policyProfile": "fabric_canonical_hub",
        "resolver": "aer"
      },
      "auth": {"mode": "delegated", "delegation": "on-behalf-of"}
    }
  }
}
```

`uniqueConstraints` (ADR-0005 D1 v2; accepted by the loader from rung 3 slice 1
onwards) declares, per concept the source serves, the property sets that are
unique there — the one-side of a cross-source join. Declare it only where it is
true: in the demo, `accountId` is unique on `Account` in the CRM source and on
nothing else. The builder writes `{}` when a source declares none; `null` is
invalid. `cdf-catalog probe` verifies every declared key set against the live
source and fails onboarding on a contradiction (CC-14).

Concept/property entries inherit omitted fields from the source rule. Supported
masks are `none`, `redact`, keyed `hmac`, and `drop`; unsalted hashing is not
supported. Row constraints map a bare SPARQL binding to `tenant` or an
allowlisted `claim:<name>`. Unknown fields, unsafe variables, secret-like
names/claims, and overrides absent from CSI are rejected. Generated defaults
remain unrestricted with `mask: none`; production should select
`CDF_POLICY_BACKEND=catalog|openfga` and set `CDF_POLICY_REQUIRED=true`.

The OpenFGA backend additionally requires `CDF_OPENFGA_API_URL`,
`CDF_OPENFGA_STORE_ID`, `CDF_OPENFGA_MODEL_ID`, and
`CDF_OPENFGA_RELATIONSHIP`; `CDF_OPENFGA_TIMEOUT_SECONDS` is bounded to ten
seconds. The API URL must use HTTPS outside explicit loopback development;
bearer material always requires HTTPS, including on loopback. The client denies
redirects, bounds response bytes, and validates an object with a boolean
`allowed` field. An optional bearer is supplied only through
`CDF_OPENFGA_BEARER_TOKEN` and is never serialized. HMAC masking requires
`CDF_MASKING_KEY` (minimum 16 bytes) or
`CDF_MASKING_KEY_RESOLVER_FACTORY=package.module:function`.

Binding names are bare SPARQL result variables, not source column names. Runtime
resolution is disabled unless a source explicitly uses `canonical_hub` and the
service has a CDF-owned guarded resolver injected. Observable fields and binding
variables must be unique; identifier/oracle fields are forbidden. The checked-in
demo manifest deliberately leaves every source at `mode: none`.

r2g remains the default relational producer. The optional
`cdf.catalog.adapters.rsa.rsa_bundle_to_csi` adapter normalizes an RSA bundle
but deliberately does not create R2RML; relational manifest validation still
requires a real mapping.
