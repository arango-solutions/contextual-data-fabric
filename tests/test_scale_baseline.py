"""Live scale-baseline recorder (S1 scale knob, evidence half)."""

from __future__ import annotations

import pytest

from cdf.eval.scale_baseline import (
    SCHEMA_VERSION,
    WORKLOAD,
    build_report,
    run_workload,
    summarize,
)
from cdf.query.executor import LegExecutionMetrics, PlanExecutionMetrics
from cdf.query.grounding import AnswerEnvelope


def _metrics(duration: float = 12.5) -> PlanExecutionMetrics:
    return PlanExecutionMetrics(
        total_duration_ms=duration,
        partition_duration_ms=1.0,
        execution_duration_ms=duration - 2.0,
        reassembly_duration_ms=1.0,
        leg_duration_sum_ms=duration - 2.0,
        row_count=3,
        legs=(
            LegExecutionMetrics(
                source_id="postgresql:crm",
                kind="postgresql",
                status="ok",
                duration_ms=8.0,
                row_count=3,
            ),
        ),
    )


def _grounded(**overrides) -> AnswerEnvelope:
    defaults = dict(
        status="grounded",
        bindings=({"name": "x"},),
        citations=(),
        retrieval_path=(),
        execution_metrics=_metrics(),
    )
    defaults.update(overrides)
    return AnswerEnvelope(**defaults)


# ------------------------------------------------------------- summarize


def test_summarize_percentiles_nearest_rank():
    s = summarize([10.0, 20.0, 30.0, 40.0, 100.0])
    assert (s.samples, s.min_ms, s.p50_ms, s.max_ms) == (5, 10.0, 30.0, 100.0)
    assert s.p95_ms == 100.0  # nearest-rank at n=5 -> rank 5


def test_summarize_single_sample():
    s = summarize([42.0])
    assert (s.p50_ms, s.p95_ms) == (42.0, 42.0)


def test_summarize_refuses_empty():
    with pytest.raises(ValueError, match="zero samples"):
        summarize([])


# ----------------------------------------------------------- run_workload


def test_run_workload_records_every_query_with_leg_telemetry():
    calls: list[str] = []

    def federate(sparql: str) -> AnswerEnvelope:
        calls.append(sparql)
        return _grounded()

    ticks = iter(range(1000))
    results = run_workload(federate, repetitions=3, clock=lambda: next(ticks) / 1000)
    assert len(results) == len(WORKLOAD)
    assert len(calls) == 3 * len(WORKLOAD)
    entry = results[0]
    assert entry["query_id"] == "anchor-pg"
    assert entry["wall_ms"]["samples"] == 3
    assert entry["engine_total_ms"]["p50_ms"] == 12.5
    assert entry["legs"]["postgresql:crm"]["row_count"] == 3
    assert len(entry["sparql_sha256"]) == 64


def test_run_workload_hard_fails_on_a_non_grounded_answer():
    def federate(sparql: str) -> AnswerEnvelope:
        return _grounded(status="refused", refusal_reason="leg down", bindings=())

    with pytest.raises(RuntimeError, match="anchor-pg.*refused.*leg down"):
        run_workload(federate, repetitions=1)


def test_run_workload_tolerates_missing_engine_metrics():
    results = run_workload(
        lambda _: _grounded(execution_metrics=None), repetitions=2
    )
    assert "engine_total_ms" not in results[0]
    assert results[0]["wall_ms"]["samples"] == 2


def test_run_workload_refuses_zero_repetitions():
    with pytest.raises(ValueError, match="repetitions"):
        run_workload(lambda _: _grounded(), repetitions=0)


# ------------------------------------------------------------ build_report


def test_report_carries_factor_schema_and_disclosure():
    report = build_report(
        run_workload(lambda _: _grounded(), repetitions=1),
        factor=10,
        repetitions=1,
        now="2026-09-06T00:00:00+00:00",
    )
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["scale_factor"] == 10
    assert "not a public scale claim" in report["disclosure"]
    assert len(report["queries"]) == len(WORKLOAD)
