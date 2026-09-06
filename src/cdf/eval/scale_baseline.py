"""Live scale baseline: the S1 scale knob's evidence recorder (S4 feeder).

Runs a fixed, contract-shaped federated workload against the LIVE stack at
whatever ``CDF_SCALE_FACTOR`` the corpus was seeded with, and records
latency + per-leg telemetry to ``docs/evidence/scale-baseline-<N>x.json``.

Honesty statement (mirrors ``performance_baseline``'s): this is an internal,
laptop-class baseline — local Docker sources plus hosted Snowflake — for
tracking how latency moves with corpus scale. It is *disclosed evidence*, not
a public scale claim (SOTA scorecard dimension 10 stays self-assessed).

The workload asserts only ``grounded`` status, never row counts — counts are
exactly what the knob changes. The gate (exact 1x counts) is the correctness
instrument; this module is the trend instrument.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from cdf.eval.scale import scale_factor_from_env

SCHEMA_VERSION = 1
WORKLOAD_VERSION = "live-scale-v1"
PREFIX = "PREFIX c: <urn:arango-sparql:concept#>\n"

#: Contract-shaped queries spanning the leg mix (ids stable across factors so
#: evidence files are comparable). Shapes mirror the golden set's coverage.
WORKLOAD: tuple[tuple[str, str], ...] = (
    (
        "anchor-pg",
        PREFIX + "SELECT ?name ?tier WHERE { ?a a c:Account ; "
        "c:accountName ?name ; c:currentProductTier ?tier }",
    ),
    (
        "join-2leg-pg-arango",
        PREFIX + "SELECT ?name ?source ?url WHERE { "
        "?a a c:Account ; c:accountName ?name ; c:accountId ?id . "
        "?d a c:Document ; c:source ?source ; c:citableUrl ?url ; c:accountId ?id }",
    ),
    (
        "join-3leg-pg-snowflake-arango",
        PREFIX + "SELECT ?name ?period ?volume ?source WHERE { "
        "?a a c:Account ; c:accountName ?name ; c:accountId ?id . "
        "?u a c:UsageMetric ; c:period ?period ; c:queryVolumeM ?volume ; c:accountId ?id . "
        "?d a c:Document ; c:source ?source ; c:accountId ?id }",
    ),
    (
        "filter-clickhouse-arango",
        PREFIX + "SELECT ?feature ?avgLatencyMs ?source WHERE { "
        "?e a c:QueryEvent ; c:feature ?feature ; c:avgLatencyMs ?avgLatencyMs ; "
        "c:accountId ?id . ?d a c:Document ; c:source ?source ; c:accountId ?id . "
        "FILTER(?avgLatencyMs < 25) }",
    ),
)


@dataclass(frozen=True)
class LatencySummary:
    samples: int
    min_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float


def summarize(samples_ms: Sequence[float]) -> LatencySummary:
    """min/p50/p95/max over raw wall-time samples (p95 by nearest-rank)."""
    if not samples_ms:
        raise ValueError("cannot summarize zero samples")
    ordered = sorted(samples_ms)
    rank = max(1, round(0.95 * len(ordered)))  # nearest-rank p95, 1-indexed
    return LatencySummary(
        samples=len(ordered),
        min_ms=round(ordered[0], 3),
        p50_ms=round(median(ordered), 3),
        p95_ms=round(ordered[rank - 1], 3),
        max_ms=round(ordered[-1], 3),
    )


def run_workload(
    federate: Callable[[str], Any],
    repetitions: int,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> list[dict[str, Any]]:
    """Execute each workload query ``repetitions`` times against ``federate``
    (an ``AnswerEnvelope``-returning callable). A non-grounded envelope is a
    hard failure — a baseline over refusals would be a number about nothing.
    """
    if repetitions < 1:
        raise ValueError(f"repetitions must be >= 1, got {repetitions}")
    results: list[dict[str, Any]] = []
    for query_id, sparql in WORKLOAD:
        wall_samples: list[float] = []
        engine_samples: list[float] = []
        rows = 0
        legs: dict[str, dict[str, Any]] = {}
        for _ in range(repetitions):
            start = clock()
            envelope = federate(sparql)
            wall_samples.append((clock() - start) * 1000.0)
            if envelope.status != "grounded":
                raise RuntimeError(
                    f"workload query {query_id!r} came back {envelope.status!r} "
                    f"({envelope.refusal_reason or 'no reason'}) — fix the stack "
                    "before recording a baseline"
                )
            rows = len(envelope.bindings)
            metrics = envelope.execution_metrics
            if metrics is not None:
                engine_samples.append(metrics.total_duration_ms)
                for leg in metrics.legs:
                    legs[leg.source_id] = {
                        "kind": leg.kind,
                        "row_count": leg.row_count,
                        "duration_ms": round(leg.duration_ms, 3),
                        "seed_row_count": leg.seed_row_count,
                    }
        entry: dict[str, Any] = {
            "query_id": query_id,
            "sparql_sha256": hashlib.sha256(sparql.encode()).hexdigest(),
            "result_rows": rows,
            "wall_ms": summarize(wall_samples).__dict__,
            "legs": dict(sorted(legs.items())),
        }
        if engine_samples:
            entry["engine_total_ms"] = summarize(engine_samples).__dict__
        results.append(entry)
    return results


def build_report(
    queries: list[dict[str, Any]],
    *,
    factor: int,
    repetitions: int,
    now: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "workload_version": WORKLOAD_VERSION,
        "disclosure": (
            "internal laptop-class baseline: local Docker sources + hosted "
            "Snowflake; disclosed evidence, not a public scale claim"
        ),
        "generated_at": now or datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "scale_factor": factor,
        "repetitions": repetitions,
        "queries": queries,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cdf-scale-baseline", description=__doc__)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/evidence"))
    args = parser.parse_args(argv)

    from cdf.service import FederationService  # lazy: needs a configured env

    factor = scale_factor_from_env()
    service = FederationService.from_env()
    queries = run_workload(service.federate_sparql, args.repetitions)
    report = build_report(queries, factor=factor, repetitions=args.repetitions)
    output = args.output_dir / f"scale-baseline-{factor}x.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for entry in queries:
        wall = entry["wall_ms"]
        print(
            f"{entry['query_id']}: rows={entry['result_rows']} "
            f"p50={wall['p50_ms']}ms p95={wall['p95_ms']}ms"
        )
    print(f"wrote {output} (factor {factor}x, {args.repetitions} reps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
