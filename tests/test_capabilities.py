"""Capability registry v1 (ADR-0005 D4; CC-14).

The manifest declares per-source capabilities; the planner consults them
instead of engine kinds; onboarding probes bind declarations to reality.
"""

from __future__ import annotations

import pytest

from cdf.catalog.capabilities import (
    NO_CAPABILITIES,
    AggregationCapability,
    SourceCapabilities,
    TextSearchCapability,
    aggregation_probe_sparql,
    capabilities_document,
    default_capabilities_for_kind,
    parse_capabilities,
    probe_capabilities,
)
from cdf.query import SourceCatalog, partition_query
from cdf.query.executor import SourceResult
from cdf.query.planner import UnsupportedQueryError
from cdf.query.types import SourceRef

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"


# ---------------------------------------------------------------- parsing


def test_parse_full_block_roundtrips_through_the_builder_serializer():
    declared = SourceCapabilities(
        aggregation=AggregationCapability(
            group_by=True, having=True, count_distinct=True, approx_count_distinct="hll"
        ),
        text_search=TextSearchCapability(
            dialect="arangosearch",
            indexed_properties=("Document.text",),
            analyzer="text_en@rev",
            scored=True,
        ),
        order_limit_pushdown=True,
    )
    assert parse_capabilities(capabilities_document(declared), "caps") == declared


def test_parse_empty_block_is_safe_deny():
    assert parse_capabilities({}, "caps") == NO_CAPABILITIES


@pytest.mark.parametrize(
    ("block", "fragment"),
    [
        ({"aggregation": {"groupBy": "yes"}}, "must be a boolean"),
        ({"aggregation": {"rollup": True}}, "unknown fields"),
        ({"textSearch": {"dialect": "lucene"}}, "dialect must be one of"),
        ({"unknown": True}, "unknown fields"),
        ({"orderLimitPushdown": 1}, "must be a boolean"),
        ({"aggregation": {"approxCountDistinct": ""}}, "non-empty string or null"),
        ({"textSearch": {"indexedProperties": [""]}}, "non-empty strings"),
    ],
)
def test_parse_rejects_malformed_blocks(block, fragment):
    with pytest.raises(ValueError, match=fragment):
        parse_capabilities(block, "caps")


def test_cc14_dialect_none_may_not_claim_features():
    # A capability that cannot execute must not declare features (CC-14).
    with pytest.raises(ValueError, match="CC-14"):
        parse_capabilities(
            {"textSearch": {"dialect": "none", "scored": True}}, "caps"
        )


# ------------------------------------------------------- per-kind defaults


@pytest.mark.parametrize("kind", ["postgresql", "arango"])
def test_sparql_complete_legs_default_to_aggregation(kind):
    assert default_capabilities_for_kind(kind).aggregation.group_by is True


@pytest.mark.parametrize("kind", ["snowflake", "clickhouse", "duckdb", ""])
def test_native_and_unknown_kinds_default_to_safe_deny(kind):
    assert default_capabilities_for_kind(kind) == NO_CAPABILITIES


# ------------------------------------------- catalog: manifest beats kind


def _csi(kind, ref, entities):
    return {
        "csiVersion": "1",
        "conceptualModel": {
            "entities": [
                {"name": n, "properties": [{"name": p} for p in props]}
                for n, props in entities
            ]
        },
        "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
        "provenance": {
            "producer": "test",
            "direction": "forward",
            "source": {"kind": kind, "ref": ref},
        },
    }


PG_CSI = _csi("postgresql", "crm", [("Account", ["accountId", "currentProductTier"])])
AGG_PG = (
    PREFIX + "SELECT ?tier (COUNT(?a) AS ?n) WHERE { "
    "?a a c:Account ; c:currentProductTier ?tier } GROUP BY ?tier"
)


def test_csi_only_catalog_falls_back_to_kind_defaults():
    catalog = SourceCatalog.from_csi_documents([PG_CSI])
    assert catalog.capabilities_for("postgresql:crm").aggregation.group_by is True
    assert catalog.capabilities_for("nonexistent:source") == NO_CAPABILITIES


def test_manifest_declaration_overrides_the_kind_default():
    # A postgres source whose manifest says "no GROUP BY" must be refused even
    # though the kind default would admit it — declarations win (ADR-0005 D4).
    catalog = SourceCatalog.from_csi_documents([PG_CSI])
    assert partition_query(AGG_PG, catalog).sub_queries  # kind default admits
    catalog._capabilities["postgresql:crm"] = NO_CAPABILITIES  # manifest-applied state
    with pytest.raises(UnsupportedQueryError, match="declares no GROUP BY capability"):
        partition_query(AGG_PG, catalog)


# ------------------------------------------------------------------ probe


class _FakeExecutor:
    """Mirrors the SourceExecutor protocol: ``execute(SubQuery) -> SourceResult``."""

    def __init__(self, fail_with: Exception | None = None):
        self.fail_with = fail_with
        self.executed = []

    def execute(self, subquery) -> SourceResult:
        self.executed.append(subquery)
        if self.fail_with is not None:
            raise self.fail_with
        return SourceResult(rows=({"n": 3},), native_query="SELECT COUNT(*) ...")


SRC = SourceRef(source_id="postgresql:crm", kind="postgresql", ref="crm")
DECLARED = SourceCapabilities(aggregation=AggregationCapability(group_by=True))
CONCEPT = "urn:arango-sparql:concept#Account"


def test_probe_passes_when_the_engine_answers_the_group_by():
    executor = _FakeExecutor()
    assert probe_capabilities(SRC, DECLARED, executor, CONCEPT) == []
    (probe,) = executor.executed
    assert probe.sparql == aggregation_probe_sparql(CONCEPT)
    assert "COUNT" in probe.sparql and CONCEPT in probe.sparql


def test_probe_fails_when_the_engine_raises():
    executor = _FakeExecutor(fail_with=RuntimeError("no GROUP BY emitter"))
    failures = probe_capabilities(SRC, DECLARED, executor, CONCEPT)
    assert len(failures) == 1
    assert "postgresql:crm" in failures[0]
    assert "aggregation.groupBy" in failures[0]
    assert "no GROUP BY emitter" in failures[0]


def test_probe_skips_capabilities_that_are_not_declared():
    executor = _FakeExecutor(fail_with=RuntimeError("must never run"))
    assert probe_capabilities(SRC, NO_CAPABILITIES, executor, CONCEPT) == []
    assert executor.executed == []


class _LenientExecutor:
    """A leg that answers WITHOUT error but never ran the GROUP BY — the
    failure mode the shape check exists for (observed on the ClickHouse
    compiler, which dropped the aggregation and returned plain rows)."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, subquery) -> SourceResult:
        return SourceResult(rows=self.rows)


@pytest.mark.parametrize(
    "rows",
    [
        (),                                     # empty: aggregation dropped
        ({"s": "urn:x"}, {"s": "urn:y"}),        # plain rows, no count
        ({"n": "3"},),                           # stringly-typed count
        ({"n": True},),                          # bool is not a count
        ({"n": -1},),                            # negative count is nonsense
        ({"n": 1}, {"n": 2}),                    # COUNT must be ONE row
    ],
)
def test_probe_rejects_non_error_results_that_did_not_aggregate(rows):
    failures = probe_capabilities(SRC, DECLARED, _LenientExecutor(rows), CONCEPT)
    assert len(failures) == 1
    assert "did not execute the aggregation" in failures[0]
