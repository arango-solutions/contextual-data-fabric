---
title: "ADR-0003 — File-backed authoritative catalog manifest v1"
adr: 0003
module: 05-federated-query-engine
status: accepted
date: 2026-08-05
deciders: ["Arthur Keen"]
related:
  - "[[ADR-0001-conceptual-query-language|ADR-0001]]"
  - "[[contextual-data-fabric-prd|PRD]] §5.2, §10.2"
---

# ADR-0003 — Where is the authoritative federation catalog?

**Status:** Accepted for M11 / P3 WP-16.

## Context

The runtime previously discovered every JSON file under `CDF_CSI_DIR` and
guessed each relational R2RML filename. That is convenient for a demo but is
not an authoritative catalog: there is no atomic generation, artifact
integrity check, declared concept ownership, or safe place for governance and
runtime-resolution metadata.

The product architecture describes ArangoDB as the hub and eventually needs
temporal catalog history there. That backend does not exist in this repository
today. No production hub collection, temporal schema, migration, transaction
policy, retention policy, or operational infrastructure has been provisioned
or tested. Claiming the hub is authoritative now would therefore be false.

## Decision

Catalog manifest version 1 is a checked-in, file-backed JSON manifest loaded
through the small `CatalogLoader` protocol. `FileCatalogLoader` is the only
implemented backend and is authoritative whenever `CDF_CATALOG_MANIFEST` is
set. The legacy `CDF_CSI_DIR` / `CDF_R2RML_DIR` discovery path remains
supported when it is not set.

The manifest:

- has an opaque generation and canonical content hash;
- declares exactly one owner for each conceptual class;
- identifies every source by the CSI provenance-derived `kind:ref`;
- pins CSI and R2RML with repository-relative paths, SHA-256, artifact
  generation, producer, and direction;
- snapshots CSI statistics and declares join keys, per-concept unique
  constraints (`uniqueConstraints`, keyed by concept, key sets of property
  names; always written, `{}` when none — ADR-0005 D1 v2, rung 3), source
  defaults plus per-concept/per-property entitlements, runtime-resolution
  metadata, and auth/delegation mode. Entitlements cover role/group/scope/purpose,
  classification, `none|redact|hmac|drop`, safe principal-bound row
  constraints, source disclosure, policy IDs, and explicit trusted-fabric PEP
  permissions;
- contains metadata only: credentials, tokens, DSNs, and secret-like fields
  are rejected;
- requires an actual R2RML artifact for every relational source. An adapter
  must never synthesize one merely to satisfy catalog validation.

Artifact references are relative to the catalog root (the repository root for
the checked-in manifest), not to the manifest file. Absolute paths and `.` or
`..` path segments are invalid. This lets `deploy/catalog/manifest.json`
reference existing deploy artifacts without copying or rewriting them.

Build output is deterministic. Validation re-hashes every artifact, checks CSI
source/provenance/concepts/statistics against the declaration, enforces
single-owner semantics, and proves a `SourceCatalog` can be built.
Entitlement overrides are cross-checked against CSI concepts/properties;
unknown fields, secret-like names/claims, and unsafe binding variables fail
validation. Generated manifests remain allow-by-default/no-mask for backward
compatibility until an operator supplies a governance overlay.

## Deferred backend

A hub-resident temporal graph catalog may implement the same `CatalogLoader`
protocol later. It must return the same immutable validated model and preserve
the same content-hash and ownership semantics. Before adoption it requires a
separate ADR covering temporal graph shape, atomic publication, rollback,
authorization, retention, migration from file generations, and an integration
test against real external infrastructure.

The future backend is not emulated by writing catalog documents to an arbitrary
ArangoDB collection, and catalog validation remains fully offline.

## Consequences

- `CDF_CATALOG_MANIFEST` removes runtime filename guessing and makes the
  manifest authoritative for both CSI and R2RML paths.
- Catalog publication is currently a repository/deployment artifact operation,
  not an online control-plane service.
- SHA-256 detects accidental or unauthorized artifact drift, but this ADR does
  not claim signatures, remote attestation, or a secret-distribution system.
- r2g remains the default relational producer because it emits coherent CSI +
  R2RML. Direct RSA bundle onboarding is optional and still requires a real
  separately produced R2RML mapping.
