"""The pre-demo golden gate (WP-P1.6/P1.7): run every live golden case.

PJ's runbook rule, mechanized: **run the eval gate before any demo.** Loads
``deploy/golden/*.json``, wires a :class:`~cdf.service.FederationService` from
the environment (same variables as the service — see ``make demo``), runs each
case against the live stacks, and exits non-zero on any red.

    make gate        # or directly:
    ARANGO_URL=... ONTOP_SPARQL_ENDPOINT=... .venv/bin/python deploy/demo/gate.py
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cdf.eval.golden import filter_goldens, load_goldens, run_golden_live
from cdf.service import FederationService

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
EXPECTED_GOLDEN_TOTAL = 18
EXPECTED_PARTIAL_COUNTS = {
    ("snowflake:telemetry",): (13, 5),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the CDF live golden gate")
    parser.add_argument(
        "--nl",
        action="store_true",
        help="allow the non-deterministic live NL provider path",
    )
    parser.add_argument(
        "--exclude-source",
        action="append",
        default=[],
        help="skip cases expecting this source id; repeatable for partial CI stacks",
    )
    return parser


def validate_golden_inventory(
    all_cases: Sequence[dict[str, Any]],
    selected: Sequence[dict[str, Any]],
    skipped: Sequence[dict[str, Any]],
    excluded_sources: Sequence[str],
) -> None:
    """Prevent an empty, shrunken, or unexpectedly filtered gate from passing."""

    if len(all_cases) != EXPECTED_GOLDEN_TOTAL:
        raise ValueError(
            f"expected {EXPECTED_GOLDEN_TOTAL} live goldens, found {len(all_cases)}"
        )
    if not selected:
        raise ValueError("golden selection is empty")
    exclusions = tuple(sorted(set(excluded_sources)))
    expected = EXPECTED_PARTIAL_COUNTS.get(exclusions)
    if expected is not None and (len(selected), len(skipped)) != expected:
        raise ValueError(
            f"exclusions {exclusions!r} expected {expected[0]} selected and "
            f"{expected[1]} skipped, got {len(selected)} selected and {len(skipped)} skipped"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # The mandatory pre-demo gate is DETERMINISTIC: the NL front-end (WP-D1,
    # LLM-driven) is pinned off so golden outcomes can't drift with a model.
    # NL behavior is covered by the fake-client unit tests (tests/test_nl.py);
    # run `gate.py --nl` explicitly to smoke the live LLM path (non-gating).
    import os

    if not args.nl:
        os.environ["CDF_NL_DISABLED"] = "1"

    all_cases = load_goldens(GOLDEN_DIR)
    cases, skipped = filter_goldens(all_cases, args.exclude_source)
    try:
        validate_golden_inventory(all_cases, cases, skipped, args.exclude_source)
    except ValueError as exc:
        print(f"gate: invalid golden inventory: {exc}")
        return 2

    service = FederationService.from_env()
    if not service.executors:
        print("gate: no executors wired — are the stacks up and env vars set?")
        return 2

    red = 0
    for case in cases:
        outcome = run_golden_live(case, service)
        mark = "\033[32mPASS\033[0m" if outcome.passed else "\033[31mFAIL\033[0m"
        print(f"{mark}  {outcome.name}")
        for m in outcome.mismatches:
            print(f"      - {m}")
            red += 1
    skip_suffix = f", {len(skipped)} excluded" if skipped else ""
    print(
        f"\ngate: {len(cases)} cases{skip_suffix}, "
        f"{'all green' if not red else f'{red} mismatch(es)'}"
    )
    return 0 if not red else 1


if __name__ == "__main__":
    sys.exit(main())
