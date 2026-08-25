"""Single-leg aggregation admission (issue #14 — rung 2 of the ladder).

E1.5 admits a top-level GROUP BY whose whole pattern routes to ONE
aggregation-capable source (postgresql via Ontop, arango via the transpiler);
the leg gets the ORIGINAL query verbatim and its rows are final bindings.
Cross-source aggregation and aggregation on the native snowflake/clickhouse
legs stay refused by name.
"""

from __future__ import annotations

import pytest

from cdf.query import SourceCatalog, SourceResult, execute_plan, ground, partition_query
from cdf.query.planner import UnsupportedQueryError

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"


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


def _catalog(*sources):
    return SourceCatalog.from_csi_documents([_csi(*s) for s in sources])


PG = ("postgresql", "crm", [("Account", ["accountId", "currentProductTier"])])
AR = ("arango", "cmf", [("Document", ["accountId", "source", "role"])])
SF = ("snowflake", "telemetry", [("UsageMetric", ["accountId", "queryVolumeM"])])

AGG_PG = (
    PREFIX + "SELECT ?tier (COUNT(?a) AS ?n) WHERE { "
    "?a a c:Account ; c:currentProductTier ?tier } GROUP BY ?tier"
)


def test_single_source_aggregation_is_admitted_verbatim():
    plan = partition_query(AGG_PG, _catalog(PG))
    assert len(plan.sub_queries) == 1
    leg = plan.sub_queries[0]
    assert leg.source.source_id == "postgresql:crm"
    assert leg.sparql == AGG_PG  # verbatim — the engine runs the aggregation
    assert plan.join_keys == ()
    assert plan.projection == ("?tier", "?n")
    # The aggregate alias is declared leg-supplied (else it reads unavailable).
    assert "?n" in leg.variables and "?tier" in leg.variables


def test_admitted_aggregation_executes_as_final_bindings():
    plan = partition_query(AGG_PG, _catalog(PG))

    class _Agg:
        def execute(self, sq):
            return SourceResult(rows=({"tier": "Enterprise", "n": 2}, {"tier": "Free", "n": 1}))

    env = ground(execute_plan(plan, {"postgresql:crm": _Agg()}))
    assert env.status == "grounded"
    assert sorted(dict(b)["tier"] for b in env.bindings) == ["Enterprise", "Free"]


def test_arango_single_source_aggregation_is_admitted():
    q = (
        PREFIX + "SELECT ?source (COUNT(?d) AS ?n) WHERE { "
        "?d a c:Document ; c:source ?source ; c:role \"signal\" } GROUP BY ?source"
    )
    plan = partition_query(q, _catalog(AR))
    assert plan.sub_queries[0].source.kind == "arango"
    assert plan.sub_queries[0].sparql == q


def test_having_and_order_by_ride_the_verbatim_leg():
    q = (
        PREFIX + "SELECT ?tier (COUNT(?a) AS ?n) WHERE { "
        "?a a c:Account ; c:currentProductTier ?tier } "
        "GROUP BY ?tier HAVING (COUNT(?a) > 1) ORDER BY DESC(?n)"
    )
    plan = partition_query(q, _catalog(PG))
    assert plan.sub_queries[0].sparql == q


def test_cross_source_aggregation_is_refused_by_name():
    q = (
        PREFIX + "SELECT ?tier (COUNT(?d) AS ?n) WHERE { "
        "?a a c:Account ; c:currentProductTier ?tier ; c:accountId ?k . "
        "?d a c:Document ; c:accountId ?k } GROUP BY ?tier"
    )
    with pytest.raises(UnsupportedQueryError, match="cross-source aggregation"):
        partition_query(q, _catalog(PG, AR))


def test_aggregation_on_native_leg_kind_is_refused_per_capability():
    q = (
        PREFIX + "SELECT (COUNT(?u) AS ?n) WHERE { "
        "?u a c:UsageMetric ; c:queryVolumeM ?v } GROUP BY ?v"
    )
    with pytest.raises(UnsupportedQueryError, match="kind snowflake"):
        partition_query(q, _catalog(SF))


def test_aggregation_over_unmapped_concept_is_refused():
    q = PREFIX + "SELECT (COUNT(?g) AS ?n) WHERE { ?g a c:Ghost ; c:x ?v } GROUP BY ?v"
    with pytest.raises(UnsupportedQueryError, match="no known source"):
        partition_query(q, _catalog(PG))


def test_genuine_bind_still_refused_even_with_aggregation_present():
    # BIND inside the grouped pattern is user Extend, not aggregation plumbing.
    q = (
        PREFIX + "SELECT ?tier (COUNT(?a) AS ?n) WHERE { "
        "?a a c:Account ; c:currentProductTier ?tier . "
        "BIND(1 AS ?one) } GROUP BY ?tier"
    )
    with pytest.raises(UnsupportedQueryError):
        partition_query(q, _catalog(PG))


def test_plain_bind_without_aggregation_still_refused():
    q = PREFIX + "SELECT ?x WHERE { ?a a c:Account . BIND(1 AS ?x) }"
    with pytest.raises(UnsupportedQueryError, match="BIND"):
        partition_query(q, _catalog(PG))
