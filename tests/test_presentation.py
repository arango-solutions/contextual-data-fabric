"""Presentation directives (issue #17): the two-plane split.

The directive parser is deterministic (closed vocabulary, end-anchored); the
hint rides the envelope BESIDE the bindings; the query plane never sees it.
"""

from __future__ import annotations

import json

from cdf.query.presentation import split_presentation

# -- the deterministic parser --------------------------------------------------


def test_trailing_directive_is_split_and_question_cleaned():
    q, hint = split_presentation(
        "how many accounts are on each product tier? and display as a pie chart"
    )
    assert q == "how many accounts are on each product tier?"
    assert hint == {"requested": "pie", "source": "question"}


def test_phrasings_and_aliases():
    cases = {
        "count issues by source, show it as a bar graph": "bar",
        "count issues by source and plot as a column chart": "bar",
        "usage over time — render it in a line chart.": "line",
        "usage over time and display as a timeseries": "line",
        "list accounts and present the results as a table": "table",
    }
    for question, kind in cases.items():
        cleaned, hint = split_presentation(question)
        assert hint is not None and hint["requested"] == kind, question
        assert "chart" not in cleaned and "display" not in cleaned


def test_by_grouping_is_captured():
    _, hint = split_presentation("count issues and display as a pie chart by source")
    assert hint == {"requested": "pie", "source": "question", "by": "source"}


def test_plain_question_passes_through_untouched():
    q = "at each account's peak usage quarter, how is volume trending?"
    assert split_presentation(q) == (q, None)


def test_mid_sentence_chart_mention_is_not_a_directive():
    q = "which accounts mention the pie chart budget in their documents?"
    assert split_presentation(q) == (q, None)


def test_directive_only_question_passes_through():
    q = "display as a pie chart"
    assert split_presentation(q) == (q, None)  # no query to answer — refuse normally


# -- the envelope carries the hint (service plane) -----------------------------


def _service(tmp_path):
    from cdf.service.app import FederationService

    csi_dir = tmp_path / "csi"
    csi_dir.mkdir()
    (csi_dir / "crm.json").write_text(json.dumps({
        "csiVersion": "1",
        "conceptualModel": {"entities": [
            {"name": "Account",
             "properties": [{"name": "accountId"}, {"name": "currentProductTier"}]}]},
        "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
        "provenance": {"producer": "r2g", "direction": "forward",
                       "source": {"kind": "postgresql", "ref": "crm"}},
    }))
    questions = tmp_path / "questions.json"
    # A plain (non-aggregate) prepared question: presentation is orthogonal to
    # what the query computes — the hint must ride ANY envelope.
    questions.write_text(json.dumps({
        "what product tier is each account on?":
            "PREFIX c: <urn:arango-sparql:concept#> SELECT ?tier "
            "WHERE { ?a a c:Account ; c:currentProductTier ?tier }",
    }))
    return FederationService.from_env({
        "CDF_CSI_DIR": str(csi_dir),
        "CDF_PREPARED_QUESTIONS": str(questions),
        "CDF_NL_DISABLED": "1",
    })


def test_hint_rides_the_envelope_and_registry_matches_cleaned_question(tmp_path):
    service = _service(tmp_path)
    env = service.federate_question(
        "what product tier is each account on? and display as a pie chart"
    )
    # The cleaned question hit the registry (no executor -> failed leg, but the
    # plan resolved — proving the directive never reached the query plane).
    assert env.presentation == {"requested": "pie", "source": "question"}
    assert env.retrieval_path  # the leg was planned from the registry SPARQL


def test_directive_never_rescues_a_refusal(tmp_path):
    service = _service(tmp_path)
    env = service.federate_question("what is the meaning of life? show as a pie chart")
    assert env.status == "refused"
    # The hint still rides (renderers ignore it on refusal), the refusal stands.
    assert env.presentation == {"requested": "pie", "source": "question"}
    assert not env.bindings and not env.citations


def test_no_directive_means_no_presentation_field(tmp_path):
    service = _service(tmp_path)
    env = service.federate_question("what product tier is each account on?")
    assert env.presentation is None


# -- page drift guard -----------------------------------------------------------


def test_demo_page_renders_presentation():
    from pathlib import Path

    page = (Path(__file__).resolve().parents[1] / "deploy/demo/server.py").read_text()
    assert "renderPresentation(" in page
    assert "d.presentation" in page
    assert "rendered bar" in page  # the pie-override honesty note
