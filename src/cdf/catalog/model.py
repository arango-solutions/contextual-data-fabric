"""Strict immutable catalog-manifest v1 model and file-backed loader.

The file loader is the authoritative implementation for v1.  Its small
``CatalogLoader`` protocol deliberately leaves storage replaceable without
changing consumers (ADR-0003).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Protocol

from cdf.query.catalog import SourceCatalog, parse_csi_statistics

from .capabilities import SourceCapabilities, parse_capabilities

MANIFEST_VERSION = "1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_RE = re.compile(
    r"(?:passwords?|passwds?|secrets?|tokens?|api[_-]?keys?|access[_-]?keys?"
    r"|client[_-]?secrets?|private[_-]?keys?|credentials?"
    r"|connection[_-]?strings?|dsn)$",
    re.IGNORECASE,
)
_SOURCE_KINDS_WITHOUT_R2RML = frozenset({"arango"})
_ARTIFACT_DIRECTIONS = frozenset({"forward", "reverse"})
_MASKS = frozenset({"none", "redact", "hmac", "drop"})
_CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "restricted"})
_AUTH_MODES = frozenset({"none", "service", "delegated"})
_DELEGATIONS = frozenset({"none", "user", "on-behalf-of"})
_RESOLUTION_MODES = frozenset({"none", "canonical_hub"})
_BINDING_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ORACLE_FIELD_NAMES = frozenset(
    {
        "canonical_id",
        "expected_canonical_id",
        "gold_id",
        "ground_truth_id",
        "match_id",
        "oracle_id",
        "resolved_to",
    }
)


def canonical_json(value: Any) -> bytes:
    """Return the stable UTF-8 JSON representation used by all v1 hashes."""
    return json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def canonical_content_hash(document: Mapping[str, Any]) -> str:
    """Hash a manifest, excluding only its self-referential ``contentHash``."""
    content = dict(document)
    content.pop("contentHash", None)
    return hashlib.sha256(canonical_json(content)).hexdigest()


def canonical_generation(document: Mapping[str, Any]) -> str:
    """Derive a deterministic opaque generation from generation-free content."""
    content = dict(document)
    content.pop("contentHash", None)
    content.pop("generation", None)
    return f"sha256:{hashlib.sha256(canonical_json(content)).hexdigest()}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    sha256: str
    generation: str
    producer: str
    direction: str


@dataclass(frozen=True)
class RowConstraintSpec:
    binding_variable: str
    principal_attribute: str


@dataclass(frozen=True)
class EntitlementRule:
    classification: str
    allowed_roles: tuple[str, ...]
    allowed_groups: tuple[str, ...]
    allowed_scopes: tuple[str, ...]
    allowed_purposes: tuple[str, ...]
    mask: str
    row_constraints: tuple[RowConstraintSpec, ...]
    disclose_source: bool
    policy_ids: tuple[str, ...]
    allow_fabric_masking: bool
    allow_fabric_row_pushdown: bool


@dataclass(frozen=True)
class Entitlements(EntitlementRule):
    concepts: Mapping[str, EntitlementRule]
    properties: Mapping[str, EntitlementRule]

    def rule_for(self, resource_type: str, name: str) -> EntitlementRule:
        if resource_type == "concept":
            return self.concepts.get(name, self)
        if resource_type == "property":
            return self.properties.get(name, self)
        return self


@dataclass(frozen=True)
class RuntimeResolution:
    mode: str
    join_variable: str | None
    canonical_key_regex: str | None
    canonical_key_prefix: str | None
    scope_binding_variable: str | None
    observable_bindings: Mapping[str, str]
    policy_profile: str | None
    resolver: str | None


@dataclass(frozen=True)
class AuthMetadata:
    mode: str
    delegation: str


@dataclass(frozen=True)
class CatalogSource:
    source_id: str
    kind: str
    ref: str
    concepts: tuple[str, ...]
    csi: ArtifactRef
    r2rml: ArtifactRef | None
    statistics_snapshot: Mapping[str, Any] | None
    join_keys: tuple[str, ...]
    entitlements: Entitlements
    runtime_resolution: RuntimeResolution
    auth: AuthMetadata
    capabilities: SourceCapabilities | None = None
    """ADR-0005 D4 declarations; ``None`` = legacy manifest (kind defaults
    apply). CC-14: onboarding probes strip declarations reality contradicts."""


@dataclass(frozen=True)
class CatalogManifest:
    catalog_manifest_version: str
    generation: str
    content_hash: str
    concept_base: str
    sources: tuple[CatalogSource, ...]

    def source(self, source_id: str) -> CatalogSource | None:
        return next((item for item in self.sources if item.source_id == source_id), None)


@dataclass(frozen=True)
class LoadedCatalog:
    manifest: CatalogManifest
    root: Path
    csi_documents: tuple[Mapping[str, Any], ...]
    csi_paths: Mapping[str, Path]
    r2rml_paths: Mapping[str, Path]

    def source_catalog(self) -> SourceCatalog:
        documents = [dict(item) for item in self.csi_documents]
        source_ids = [item.source_id for item in self.manifest.sources]
        catalog = SourceCatalog.from_csi_documents(
            documents,
            source_ids=source_ids,
            concept_base=self.manifest.concept_base,
        )
        catalog.apply_manifest(self.manifest)
        return catalog


class CatalogLoader(Protocol):
    """Backend-neutral loader protocol retained for the deferred hub backend."""

    def load(self) -> LoadedCatalog: ...


@dataclass(frozen=True)
class FileCatalogLoader:
    path: Path
    root: Path | None = None

    def load(self) -> LoadedCatalog:
        return load_manifest(self.path, root=self.root)


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _strict(raw: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unexpected = set(raw) - allowed
    if unexpected:
        raise ValueError(f"{path} has unknown fields: {', '.join(sorted(unexpected))}")


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _strings(value: Any, path: str, *, unique: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    result = tuple(_string(item, f"{path}[{index}]") for index, item in enumerate(value))
    if unique and len(set(result)) != len(result):
        raise ValueError(f"{path} contains duplicates")
    return result


def _reject_secrets(value: Any, path: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                raise ValueError(f"{path}.{key} is credential/secret-like and is forbidden")
            _reject_secrets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{path}[{index}]")


def _artifact(value: Any, path: str) -> ArtifactRef:
    raw = _object(value, path)
    _strict(raw, {"path", "sha256", "generation", "producer", "direction"}, path)
    artifact_path = _string(raw.get("path"), f"{path}.path")
    pure = PurePosixPath(artifact_path)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise ValueError(f"{path}.path must be a clean repository-relative path")
    digest = _string(raw.get("sha256"), f"{path}.sha256")
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{path}.sha256 must be a lowercase SHA-256 digest")
    direction = _string(raw.get("direction"), f"{path}.direction")
    if direction not in _ARTIFACT_DIRECTIONS:
        raise ValueError(f"{path}.direction must be forward or reverse")
    return ArtifactRef(
        path=artifact_path,
        sha256=digest,
        generation=_string(raw.get("generation"), f"{path}.generation"),
        producer=_string(raw.get("producer"), f"{path}.producer"),
        direction=direction,
    )


def _boolean_value(value: Any, path: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _principal_attribute(value: Any, path: str) -> str:
    attribute = _string(value, path)
    if attribute == "tenant":
        return attribute
    prefix = "claim:"
    if not attribute.startswith(prefix):
        raise ValueError(f"{path} must be tenant or claim:<safe-claim>")
    claim = attribute[len(prefix) :]
    if not _BINDING_VARIABLE_RE.fullmatch(claim):
        raise ValueError(f"{path} has an unsafe claim name")
    folded = claim.casefold()
    if _SECRET_KEY_RE.search(claim) or any(
        item in folded for item in ("password", "token", "secret", "credential", "authorization")
    ):
        raise ValueError(f"{path} references a secret-like claim")
    return attribute


def _row_constraints(value: Any, path: str) -> tuple[RowConstraintSpec, ...]:
    if value is None:
        return ()
    raw = _object(value, path)
    constraints: list[RowConstraintSpec] = []
    for variable_value, attribute_value in sorted(raw.items()):
        variable = _binding_variable(variable_value, f"{path}.{variable_value}")
        if _SECRET_KEY_RE.search(variable):
            raise ValueError(f"{path}.{variable} is a secret-like binding variable")
        constraints.append(
            RowConstraintSpec(
                binding_variable=variable,
                principal_attribute=_principal_attribute(
                    attribute_value, f"{path}.{variable}"
                ),
            )
        )
    return tuple(constraints)


_ENTITLEMENT_RULE_FIELDS = {
    "classification",
    "allowedRoles",
    "allowedGroups",
    "allowedScopes",
    "allowedPurposes",
    "mask",
    "rowConstraints",
    "discloseSource",
    "policyIds",
    "allowFabricMasking",
    "allowFabricRowPushdown",
}


def _entitlement_rule(
    value: Any,
    path: str,
    *,
    inherited: EntitlementRule | None = None,
) -> EntitlementRule:
    raw = _object(value, path)
    _strict(raw, _ENTITLEMENT_RULE_FIELDS, path)
    classification = _string(
        raw.get(
            "classification",
            inherited.classification if inherited is not None else None,
        ),
        f"{path}.classification",
    )
    if classification not in _CLASSIFICATIONS:
        raise ValueError(f"{path}.classification is invalid")
    mask = _string(
        raw.get("mask", inherited.mask if inherited is not None else None),
        f"{path}.mask",
    )
    if mask not in _MASKS:
        raise ValueError(f"{path}.mask is invalid")
    return EntitlementRule(
        classification=classification,
        allowed_roles=_strings(
            raw.get(
                "allowedRoles",
                list(inherited.allowed_roles) if inherited is not None else [],
            ),
            f"{path}.allowedRoles",
        ),
        allowed_groups=_strings(
            raw.get(
                "allowedGroups",
                list(inherited.allowed_groups) if inherited is not None else [],
            ),
            f"{path}.allowedGroups",
        ),
        allowed_scopes=_strings(
            raw.get(
                "allowedScopes",
                list(inherited.allowed_scopes) if inherited is not None else [],
            ),
            f"{path}.allowedScopes",
        ),
        allowed_purposes=_strings(
            raw.get(
                "allowedPurposes",
                list(inherited.allowed_purposes) if inherited is not None else [],
            ),
            f"{path}.allowedPurposes",
        ),
        mask=mask,
        row_constraints=(
            _row_constraints(raw.get("rowConstraints"), f"{path}.rowConstraints")
            if "rowConstraints" in raw
            else (inherited.row_constraints if inherited is not None else ())
        ),
        disclose_source=_boolean_value(
            raw.get("discloseSource"),
            f"{path}.discloseSource",
            default=inherited.disclose_source if inherited is not None else True,
        ),
        policy_ids=_strings(
            raw.get(
                "policyIds",
                list(inherited.policy_ids) if inherited is not None else [],
            ),
            f"{path}.policyIds",
        ),
        allow_fabric_masking=_boolean_value(
            raw.get("allowFabricMasking"),
            f"{path}.allowFabricMasking",
            default=inherited.allow_fabric_masking if inherited is not None else False,
        ),
        allow_fabric_row_pushdown=_boolean_value(
            raw.get("allowFabricRowPushdown"),
            f"{path}.allowFabricRowPushdown",
            default=(
                inherited.allow_fabric_row_pushdown
                if inherited is not None
                else False
            ),
        ),
    )


def _rule_map(
    value: Any,
    path: str,
    inherited: EntitlementRule,
) -> Mapping[str, EntitlementRule]:
    if value is None:
        return MappingProxyType({})
    raw = _object(value, path)
    rules: dict[str, EntitlementRule] = {}
    for name, rule_value in sorted(raw.items()):
        resource = _string(name, f"{path} key")
        if _SECRET_KEY_RE.search(resource):
            raise ValueError(f"{path}.{resource} is secret-like and forbidden")
        rules[resource] = _entitlement_rule(
            rule_value,
            f"{path}.{resource}",
            inherited=inherited,
        )
    return MappingProxyType(rules)


def _entitlements(value: Any, path: str) -> Entitlements:
    raw = _object(value, path)
    _strict(raw, _ENTITLEMENT_RULE_FIELDS | {"concepts", "properties"}, path)
    base = _entitlement_rule(
        {key: item for key, item in raw.items() if key in _ENTITLEMENT_RULE_FIELDS},
        path,
    )
    return Entitlements(
        classification=base.classification,
        allowed_roles=base.allowed_roles,
        allowed_groups=base.allowed_groups,
        allowed_scopes=base.allowed_scopes,
        allowed_purposes=base.allowed_purposes,
        mask=base.mask,
        row_constraints=base.row_constraints,
        disclose_source=base.disclose_source,
        policy_ids=base.policy_ids,
        allow_fabric_masking=base.allow_fabric_masking,
        allow_fabric_row_pushdown=base.allow_fabric_row_pushdown,
        concepts=_rule_map(raw.get("concepts"), f"{path}.concepts", base),
        properties=_rule_map(raw.get("properties"), f"{path}.properties", base),
    )


def _resolution(value: Any, path: str) -> RuntimeResolution:
    raw = _object(value, path)
    _strict(
        raw,
        {
            "mode",
            "joinVariable",
            "canonicalKeyRegex",
            "canonicalKeyPrefix",
            "scopeBindingVariable",
            "observableBindings",
            "policyProfile",
            "resolver",
        },
        path,
    )
    mode = _string(raw.get("mode"), f"{path}.mode")
    if mode not in _RESOLUTION_MODES:
        raise ValueError(f"{path}.mode is invalid")
    configured_fields = set(raw) - {"mode"}
    if mode == "none":
        if configured_fields:
            raise ValueError(f"{path} cannot configure bindings when mode is none")
        return RuntimeResolution(
            mode=mode,
            join_variable=None,
            canonical_key_regex=None,
            canonical_key_prefix=None,
            scope_binding_variable=None,
            observable_bindings=MappingProxyType({}),
            policy_profile=None,
            resolver=None,
        )

    required = {
        "joinVariable",
        "scopeBindingVariable",
        "observableBindings",
        "policyProfile",
        "resolver",
    }
    missing = required - set(raw)
    if missing:
        raise ValueError(f"{path} is missing required fields: {', '.join(sorted(missing))}")
    join_variable = _binding_variable(raw.get("joinVariable"), f"{path}.joinVariable")
    scope_variable = _binding_variable(
        raw.get("scopeBindingVariable"), f"{path}.scopeBindingVariable"
    )
    canonical_regex = raw.get("canonicalKeyRegex")
    canonical_prefix = raw.get("canonicalKeyPrefix")
    if canonical_regex is not None:
        canonical_regex = _string(canonical_regex, f"{path}.canonicalKeyRegex")
        try:
            re.compile(canonical_regex)
        except re.error as exc:
            raise ValueError(f"{path}.canonicalKeyRegex is invalid: {exc}") from exc
    if canonical_prefix is not None:
        canonical_prefix = _string(canonical_prefix, f"{path}.canonicalKeyPrefix")
    if canonical_regex is None and canonical_prefix is None:
        raise ValueError(
            f"{path} requires canonicalKeyRegex and/or canonicalKeyPrefix"
        )

    observables_raw = _object(raw.get("observableBindings"), f"{path}.observableBindings")
    if not observables_raw:
        raise ValueError(f"{path}.observableBindings must not be empty")
    observables: dict[str, str] = {}
    for field_name, binding_value in observables_raw.items():
        field_path = f"{path}.observableBindings.{field_name}"
        field = _string(field_name, field_path)
        normalized_field = _normalized_identifier(field)
        if _is_oracle_identifier(normalized_field):
            raise ValueError(f"{field_path} is an oracle/identifier field and is forbidden")
        binding = _binding_variable(binding_value, field_path)
        if _is_oracle_identifier(_normalized_identifier(binding)):
            raise ValueError(f"{field_path} targets an identifier binding and is forbidden")
        observables[field] = binding
    if len(set(observables.values())) != len(observables):
        raise ValueError(f"{path}.observableBindings binding variables must be unique")
    reserved = {join_variable, scope_variable}
    overlap = reserved & set(observables.values())
    if overlap:
        raise ValueError(
            f"{path}.observableBindings cannot reuse join/scope variables: "
            f"{', '.join(sorted(overlap))}"
        )
    if join_variable == scope_variable:
        raise ValueError(f"{path} join and scope binding variables must be unique")

    return RuntimeResolution(
        mode=mode,
        join_variable=join_variable,
        canonical_key_regex=canonical_regex,
        canonical_key_prefix=canonical_prefix,
        scope_binding_variable=scope_variable,
        observable_bindings=MappingProxyType(observables),
        policy_profile=_string(raw.get("policyProfile"), f"{path}.policyProfile"),
        resolver=_string(raw.get("resolver"), f"{path}.resolver"),
    )


def _binding_variable(value: Any, path: str) -> str:
    variable = _string(value, path)
    if not _BINDING_VARIABLE_RE.fullmatch(variable):
        raise ValueError(
            f"{path} must be a bare SPARQL binding variable "
            "(letters/underscore followed by letters, digits, or underscore)"
        )
    return variable


def _normalized_identifier(value: str) -> str:
    return value.strip().casefold().replace("-", "_").replace(".", "_")


def _is_oracle_identifier(value: str) -> bool:
    return (
        value in _ORACLE_FIELD_NAMES
        or "oracle" in value
        or "canonical" in value
        or value == "id"
        or value.endswith("_id")
    )


def _auth(value: Any, path: str) -> AuthMetadata:
    raw = _object(value, path)
    _strict(raw, {"mode", "delegation"}, path)
    mode = _string(raw.get("mode"), f"{path}.mode")
    delegation = _string(raw.get("delegation"), f"{path}.delegation")
    if mode not in _AUTH_MODES:
        raise ValueError(f"{path}.mode is invalid")
    if delegation not in _DELEGATIONS:
        raise ValueError(f"{path}.delegation is invalid")
    if (mode == "delegated") != (delegation != "none"):
        raise ValueError(f"{path}.delegation must match delegated auth mode")
    return AuthMetadata(mode=mode, delegation=delegation)


def parse_manifest(document: Mapping[str, Any]) -> CatalogManifest:
    """Parse manifest v1 into immutable values without touching the filesystem."""
    raw = dict(document)
    _reject_secrets(raw)
    _strict(
        raw,
        {"catalogManifestVersion", "generation", "contentHash", "conceptBase", "sources"},
        "manifest",
    )
    if raw.get("catalogManifestVersion") != MANIFEST_VERSION:
        raise ValueError("catalogManifestVersion must be '1'")
    sources_raw = raw.get("sources")
    if not isinstance(sources_raw, list) or not sources_raw:
        raise ValueError("manifest.sources must be a non-empty array")

    sources: list[CatalogSource] = []
    source_ids: set[str] = set()
    concept_owners: dict[str, str] = {}
    for index, value in enumerate(sources_raw):
        path = f"manifest.sources[{index}]"
        item = _object(value, path)
        _strict(
            item,
            {
                "sourceId",
                "kind",
                "ref",
                "concepts",
                "csi",
                "r2rml",
                "statisticsSnapshot",
                "joinKeys",
                "entitlements",
                "runtimeResolution",
                "auth",
                "capabilities",
            },
            path,
        )
        source_id = _string(item.get("sourceId"), f"{path}.sourceId")
        kind = _string(item.get("kind"), f"{path}.kind")
        ref = _string(item.get("ref"), f"{path}.ref")
        expected_id = f"{kind}:{ref}"
        if source_id != expected_id:
            raise ValueError(f"{path}.sourceId must equal {expected_id!r}")
        if source_id in source_ids:
            raise ValueError(f"duplicate sourceId {source_id!r}")
        source_ids.add(source_id)
        concepts = _strings(item.get("concepts"), f"{path}.concepts")
        if not concepts:
            raise ValueError(f"{path}.concepts must not be empty")
        for concept in concepts:
            owner = concept_owners.setdefault(concept, source_id)
            if owner != source_id:
                raise ValueError(
                    f"concept {concept!r} overlaps owners {owner!r} and {source_id!r}"
                )
        csi = _artifact(item.get("csi"), f"{path}.csi")
        r2rml_value = item.get("r2rml")
        r2rml = None if r2rml_value is None else _artifact(r2rml_value, f"{path}.r2rml")
        if kind not in _SOURCE_KINDS_WITHOUT_R2RML and r2rml is None:
            raise ValueError(f"{path}.r2rml is required for relational execution")
        statistics = item.get("statisticsSnapshot")
        if statistics is not None:
            statistics = _object(statistics, f"{path}.statisticsSnapshot")
            parse_csi_statistics({"statistics": statistics})
            statistics = _freeze(_object(statistics, f"{path}.statisticsSnapshot"))
        sources.append(
            CatalogSource(
                source_id=source_id,
                kind=kind,
                ref=ref,
                concepts=concepts,
                csi=csi,
                r2rml=r2rml,
                statistics_snapshot=statistics,
                join_keys=_strings(item.get("joinKeys"), f"{path}.joinKeys"),
                entitlements=_entitlements(item.get("entitlements"), f"{path}.entitlements"),
                runtime_resolution=_resolution(
                    item.get("runtimeResolution"), f"{path}.runtimeResolution"
                ),
                auth=_auth(item.get("auth"), f"{path}.auth"),
                capabilities=(
                    None
                    if item.get("capabilities") is None
                    else parse_capabilities(item["capabilities"], f"{path}.capabilities")
                ),
            )
        )

    content_hash = _string(raw.get("contentHash"), "manifest.contentHash")
    if not _SHA256_RE.fullmatch(content_hash):
        raise ValueError("manifest.contentHash must be a lowercase SHA-256 digest")
    return CatalogManifest(
        catalog_manifest_version=MANIFEST_VERSION,
        generation=_string(raw.get("generation"), "manifest.generation"),
        content_hash=content_hash,
        concept_base=_string(raw.get("conceptBase"), "manifest.conceptBase"),
        sources=tuple(sources),
    )


def _repository_root(manifest_path: Path) -> Path:
    for candidate in (manifest_path.parent, *manifest_path.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return manifest_path.parent


def _resolve_artifact(root: Path, reference: ArtifactRef, path: str) -> Path:
    candidate = (root / Path(reference.path)).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError(f"{path}.path escapes the catalog root")
    if not candidate.is_file():
        raise ValueError(f"{path}.path does not exist: {reference.path}")
    actual = file_sha256(candidate)
    if actual != reference.sha256:
        raise ValueError(f"{path} hash drift: expected {reference.sha256}, got {actual}")
    return candidate


def _concepts(document: Mapping[str, Any]) -> tuple[str, ...]:
    conceptual = _object(document.get("conceptualModel"), "CSI conceptualModel")
    entities = conceptual.get("entities")
    if not isinstance(entities, list):
        raise ValueError("CSI conceptualModel.entities must be an array")
    names = tuple(
        _string(_object(item, "CSI entity").get("name"), "CSI entity.name")
        for item in entities
    )
    if len(set(names)) != len(names):
        raise ValueError("CSI conceptualModel contains duplicate entities")
    return tuple(sorted(names))


def _properties(document: Mapping[str, Any]) -> set[str]:
    conceptual = _object(document.get("conceptualModel"), "CSI conceptualModel")
    result: set[str] = set()
    for entity_index, entity_value in enumerate(conceptual.get("entities") or []):
        entity = _object(entity_value, f"CSI conceptualModel.entities[{entity_index}]")
        for prop_index, prop_value in enumerate(entity.get("properties") or []):
            prop = _object(
                prop_value,
                f"CSI conceptualModel.entities[{entity_index}].properties[{prop_index}]",
            )
            result.add(_string(prop.get("name"), "CSI conceptual property.name"))
    return result


def load_manifest(path: str | Path, *, root: str | Path | None = None) -> LoadedCatalog:
    """Load, hash-check, cross-check, and materialize a file-backed manifest."""
    manifest_path = Path(path).resolve()
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("catalog manifest must be a JSON object")
    manifest = parse_manifest(raw)
    actual_content_hash = canonical_content_hash(raw)
    if manifest.content_hash != actual_content_hash:
        raise ValueError(
            f"manifest content hash drift: expected {manifest.content_hash}, "
            f"got {actual_content_hash}"
        )
    artifact_root = Path(root).resolve() if root is not None else _repository_root(manifest_path)
    documents: list[Mapping[str, Any]] = []
    csi_paths: dict[str, Path] = {}
    r2rml_paths: dict[str, Path] = {}
    for index, source in enumerate(manifest.sources):
        item_path = f"manifest.sources[{index}]"
        csi_path = _resolve_artifact(artifact_root, source.csi, f"{item_path}.csi")
        csi_raw = json.loads(csi_path.read_text(encoding="utf-8"))
        if not isinstance(csi_raw, dict):
            raise ValueError(f"{item_path}.csi must contain a JSON object")
        if csi_raw.get("csiVersion") != "1":
            raise ValueError(f"{item_path}.csi csiVersion must be '1'")
        provenance = _object(csi_raw.get("provenance"), f"{item_path}.csi.provenance")
        csi_source = _object(provenance.get("source"), f"{item_path}.csi.provenance.source")
        if csi_source.get("kind") != source.kind or csi_source.get("ref") != source.ref:
            raise ValueError(f"{item_path}.csi source does not match manifest source")
        if provenance.get("producer") != source.csi.producer:
            raise ValueError(f"{item_path}.csi producer does not match manifest")
        if provenance.get("direction") != source.csi.direction:
            raise ValueError(f"{item_path}.csi direction does not match manifest")
        if _concepts(csi_raw) != tuple(sorted(source.concepts)):
            raise ValueError(f"{item_path}.concepts do not match CSI conceptual entities")
        properties = _properties(csi_raw)
        unknown_entitlement_concepts = set(source.entitlements.concepts) - set(
            source.concepts
        )
        if unknown_entitlement_concepts:
            raise ValueError(
                f"{item_path}.entitlements.concepts are absent from CSI: "
                f"{', '.join(sorted(unknown_entitlement_concepts))}"
            )
        unknown_entitlement_properties = set(source.entitlements.properties) - properties
        if unknown_entitlement_properties:
            raise ValueError(
                f"{item_path}.entitlements.properties are absent from CSI: "
                f"{', '.join(sorted(unknown_entitlement_properties))}"
            )
        missing_join_keys = set(source.join_keys) - properties
        if missing_join_keys:
            raise ValueError(
                f"{item_path}.joinKeys are absent from CSI: "
                f"{', '.join(sorted(missing_join_keys))}"
            )
        parsed_stats = parse_csi_statistics(csi_raw)
        raw_stats = csi_raw.get("statistics")
        if parsed_stats is None:
            if source.statistics_snapshot is not None:
                raise ValueError(f"{item_path}.statisticsSnapshot has no CSI statistics")
        elif source.statistics_snapshot is None:
            raise ValueError(f"{item_path}.statisticsSnapshot is required for CSI statistics")
        elif canonical_json(raw_stats) != canonical_json(dict(source.statistics_snapshot)):
            raise ValueError(f"{item_path}.statisticsSnapshot does not match CSI statistics")
        # CSI bytes are integrity-pinned but CSI is an input to existing
        # adapters that require ordinary nested dict/list values.  The strict
        # immutable contract applies to the parsed manifest model above.
        documents.append(MappingProxyType(csi_raw))
        csi_paths[source.source_id] = csi_path
        if source.r2rml is not None:
            r2rml_paths[source.source_id] = _resolve_artifact(
                artifact_root, source.r2rml, f"{item_path}.r2rml"
            )

    loaded = LoadedCatalog(
        manifest=manifest,
        root=artifact_root,
        csi_documents=tuple(documents),
        csi_paths=MappingProxyType(csi_paths),
        r2rml_paths=MappingProxyType(r2rml_paths),
    )
    loaded.source_catalog()
    return loaded


def validate_finite_statistics(value: Any, path: str = "statistics") -> None:
    """Extra recursive guard used by adapters before CSI parsing."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} must contain only finite numbers")
    if isinstance(value, dict):
        for key, item in value.items():
            validate_finite_statistics(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            validate_finite_statistics(item, f"{path}[{index}]")
