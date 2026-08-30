"""Concept→source index built from CSI v1 mapping documents (M5 / E1).

Each federated source publishes a ``CSI v1`` document (r2g's forward CSI for a
relational source, ``arango-schema-analyzer``'s reverse CSI for the graph). The
catalog reads their **conceptual models** and records, per conceptual IRI, which
source backs it — so the planner can route each triple pattern to the right
source.

Conceptual names are turned into IRIs with the shared concept namespace
(``urn:arango-sparql:concept#`` by default) — the *same* namespace r2g's R2RML
emitter and the ``arango-sparql-py`` AQL leg use, so a partitioned SPARQL query
means the same thing everywhere.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .types import SourceRef

if TYPE_CHECKING:
    from cdf.catalog.model import (
        AuthMetadata,
        CatalogManifest,
        EntitlementRule,
        Entitlements,
        RuntimeResolution,
    )

DEFAULT_CONCEPT_BASE = "urn:arango-sparql:concept#"
STATISTICS_VERSION = "1"


@dataclass(frozen=True)
class PropertyStatistics:
    """Optional cardinality hints for one conceptual property."""

    ndv: int | None = None
    selectivity: float | None = None


@dataclass(frozen=True)
class ClassStatistics:
    """Optional row/byte estimates and property distributions for one class."""

    row_count: int | None = None
    estimated_bytes: int | None = None
    properties: dict[str, PropertyStatistics] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceStatistics:
    """Validated, versioned CSI statistics consumed by the query planner.

    CSI documents without ``statistics`` remain valid. Unknown additive fields
    are ignored so version 1 producers can evolve without breaking readers.
    """

    version: str = STATISTICS_VERSION
    snapshot_id: str | None = None
    as_of: str | None = None
    row_count: int | None = None
    estimated_bytes: int | None = None
    cost_per_gb_usd: float | None = None
    classes: dict[str, ClassStatistics] = field(default_factory=dict)
    properties: dict[str, PropertyStatistics] = field(default_factory=dict)


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _optional_number(
    value: Any,
    path: str,
    *,
    integer: bool = False,
    maximum: float | None = None,
) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (maximum is not None and number > maximum):
        suffix = f" no greater than {maximum}" if maximum is not None else ""
        raise ValueError(f"{path} must be a finite non-negative number{suffix}")
    if integer:
        if not number.is_integer():
            raise ValueError(f"{path} must be a non-negative integer")
        return int(number)
    return number


def _row_count(raw: dict[str, Any], path: str) -> int | None:
    """Accept ``rowCount`` and the CSI-neutral alias ``cardinality``."""
    row_count = _optional_number(raw.get("rowCount"), f"{path}.rowCount", integer=True)
    cardinality = _optional_number(
        raw.get("cardinality"), f"{path}.cardinality", integer=True
    )
    if row_count is not None and cardinality is not None and row_count != cardinality:
        raise ValueError(f"{path}.rowCount and {path}.cardinality disagree")
    selected = row_count if row_count is not None else cardinality
    return int(selected) if selected is not None else None


def _property_statistics(value: Any, path: str) -> PropertyStatistics:
    raw = _mapping(value, path)
    ndv = _optional_number(raw.get("ndv"), f"{path}.ndv", integer=True)
    selectivity = _optional_number(
        raw.get("selectivity"), f"{path}.selectivity", maximum=1.0
    )
    return PropertyStatistics(
        ndv=int(ndv) if ndv is not None else None,
        selectivity=float(selectivity) if selectivity is not None else None,
    )


def parse_csi_statistics(document: dict[str, Any]) -> SourceStatistics | None:
    """Parse the additive CSI ``statistics`` v1 contract, or return ``None``.

    Contract shape::

        statistics: {
          version, snapshotId?, asOf?,
          source: {rowCount|cardinality?, estimatedBytes?, costPerGbUsd?},
          classes: {Class: {rowCount|cardinality?, estimatedBytes?,
                            properties: {property: {ndv?, selectivity?}}}},
          properties: {property: {ndv?, selectivity?}}
        }
    """
    value = document.get("statistics")
    if value is None:
        return None
    raw = _mapping(value, "statistics")
    version = raw.get("version")
    if version != STATISTICS_VERSION:
        raise ValueError(f"statistics.version must be {STATISTICS_VERSION!r}")
    source_raw = _mapping(raw.get("source", {}), "statistics.source")
    classes_raw = _mapping(raw.get("classes", {}), "statistics.classes")
    properties_raw = _mapping(raw.get("properties", {}), "statistics.properties")

    classes: dict[str, ClassStatistics] = {}
    for name, value in classes_raw.items():
        if not isinstance(name, str) or not name:
            raise ValueError("statistics.classes keys must be non-empty strings")
        item = _mapping(value, f"statistics.classes.{name}")
        item_properties = _mapping(
            item.get("properties", {}), f"statistics.classes.{name}.properties"
        )
        class_bytes = _optional_number(
            item.get("estimatedBytes"),
            f"statistics.classes.{name}.estimatedBytes",
            integer=True,
        )
        classes[name] = ClassStatistics(
            row_count=_row_count(item, f"statistics.classes.{name}"),
            estimated_bytes=int(class_bytes) if class_bytes is not None else None,
            properties={
                prop: _property_statistics(
                    prop_value, f"statistics.classes.{name}.properties.{prop}"
                )
                for prop, prop_value in item_properties.items()
                if isinstance(prop, str) and prop
            },
        )
        if len(classes[name].properties) != len(item_properties):
            raise ValueError(
                f"statistics.classes.{name}.properties keys must be non-empty strings"
            )

    properties: dict[str, PropertyStatistics] = {}
    for name, item in properties_raw.items():
        if not isinstance(name, str) or not name:
            raise ValueError("statistics.properties keys must be non-empty strings")
        properties[name] = _property_statistics(item, f"statistics.properties.{name}")

    estimated_bytes = _optional_number(
        source_raw.get("estimatedBytes"), "statistics.source.estimatedBytes", integer=True
    )
    cost_rate = _optional_number(
        source_raw.get("costPerGbUsd"), "statistics.source.costPerGbUsd"
    )
    return SourceStatistics(
        version=version,
        snapshot_id=_optional_string(raw.get("snapshotId"), "statistics.snapshotId"),
        as_of=_optional_string(raw.get("asOf"), "statistics.asOf"),
        row_count=_row_count(source_raw, "statistics.source"),
        estimated_bytes=int(estimated_bytes) if estimated_bytes is not None else None,
        cost_per_gb_usd=float(cost_rate) if cost_rate is not None else None,
        classes=classes,
        properties=properties,
    )


def source_ref_from_csi(doc: dict[str, Any], override_id: str | None = None) -> SourceRef:
    """Derive a :class:`SourceRef` from a CSI document's provenance.

    The default ``source_id`` is ``"<kind>:<ref>"`` (or just ``"<kind>"`` when no
    ref). Shared by the catalog and any caller that needs to key executors by
    the *same* id the catalog routes to (e.g. the golden-eval harness).
    """
    prov = doc.get("provenance") or {}
    src = prov.get("source") or {}
    kind = str(src.get("kind") or "unknown")
    ref = str(src.get("ref") or "")
    if override_id:
        source_id = override_id
    else:
        source_id = f"{kind}:{ref}" if ref else kind
    return SourceRef(source_id=source_id, kind=kind, ref=ref)


class SourceCatalog:
    """Maps conceptual class/property IRIs to the source(s) that back them."""

    def __init__(self, concept_base: str = DEFAULT_CONCEPT_BASE) -> None:
        self.concept_base = concept_base
        self.manifest_generation: str | None = None
        # class IRI -> source (a class is owned by exactly one source; the
        # ontology-alignment layer, M3, guarantees a single canonical name).
        self._class_source: dict[str, SourceRef] = {}
        # property/relationship IRI -> sources that expose it (a plain property
        # name like "name" can legitimately exist in several sources; the
        # planner disambiguates by the subject's class).
        self._property_sources: dict[str, set[SourceRef]] = {}
        # class IRI -> its own property local-names. The planner doesn't need
        # this (it routes by class), but NL grounding does: it must tell an LLM
        # which properties belong to *which* class, or the model attaches a
        # property to the wrong entity (e.g. a Chunk's document_id onto a
        # Document) and the query silently returns no rows.
        self._class_properties: dict[str, set[str]] = {}
        # source_id -> relationship (object-property) local-names it exposes.
        self._relationships: dict[str, set[str]] = {}
        # Structured relationship shapes per source: (type, fromEntity, toEntity).
        # `vocabulary()` emits both — the flat names (the NL schema card's need)
        # and the structure (a diagram is unreadable without edges).
        self._relationship_details: dict[str, set[tuple[str, str, str]]] = {}
        # source_id -> validated optional CSI statistics.
        self._statistics: dict[str, SourceStatistics] = {}
        # Additive M11 catalog metadata. Legacy CSI-directory catalogs leave
        # these empty, preserving the prior behavior.
        self._source_generations: dict[str, str] = {}
        self._entitlements: dict[str, Entitlements] = {}
        self._join_keys: dict[str, tuple[str, ...]] = {}
        self._runtime_resolution: dict[str, RuntimeResolution] = {}
        self._auth: dict[str, AuthMetadata] = {}

    def iri(self, name: str) -> str:
        """The conceptual IRI for a bare entity/property name."""
        return f"{self.concept_base}{name}"

    @classmethod
    def from_csi_documents(
        cls,
        documents: Iterable[dict[str, Any]],
        *,
        source_ids: Sequence[str] | None = None,
        concept_base: str = DEFAULT_CONCEPT_BASE,
    ) -> SourceCatalog:
        """Build a catalog from CSI documents.

        Args:
            documents: parsed ``CSI v1`` documents (any producer/direction).
            source_ids: optional explicit source id per document, positionally
                aligned; when omitted, each id is derived from the document's
                provenance (``"<kind>:<ref>"``).
            concept_base: concept IRI namespace (must match the producers').
        """
        catalog = cls(concept_base=concept_base)
        for i, doc in enumerate(documents):
            override = source_ids[i] if source_ids is not None else None
            catalog.add_csi(doc, source_id=override)
        return catalog

    def add_csi(self, document: dict[str, Any], *, source_id: str | None = None) -> SourceRef:
        """Register one CSI document's conceptual model under its source."""
        source = source_ref_from_csi(document, source_id)
        statistics = parse_csi_statistics(document)
        if statistics is not None:
            self._statistics[source.source_id] = statistics
        conceptual = document.get("conceptualModel") or {}

        for entity in conceptual.get("entities") or []:
            name = entity.get("name")
            if not name:
                continue
            self._class_source[self.iri(name)] = source
            class_props = self._class_properties.setdefault(self.iri(name), set())
            for prop in entity.get("properties") or []:
                prop_name = prop.get("name")
                if prop_name:
                    self._property_sources.setdefault(self.iri(prop_name), set()).add(source)
                    class_props.add(prop_name)

        for rel in conceptual.get("relationships") or []:
            rtype = rel.get("type")
            if rtype:
                self._property_sources.setdefault(self.iri(rtype), set()).add(source)
                self._relationships.setdefault(source.source_id, set()).add(rtype)
                self._relationship_details.setdefault(source.source_id, set()).add(
                    (rtype, rel.get("fromEntity") or "", rel.get("toEntity") or "")
                )

        return source

    # -- lookups -----------------------------------------------------------

    def source_of_class(self, iri: str) -> SourceRef | None:
        """The source backing a class IRI, or ``None`` if unknown."""
        return self._class_source.get(str(iri))

    def sources_of_property(self, iri: str) -> set[SourceRef]:
        """The set of sources exposing a property/relationship IRI (possibly
        empty; possibly >1 when the plain name is shared across sources)."""
        return set(self._property_sources.get(str(iri), set()))

    def statistics_for(self, source: SourceRef | str) -> SourceStatistics | None:
        """Validated statistics for a source, or ``None`` when CSI omitted them."""
        source_id = source.source_id if isinstance(source, SourceRef) else source
        return self._statistics.get(source_id)

    def apply_manifest(self, manifest: CatalogManifest) -> None:
        """Attach validated additive M11 metadata to a CSI-built catalog."""
        self.manifest_generation = manifest.generation
        for source in manifest.sources:
            self._source_generations[source.source_id] = source.csi.generation
            self._entitlements[source.source_id] = source.entitlements
            self._join_keys[source.source_id] = source.join_keys
            self._runtime_resolution[source.source_id] = source.runtime_resolution
            self._auth[source.source_id] = source.auth

    def generation_for(self, source: SourceRef | str) -> str | None:
        source_id = source.source_id if isinstance(source, SourceRef) else source
        return self._source_generations.get(source_id)

    def entitlements_for(self, source: SourceRef | str) -> Entitlements | None:
        source_id = source.source_id if isinstance(source, SourceRef) else source
        return self._entitlements.get(source_id)

    def entitlement_rule_for(
        self,
        source: SourceRef | str,
        resource_type: str,
        name: str,
    ) -> EntitlementRule | None:
        """Resolve a concept/property override over its source-wide default."""
        entitlements = self.entitlements_for(source)
        if entitlements is None:
            return None
        return entitlements.rule_for(resource_type, name)

    def join_keys_for(self, source: SourceRef | str) -> tuple[str, ...]:
        source_id = source.source_id if isinstance(source, SourceRef) else source
        return self._join_keys.get(source_id, ())

    def resolution_for(self, source: SourceRef | str) -> RuntimeResolution | None:
        source_id = source.source_id if isinstance(source, SourceRef) else source
        return self._runtime_resolution.get(source_id)

    def auth_for(self, source: SourceRef | str) -> AuthMetadata | None:
        source_id = source.source_id if isinstance(source, SourceRef) else source
        return self._auth.get(source_id)

    def source_metadata_for(self, source: SourceRef | str) -> dict[str, str]:
        """Safe additive source metadata from the authoritative manifest."""
        source_id = source.source_id if isinstance(source, SourceRef) else source
        metadata: dict[str, str] = {}
        generation = self.generation_for(source_id)
        if generation is not None:
            metadata["generation"] = generation
        entitlements = self.entitlements_for(source_id)
        if entitlements is not None:
            metadata["classification"] = entitlements.classification
        if self.manifest_generation is not None:
            metadata["manifest_generation"] = self.manifest_generation
        return metadata

    def safe_metadata_for(self, source: SourceRef | str) -> dict[str, str]:
        """Generation/classification only; safe for agent introspection."""
        return self.source_metadata_for(source)

    @property
    def sources(self) -> set[SourceRef]:
        """Every distinct source known to the catalog."""
        out = set(self._class_source.values())
        for srcs in self._property_sources.values():
            out |= srcs
        return out

    def _local(self, iri: str) -> str:
        base = self.concept_base
        return iri[len(base):] if iri.startswith(base) else iri

    def vocabulary(self) -> list[dict[str, Any]]:
        """Per-source, **class-structured** vocabulary for NL-query grounding.

        One entry per source::

            {source_id, kind, ref,
             classes: [{name, properties: [...]}, ...],   # properties per class
             relationships: [...]}                         # object-property names

        Local names (``concept_base`` stripped), sorted for determinism. The
        per-class grouping is essential: it lets an NL front-end tell an LLM
        that ``document_id`` belongs to ``Chunk`` and not ``Document`` — a flat
        property bag lets the model attach a property to the wrong class and get
        a silently-empty answer.
        """
        by_source: dict[str, dict[str, Any]] = {}

        def _entry(src: SourceRef) -> dict[str, Any]:
            return by_source.setdefault(
                src.source_id,
                {
                    "source_id": src.source_id,
                    "kind": src.kind,
                    "ref": src.ref,
                    "classes": [],
                    "relationships": sorted(self._relationships.get(src.source_id, set())),
                    "relationshipDetails": [
                        {"type": r[0], "from": r[1], "to": r[2]}
                        for r in sorted(
                            self._relationship_details.get(src.source_id, set())
                        )
                    ],
                    **self.safe_metadata_for(src),
                },
            )

        for iri, src in sorted(self._class_source.items(), key=lambda kv: kv[0]):
            _entry(src)["classes"].append(
                {
                    "name": self._local(iri),
                    "properties": sorted(self._class_properties.get(iri, set())),
                }
            )

        return sorted(by_source.values(), key=lambda e: e["source_id"])
