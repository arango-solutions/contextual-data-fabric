"""Tests for cross-source label-integrity analysis (cdf.catalog.collisions).

The core (:func:`analyze`) is pure over a list of attributes, so most cases run
without a manifest; one integration case loads the real demo catalog and asserts
the two collisions the spec says can *only* be found after the four CSIs merge.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cdf.catalog.collisions import (
    Attribute,
    analyze,
    humanize,
    load_allowlist,
    render_report,
)

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


def test_real_manifest_cross_source_collisions_are_cleared():
    from cdf.catalog.builder import validate_manifest
    from cdf.catalog.collisions import attributes_from_loaded, label_report

    loaded = validate_manifest(_REPO / "deploy" / "catalog" / "manifest.json", root=_REPO)
    report = label_report(loaded)

    labels = {c.label for c in report.collisions}
    # role -> contactRole (Contact) and event date -> occurredAt (QueryEvent) were
    # renamed at the r2g side, so the two formerly cross-source clashes are gone;
    # only the three intentional Contract/Opportunity collisions remain.
    assert "role" not in labels
    assert "event date" not in labels
    assert labels == {"contract id", "product scope", "renewal date"}
    # The renamed labels now exist, each carried by a single entity (not colliding).
    all_labels = {a.label for a in attributes_from_loaded(loaded)}
    assert "contact role" in all_labels
    assert "occurred at" in all_labels
    # The spec's named synonym + hub are unaffected by the renames.
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


# -- allowlist: accepting intentional collisions -----------------------------


def _pair(label: str) -> list[Attribute]:
    return [_attr("Contract", label), _attr("Opportunity", label)]


def test_allowlisted_collision_is_accepted_and_excluded_from_gate():
    report = analyze(
        _pair("contractId") + _pair("role"),
        frozenset(),
        allowed={"contract id": "PK vs FK by design"},
    )
    by_label = {c.label: c for c in report.collisions}
    assert by_label["contract id"].accepted is True
    assert by_label["contract id"].accepted_reason == "PK vs FK by design"
    assert by_label["role"].accepted is False
    # The gate fails only on the unaccepted collision.
    assert [c.label for c in report.unexpected_collisions] == ["role"]
    assert [c.label for c in report.accepted_collisions] == ["contract id"]


def test_no_allowlist_leaves_every_collision_unexpected():
    report = analyze(_pair("role"), frozenset())
    assert report.unexpected_collisions == report.collisions
    assert report.accepted_collisions == ()


def test_render_marks_accepted_collision_with_reason():
    report = analyze(
        _pair("contractId"),
        frozenset(),
        allowed={"contract id": "shared by design"},
    )
    text = render_report(report)
    assert "1 collision(s) (1 accepted)" in text
    assert "[accepted — shared by design]" in text


def test_load_allowlist_parses_labels_and_reasons(tmp_path: Path):
    path = tmp_path / "allow.json"
    path.write_text(
        json.dumps(
            {
                "allowed": [
                    {"label": "contract id", "reason": "PK/FK by design"},
                    {"label": "renewal date"},  # reason optional
                ]
            }
        ),
        encoding="utf-8",
    )
    allowed = load_allowlist(path)
    assert allowed == {"contract id": "PK/FK by design", "renewal date": ""}


@pytest.mark.parametrize(
    "payload",
    [
        [],  # not an object
        {"allowd": []},  # unknown top-level key (typo)
        {"allowed": {}},  # 'allowed' not a list
        {"allowed": ["contract id"]},  # entry not an object
        {"allowed": [{"reason": "no label"}]},  # missing label
        {"allowed": [{"label": "  "}]},  # blank label
        {"allowed": [{"label": "x", "note": "unknown field"}]},  # unknown entry field
        {"allowed": [{"label": "x"}, {"label": "x"}]},  # duplicate label
    ],
)
def test_load_allowlist_rejects_bad_shape(tmp_path: Path, payload: object):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_allowlist(path)


def test_real_manifest_allowlist_accepts_the_intentional_collisions():
    from cdf.catalog.builder import validate_manifest
    from cdf.catalog.collisions import label_report

    loaded = validate_manifest(_REPO / "deploy" / "catalog" / "manifest.json", root=_REPO)
    allowed = load_allowlist(
        _REPO / "deploy" / "catalog" / "label-collisions-allow.json"
    )
    report = label_report(loaded, allowed=allowed)

    accepted = {c.label for c in report.accepted_collisions}
    unexpected = {c.label for c in report.unexpected_collisions}
    # The three same-source Contract/Opportunity collisions are intentional.
    assert accepted == {"contract id", "product scope", "renewal date"}
    # The two cross-source clashes have been renamed away — nothing else remains,
    # so the catalog is clean apart from the allowlist and the gate passes.
    assert unexpected == set()
