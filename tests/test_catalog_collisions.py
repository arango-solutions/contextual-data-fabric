"""Tests for cross-source label-integrity analysis (cdf.catalog.collisions).

The core (:func:`analyze`) is pure over a list of attributes, so most cases run
without a manifest; one integration case loads the real demo catalog and asserts
the two collisions the spec says can *only* be found after the four CSIs merge.
"""

from __future__ import annotations

from pathlib import Path

from cdf.catalog.collisions import Attribute, analyze, humanize, render_report

_REPO = Path(__file__).resolve().parents[1]


def _attr(entity: str, name: str, source_id: str = "postgresql:crm") -> Attribute:
    return Attribute(
        entity=entity, source_id=source_id, producer="test", name=name, label=humanize(name)
    )


# -- label derivation --------------------------------------------------------


def test_humanize_splits_camel_and_digits_and_strips_underscore():
    assert humanize("eventDate") == "event date"
    assert humanize("avgLatencyMs") == "avg latency ms"
    assert humanize("seatCount") == "seat count"
    assert humanize("_uri") == "uri"
    assert humanize("accountId") == "account id"


# -- collisions --------------------------------------------------------------


def test_label_on_two_entities_is_a_cross_source_collision():
    report = analyze(
        [
            _attr("Contact", "role", "postgresql:crm"),
            _attr("Document", "role", "arango:cmf"),
            _attr("Contact", "email", "postgresql:crm"),
        ],
        frozenset(),
    )
    assert [c.label for c in report.collisions] == ["role"]
    collision = report.collisions[0]
    assert collision.cross_source is True
    assert {a.entity for a in collision.attributes} == {"Contact", "Document"}


def test_label_on_one_entity_is_not_a_collision():
    report = analyze([_attr("Account", "email")], frozenset())
    assert report.collisions == ()


def test_structural_and_join_keys_are_excluded_from_collisions():
    # id / uri / the shared join key are on many entities but are not collisions.
    attrs = [
        _attr("A", "id"),
        _attr("B", "id"),
        _attr("A", "_uri"),
        _attr("B", "_uri"),
        _attr("A", "accountId"),
        _attr("B", "accountId"),
    ]
    report = analyze(attrs, join_key_labels=frozenset({"account id"}))
    assert report.collisions == ()


# -- synonyms ----------------------------------------------------------------


def test_three_labels_sharing_a_stem_across_entities_is_a_synonym():
    report = analyze(
        [
            _attr("Contract", "seatCount", "postgresql:crm"),
            _attr("Account", "seatsSold", "postgresql:crm"),
            _attr("UsageMetric", "seatsActive", "snowflake:telemetry"),
        ],
        frozenset(),
    )
    seat = [s for s in report.synonyms if s.token == "seat"]
    assert len(seat) == 1
    assert len(seat[0].attributes) == 3


def test_two_label_morpheme_coincidence_is_not_a_synonym():
    # eventDate (a date) and eventId (an id) share "event" but are two things.
    report = analyze(
        [
            _attr("Document", "eventDate", "arango:cmf"),
            _attr("QueryEvent", "eventId", "clickhouse:analytics"),
        ],
        frozenset(),
    )
    assert all(s.token != "event" for s in report.synonyms)


def test_same_entity_prefix_is_not_a_synonym():
    report = analyze(
        [
            _attr("NpsSurvey", "surveyDate"),
            _attr("NpsSurvey", "surveyPeriod"),
            _attr("NpsSurvey", "surveyYear"),
        ],
        frozenset(),
    )
    assert all(s.token != "survey" for s in report.synonyms)  # all one entity


# -- hubs --------------------------------------------------------------------


def test_join_key_on_many_entities_is_a_hub():
    attrs = [_attr(f"E{i}", "accountId") for i in range(5)]
    report = analyze(attrs, join_key_labels=frozenset({"account id"}))
    assert len(report.hubs) == 1
    hub = report.hubs[0]
    assert hub.label == "account id"
    assert hub.entity_count == 5 and hub.total_entities == 5


# -- integration: the real demo catalog --------------------------------------


def test_real_manifest_finds_the_cross_source_collisions():
    from cdf.catalog.builder import validate_manifest
    from cdf.catalog.collisions import label_report

    loaded = validate_manifest(_REPO / "deploy" / "catalog" / "manifest.json", root=_REPO)
    report = label_report(loaded)

    by_label = {c.label: c for c in report.collisions}
    # These two exist ONLY across sources (different extractors) — the spec's point.
    assert "role" in by_label and by_label["role"].cross_source
    assert "event date" in by_label and by_label["event date"].cross_source
    # The spec's named synonym + hub.
    assert any(s.token == "seat" and len(s.attributes) == 4 for s in report.synonyms)
    assert any(h.label == "account id" and h.entity_count == 10 for h in report.hubs)


def test_render_report_is_human_readable():
    report = analyze(
        [_attr("Contact", "role", "postgresql:crm"), _attr("Document", "role", "arango:cmf")],
        frozenset(),
    )
    text = render_report(report)
    assert 'collision  "role"' in text
    assert "[cross-source]" in text
