"""Pre-demo/live-CI golden selection contracts."""

from __future__ import annotations

import pytest
from deploy.demo.gate import GOLDEN_DIR, validate_golden_inventory

from cdf.eval.golden import filter_goldens, load_goldens


def test_partial_live_stack_excludes_only_cases_requiring_missing_source() -> None:
    cases = [
        {"name": "local", "expect": {"sources_touched": ["postgresql:crm"]}},
        {
            "name": "cloud",
            "expect": {
                "sources_touched": ["postgresql:crm", "snowflake:telemetry"]
            },
        },
        {"name": "refusal", "expect": {"status": "refused"}},
    ]

    selected, skipped = filter_goldens(cases, ["snowflake:telemetry"])

    assert [case["name"] for case in selected] == ["local", "refusal"]
    assert [case["name"] for case in skipped] == ["cloud"]


def test_checked_in_live_golden_inventory_is_exact() -> None:
    cases = load_goldens(GOLDEN_DIR)
    selected, skipped = filter_goldens(cases, ["snowflake:telemetry"])

    validate_golden_inventory(cases, selected, skipped, ["snowflake:telemetry"])
    assert len(cases) == 20
    assert len(selected) == 15
    assert len(skipped) == 5


def test_empty_golden_selection_is_rejected() -> None:
    cases = load_goldens(GOLDEN_DIR)

    with pytest.raises(ValueError, match="selection is empty"):
        validate_golden_inventory(cases, [], cases, ["unknown:all"])


def test_gate_refuses_to_run_against_a_scaled_corpus(monkeypatch, capsys) -> None:
    # Goldens assert exact 1x counts; the scale knob exists for the baseline
    # program, never the gate. The guard fires before any stack is touched.
    from deploy.demo.gate import main

    monkeypatch.setenv("CDF_SCALE_FACTOR", "10")
    assert main([]) == 2
    out = capsys.readouterr().out
    assert "CDF_SCALE_FACTOR=10" in out
    assert "reseed at 1x" in out
