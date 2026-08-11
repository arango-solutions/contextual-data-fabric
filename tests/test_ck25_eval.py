"""Metered CK25 repetition and evidence aggregation tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from cdf.eval.ck25_eval import build_evidence, run_repetition, validate_evidence

ROOT = Path(__file__).resolve().parents[1]


class _Client:
    provider = "openai"
    model = "gpt-4o-mini"

    def generate(self, _messages):
        return SimpleNamespace(
            content="SELECT * WHERE {}",
            prompt_tokens=100,
            completion_tokens=10,
            cached_tokens=5,
        )


class _Runner:
    def __init__(self) -> None:
        self._client_for = lambda _config, _case: _Client()

    def run(self, config):
        cases = []
        for index in range(49):
            client = self._client_for({"provider": "openai"}, {"name": index})
            client.generate([])
            cases.append(
                SimpleNamespace(
                    name=f"ck25-{index + 1}",
                    passed=index % 2 == 0,
                    elapsed_ms=float(index + 1),
                    judge_note=None,
                )
            )
        return SimpleNamespace(config=config, cases=cases)


def test_ck25_repetition_records_accuracy_latency_tokens_and_cost(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "cdf.service.metering.estimate_cost_usd",
        lambda _provider, _model, prompt_tokens, completion_tokens: (
            prompt_tokens * 0.00000015 + completion_tokens * 0.0000006
        ),
    )
    runner = _Runner()
    repetition = run_repetition(
        runner,
        config="openai-gpt4o-mini-ck25",
        repetition=1,
    )

    assert repetition.passed == 25
    assert repetition.total == 49
    assert repetition.latency_p50_ms == 25.0
    assert repetition.latency_p95_ms == 47.0
    assert repetition.llm_calls == 49
    assert repetition.prompt_tokens == 4_900
    assert repetition.completion_tokens == 490
    assert repetition.cached_tokens == 245
    assert repetition.cost_usd is not None

    corpus = tmp_path / "corpus.yml"
    corpus.write_text("cases: []\n", encoding="utf-8")
    evidence = build_evidence(
        library_root=tmp_path,
        corpus_path=corpus,
        config="openai-gpt4o-mini-ck25",
        requested_repetitions=1,
        runs=[repetition],
    )
    assert evidence["complete"] is True
    assert evidence["total_case_evaluations"] == 49
    assert evidence["passed"] == 25
    assert evidence["cases"][0]["pass_count"] == 1
    assert len(evidence["report_sha256"]) == 64

    runs = [replace(repetition, repetition=index) for index in range(1, 4)]
    completed = build_evidence(
        library_root=tmp_path,
        corpus_path=corpus,
        config="openai-gpt4o-mini-ck25",
        requested_repetitions=3,
        runs=runs,
    )
    report_path = tmp_path / "evidence.json"
    report_path.write_text(json.dumps(completed), encoding="utf-8")
    validation = validate_evidence(report_path)
    assert validation["valid"] is True
    assert validation["total_case_evaluations"] == 147


def test_checked_in_ck25_evidence_integrity() -> None:
    validation = validate_evidence(
        ROOT / "docs" / "evidence" / "ck25-gpt-4o-mini-3x.json"
    )

    assert validation["valid"] is True
    assert validation["passed"] == 17
    assert validation["total_case_evaluations"] == 147
