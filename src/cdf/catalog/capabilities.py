"""Source-capability registry (ADR-0005 D4; CC-14).

The manifest declares, per source, what its engine can actually execute; the
planner consults these declarations instead of hardcoding engine kinds, so
admission refusals name the missing *capability* ("declares no GROUP BY
support"), never the engine. CC-14's rule binds declarations to reality:
**a declared capability whose probe fails does not exist** — onboarding runs
:func:`probe_capabilities` against the live executor and strips/fails on
mismatch.

This module is a deliberate leaf (stdlib imports only): it is shared by the
manifest model, the manifest builder, the query planner's catalog surface,
and the onboarding CLI, and must never participate in an import cycle.

v1 scope: ``aggregation`` is consulted (single-leg GROUP BY admission,
issue #14) and probed. ``textSearch`` and ``orderLimitPushdown`` are declared
and validated for shape so manifests are forward-compatible, but no planner
path consults them yet — they land with the FTS and ORDER/LIMIT work
(ADR-0005 §2.6).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # leaf discipline: runtime never imports the query package
    from cdf.query.types import SourceRef

#: Text-search dialects the ADR names; ``none`` means "cannot text-search".
TEXT_SEARCH_DIALECTS = frozenset(
    {"arangosearch", "tsvector", "snowflake-search", "clickhouse-token", "none"}
)


@dataclass(frozen=True)
class AggregationCapability:
    """What the source's native leg can compute for a pushed-down GROUP BY."""

    group_by: bool = False
    having: bool = False
    count_distinct: bool = False
    approx_count_distinct: str | None = None
    """Sketch algorithm name (e.g. ``"hll"``) when the engine offers an
    approximate distinct-count; ``None`` when it does not."""


@dataclass(frozen=True)
class TextSearchCapability:
    """Declared per ADR-0005 §2.6; consulted when `cdf:matchesText` lands."""

    dialect: str = "none"
    indexed_properties: tuple[str, ...] = ()
    analyzer: str | None = None
    """Pinned analyzer identifier (the postgres_fdw shippability lesson):
    a match is only shippable when both sides agree on the analyzer rev."""
    scored: bool = False
    """Unscored dialects can match but never rank (no cross-source fusion)."""


@dataclass(frozen=True)
class SourceCapabilities:
    """Everything the planner may consult about one source's engine."""

    aggregation: AggregationCapability = field(default_factory=AggregationCapability)
    text_search: TextSearchCapability = field(default_factory=TextSearchCapability)
    order_limit_pushdown: bool = False


#: Safe-deny default: a source that declares nothing can do nothing special.
NO_CAPABILITIES = SourceCapabilities()


def default_capabilities_for_kind(kind: str) -> SourceCapabilities:
    """Legacy per-kind defaults — the ONE place engine-kind knowledge lives.

    This is the planner's former ``_AGGREGATION_CAPABLE_KINDS`` moved behind
    the registry: used only when a catalog has no manifest applied (the
    CSI-only demo path). A manifest declaration always overrides. The values
    state what each executor verifiably does today:

    - ``postgresql`` (Ontop SPARQL leg) and ``arango`` (arango-sparql-py)
      execute GROUP BY / HAVING / COUNT(DISTINCT) natively.
    - ``snowflake`` / ``clickhouse`` native executors compile BGP + FILTER
      only — no aggregation emission yet (rung-2 exclusion, issue #14).
    - Unknown kinds get :data:`NO_CAPABILITIES` (safe-deny).
    """
    if kind in {"postgresql", "arango"}:
        return SourceCapabilities(
            aggregation=AggregationCapability(group_by=True, having=True, count_distinct=True)
        )
    return NO_CAPABILITIES


def parse_capabilities(value: Any, path: str) -> SourceCapabilities:
    """Parse and strictly validate one manifest ``capabilities`` block."""
    block = _object(value, path)
    _strict(block, {"aggregation", "textSearch", "orderLimitPushdown"}, path)

    aggregation = AggregationCapability()
    if "aggregation" in block:
        raw = _object(block["aggregation"], f"{path}.aggregation")
        _strict(
            raw,
            {"groupBy", "having", "countDistinct", "approxCountDistinct"},
            f"{path}.aggregation",
        )
        approx = raw.get("approxCountDistinct")
        if approx is not None and (not isinstance(approx, str) or not approx):
            raise ValueError(
                f"{path}.aggregation.approxCountDistinct must be a non-empty string or null"
            )
        aggregation = AggregationCapability(
            group_by=_bool(raw.get("groupBy", False), f"{path}.aggregation.groupBy"),
            having=_bool(raw.get("having", False), f"{path}.aggregation.having"),
            count_distinct=_bool(
                raw.get("countDistinct", False), f"{path}.aggregation.countDistinct"
            ),
            approx_count_distinct=approx,
        )

    text_search = TextSearchCapability()
    if "textSearch" in block:
        raw = _object(block["textSearch"], f"{path}.textSearch")
        _strict(
            raw,
            {"dialect", "indexedProperties", "analyzer", "scored"},
            f"{path}.textSearch",
        )
        dialect = raw.get("dialect", "none")
        if dialect not in TEXT_SEARCH_DIALECTS:
            raise ValueError(
                f"{path}.textSearch.dialect must be one of "
                f"{sorted(TEXT_SEARCH_DIALECTS)}, got {dialect!r}"
            )
        analyzer = raw.get("analyzer")
        if analyzer is not None and (not isinstance(analyzer, str) or not analyzer):
            raise ValueError(f"{path}.textSearch.analyzer must be a non-empty string or null")
        properties = raw.get("indexedProperties", [])
        if not isinstance(properties, list) or any(
            not isinstance(item, str) or not item for item in properties
        ):
            raise ValueError(
                f"{path}.textSearch.indexedProperties must be an array of non-empty strings"
            )
        scored = _bool(raw.get("scored", False), f"{path}.textSearch.scored")
        if dialect == "none" and (properties or scored):
            raise ValueError(
                f"{path}.textSearch declares dialect 'none' but claims indexed "
                "properties or scoring — a capability that cannot execute must not "
                "declare features (CC-14)"
            )
        text_search = TextSearchCapability(
            dialect=dialect,
            indexed_properties=tuple(properties),
            analyzer=analyzer,
            scored=scored,
        )

    return SourceCapabilities(
        aggregation=aggregation,
        text_search=text_search,
        order_limit_pushdown=_bool(
            block.get("orderLimitPushdown", False), f"{path}.orderLimitPushdown"
        ),
    )


def capabilities_document(capabilities: SourceCapabilities) -> dict[str, Any]:
    """Serialize for the manifest builder (round-trips through parse)."""
    return {
        "aggregation": {
            "groupBy": capabilities.aggregation.group_by,
            "having": capabilities.aggregation.having,
            "countDistinct": capabilities.aggregation.count_distinct,
            "approxCountDistinct": capabilities.aggregation.approx_count_distinct,
        },
        "textSearch": {
            "dialect": capabilities.text_search.dialect,
            "indexedProperties": list(capabilities.text_search.indexed_properties),
            "analyzer": capabilities.text_search.analyzer,
            "scored": capabilities.text_search.scored,
        },
        "orderLimitPushdown": capabilities.order_limit_pushdown,
    }


def aggregation_probe_sparql(concept_iri: str) -> str:
    """The minimal GROUP BY the probe pushes at a source (COUNT over one class)."""
    return f"SELECT (COUNT(?s) AS ?n) WHERE {{ ?s a <{concept_iri}> }}"


def probe_capabilities(
    source: SourceRef,
    declared: SourceCapabilities,
    executor: Any,
    probe_concept_iri: str,
) -> list[str]:
    """CC-14 onboarding check: run each *consulted* declared capability against
    the live executor; return human-readable failures (empty = verified).

    v1 probes ``aggregation.groupBy`` (the one capability the planner consults).
    A declared-false capability is never probed — absence needs no proof.
    ``executor`` satisfies the ``SourceExecutor`` protocol (``execute(SubQuery)
    -> SourceResult``); imported lazily to keep this module a leaf.
    """
    failures: list[str] = []
    if declared.aggregation.group_by:
        from cdf.query.types import SubQuery  # leaf discipline: runtime-lazy

        probe = SubQuery(
            source=source,
            triples=(),
            variables=("?n",),
            sparql=aggregation_probe_sparql(probe_concept_iri),
        )
        try:
            result = executor.execute(probe)
        except Exception as exc:  # noqa: BLE001 — any engine error means "cannot"
            failures.append(
                f"{source.source_id}: declared aggregation.groupBy but the probe "
                f"raised {type(exc).__name__}: {exc}"
            )
        else:
            error = getattr(result, "error", None)
            if error:
                failures.append(
                    f"{source.source_id}: declared aggregation.groupBy but the "
                    f"probe failed: {error}"
                )
            else:
                # Non-error is NOT proof (a lenient leg can drop the GROUP BY
                # and answer something else without complaining — observed on
                # the ClickHouse BGP compiler). The COUNT probe has exactly one
                # honest answer shape: one row binding ?n to a non-negative int.
                rows = tuple(getattr(result, "rows", ()) or ())
                single = len(rows) == 1 and isinstance(rows[0], Mapping)
                value = rows[0].get("n") if single else None
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    failures.append(
                        f"{source.source_id}: declared aggregation.groupBy but the "
                        f"probe's COUNT came back malformed (rows={len(rows)}, "
                        f"n={value!r}) — the engine did not execute the aggregation"
                    )
    return failures


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    return dict(value)


def _strict(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise ValueError(f"{path} has unknown fields: {', '.join(sorted(unexpected))}")


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value
