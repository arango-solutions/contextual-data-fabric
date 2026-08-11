"""Unified SOTA baseline report tests."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from cdf.eval.sota_scorecard import (
    CommandOutcome,
    _report_hash,
    _text_summary,
    build_scorecard,
    load_scoring_model,
)

ROOT = Path(__file__).resolve().parents[1]
SCORECARD = ROOT / "docs" / "architecture" / "project-sota-scorecard.md"


def test_text_summary_retains_only_failed_test_identifiers() -> None:
    summary = _text_summary(
        "FAILED tests/test_service.py::test_denied - AssertionError: secret-value\n"
        "1 failed, 2 passed\n",
        "",
    )
    assert summary["failed_tests"] == [
        "tests/test_service.py::test_denied",
    ]
    assert "secret-value" not in repr(summary)


def _successful_runner(command, _root, environment):
    assert "SNOWFLAKE_ACCOUNT" not in environment
    joined = " ".join(command)
    if "cdf.eval.nl_eval" in joined:
        stdout = json.dumps(
            {
                "corpus_version": "nl-v1",
                "cases": [{"passed": True}, {"passed": True}],
            }
        )
    elif "cdf.eval.resolution_eval" in joined:
        stdout = json.dumps(
            {
                "corpus_version": "resolution-v1",
                "precision": 1.0,
                "recall": 0.75,
                "abstention_rate": 0.25,
                "cross_scope_violations": 0,
                "evidence_completeness": 1.0,
                "passed": True,
            }
        )
    elif "cdf.eval.optimizer_oracle" in joined:
        stdout = json.dumps(
            {
                "corpus_version": "optimizer-oracle-v1",
                "passed": 8,
                "total": 8,
                "all_passed": True,
                "total_feasible_plans": 182,
                "max_objective_ratio": 1.0,
            }
        )
    elif "cdf.eval.performance_baseline" in joined:
        stdout = json.dumps(
            {
                "workload_version": "synthetic-federation-v1",
                "evidence_class": "internal synthetic",
                "query_source_count": 3,
                "all_passed": True,
                "profiles": [
                    {
                        "profile": "in-process",
                        "source_delay_ms": 0.0,
                        "planning_latency": {"p95_ms": 1.0},
                        "request_latency": {"p50_ms": 2.0, "p95_ms": 3.0},
                        "throughput_qps": 100.0,
                        "bytes_processed": 12_400,
                        "cost_usd": 0.0001,
                        "passed": True,
                    }
                ],
            }
        )
    elif "cdf.eval.ck25_eval" in joined:
        stdout = json.dumps(
            {
                "valid": True,
                "model": "gpt-4o-mini",
                "completed_repetitions": 3,
                "total_case_evaluations": 147,
                "passed": 120,
                "pass_rate": 120 / 147,
                "latency_p50_ms": 500.0,
                "latency_p95_ms": 1_500.0,
                "llm_calls": 160,
                "prompt_tokens": 100_000,
                "completion_tokens": 10_000,
                "cost_usd": 0.021,
                "report_sha256": "abc123",
            }
        )
    else:
        stdout = "20 passed, 2 skipped\n"
    return CommandOutcome(0, stdout, "", 12.3456)


def test_offline_scorecard_is_versioned_hashed_and_secret_free() -> None:
    report = build_scorecard(
        root=ROOT,
        command_runner=_successful_runner,
        generated_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        environment={
            "PATH": "/usr/bin",
            "SNOWFLAKE_ACCOUNT": "secret-account",
            "SNOWFLAKE_PASSWORD": "secret-password",
        },
        git_metadata={"commit": "abc123", "branch": "main", "dirty": False},
        package_versions={"contextual-data-fabric": "0.1.0"},
    )

    assert report["schema_version"] == 2
    assert report["kind"] == "cdf-sota-baseline"
    assert report["mode"] == "offline"
    assert report["passed"] is True
    assert report["scoring"]["score"] == 51.2
    assert sum(
        dimension["weight"] for dimension in report["scoring"]["dimensions"]
    ) == 100
    assert len(report["scoring"]["dimensions"]) == 12
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["live_golden"]["status"] == "not_run"
    assert checks["live_golden"]["required"] is False
    assert checks["nl_decomposition"]["summary"] == {
        "corpus_version": "nl-v1",
        "passed": 2,
        "total": 2,
        "all_passed": True,
    }
    assert checks["resolution_safety"]["summary"]["precision"] == 1.0
    assert checks["http_mcp_parity"]["summary"]["total_cases"] == 20
    assert checks["http_mcp_parity"]["summary"]["all_passed"] is True
    assert checks["unit_contracts"]["command"][0] == "python"
    digest = report.pop("report_sha256")
    assert digest == _report_hash(report)
    assert "secret-account" not in repr(report)
    assert "secret-password" not in repr(report)


def test_scoring_model_rejects_weights_that_do_not_sum_to_100(tmp_path: Path) -> None:
    model = load_scoring_model()
    malformed = {
        "schema_version": model["schema_version"],
        "rubric_version": model["rubric_version"],
        "effective_date": model["effective_date"],
        "dimensions": [
            {
                "id": item["id"],
                "label": item["label"],
                "weight": item["weight"] + (1 if index == 0 else 0),
                "level": item["level"],
            }
            for index, item in enumerate(model["dimensions"])
        ],
    }
    path = tmp_path / "invalid-scorecard.json"
    path.write_text(json.dumps(malformed), encoding="utf-8")

    try:
        load_scoring_model(path)
    except ValueError as exc:
        assert "sum to 100" in str(exc)
    else:
        raise AssertionError("invalid scoring model was accepted")


def test_markdown_dimension_headers_match_scoring_model() -> None:
    pattern = re.compile(
        r"^### \d+\. (?P<label>.+) — (?P<level>\d)/5, weight "
        r"(?P<weight>\d+), contribution (?P<contribution>\d+(?:\.\d+)?)$",
        re.MULTILINE,
    )
    headers = [match.groupdict() for match in pattern.finditer(SCORECARD.read_text())]
    dimensions = load_scoring_model()["dimensions"]

    assert len(headers) == len(dimensions) == 12
    for header, dimension in zip(headers, dimensions, strict=True):
        assert header["label"] == dimension["label"]
        assert int(header["level"]) == dimension["level"]
        assert int(header["weight"]) == dimension["weight"]
        assert float(header["contribution"]) == dimension["contribution"]


def test_live_credentials_are_exposed_only_to_live_golden() -> None:
    seen_live = False

    def environment_runner(command, root, environment):
        nonlocal seen_live
        if command[:2] == ("make", "gate"):
            seen_live = True
            assert environment["SNOWFLAKE_ACCOUNT"] == "live-account"
            return CommandOutcome(0, "15/15 passed\n", "", 1.0)
        assert "SNOWFLAKE_ACCOUNT" not in environment
        return _successful_runner(command, root, environment)

    report = build_scorecard(
        root=ROOT,
        live=True,
        command_runner=environment_runner,
        environment={"PATH": "/usr/bin", "SNOWFLAKE_ACCOUNT": "live-account"},
        git_metadata={"commit": "abc123", "branch": "main", "dirty": False},
        package_versions={},
    )
    assert seen_live is True
    assert report["passed"] is True


def test_required_command_failure_marks_report_failed_without_raw_output() -> None:
    def failing_runner(command, _root, _environment):
        joined = " ".join(command)
        if "mypy" in joined:
            return CommandOutcome(
                1,
                "",
                "connection postgresql://reader:private@db.internal failed",
                2.0,
            )
        return _successful_runner(command, _root, {})

    report = build_scorecard(
        root=ROOT,
        command_runner=failing_runner,
        generated_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        environment={},
        git_metadata={"commit": "abc123", "branch": "main", "dirty": False},
        package_versions={},
    )

    assert report["passed"] is False
    mypy = next(check for check in report["checks"] if check["name"] == "mypy")
    assert mypy["status"] == "failed"
    assert mypy["exit_code"] == 1
    assert "private" not in repr(mypy)
    assert "[REDACTED]" in repr(mypy)


def test_json_parse_error_fails_check_even_when_command_exits_zero() -> None:
    def malformed_runner(command, root, environment):
        if "cdf.eval.nl_eval" in " ".join(command):
            return CommandOutcome(0, "not-json", "", 1.0)
        return _successful_runner(command, root, environment)

    report = build_scorecard(
        root=ROOT,
        command_runner=malformed_runner,
        environment={},
        git_metadata={"commit": "abc123", "branch": "main", "dirty": False},
        package_versions={},
    )

    check = next(item for item in report["checks"] if item["name"] == "nl_decomposition")
    assert check["status"] == "failed"
    assert "parse_error" in check["summary"]
    assert report["passed"] is False


def test_required_json_domain_flag_must_be_true() -> None:
    def invalid_ck25_runner(command, root, environment):
        if "cdf.eval.ck25_eval" in " ".join(command):
            return CommandOutcome(
                0,
                json.dumps({"valid": False, "errors": ["report_sha256"]}),
                "",
                1.0,
            )
        return _successful_runner(command, root, environment)

    report = build_scorecard(
        root=ROOT,
        command_runner=invalid_ck25_runner,
        environment={},
        git_metadata={"commit": "abc123", "branch": "main", "dirty": False},
        package_versions={},
    )

    check = next(
        item for item in report["checks"] if item["name"] == "ck25_evidence_integrity"
    )
    assert check["status"] == "failed"
    assert report["passed"] is False
