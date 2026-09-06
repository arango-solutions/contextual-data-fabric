"""Corpus scale knob v0 — the shared loader seam (roadmap S1 item 3)."""

from __future__ import annotations

import pytest

from cdf.eval.scale import ENV_VAR, MAX_FACTOR, scale_factor_from_env, scale_rows

ROWS = [
    {"account_id": "001A", "_key": "doc-1", "seq": 1, "text": "alpha"},
    {"account_id": "001B", "_key": "doc-2", "seq": 7, "text": "beta"},
]


# ------------------------------------------------------------- env parsing


def test_default_factor_is_one():
    assert scale_factor_from_env({}) == 1


@pytest.mark.parametrize("raw", ["1", "10", "100", str(MAX_FACTOR)])
def test_valid_factors_parse(raw):
    assert scale_factor_from_env({ENV_VAR: raw}) == int(raw)


@pytest.mark.parametrize("raw", ["0", "-3", "1.5", "ten", str(MAX_FACTOR + 1)])
def test_invalid_factors_are_refused_loudly(raw):
    with pytest.raises(ValueError, match=ENV_VAR):
        scale_factor_from_env({ENV_VAR: raw})


# ------------------------------------------------------------ multiplication


def test_factor_one_is_identity():
    assert scale_rows(ROWS, 1, perturb_keys=("_key",)) == ROWS


def test_copy_zero_is_the_verbatim_original_corpus():
    scaled = scale_rows(ROWS, 3, perturb_keys=("_key", "seq"))
    assert scaled[: len(ROWS)] == ROWS  # 1x is always a subset of Nx


def test_spine_is_preserved_and_unique_keys_perturbed():
    scaled = scale_rows(ROWS, 3, perturb_keys=("_key", "seq"))
    assert len(scaled) == 6
    # every copy keeps the join spine verbatim
    assert {r["account_id"] for r in scaled} == {"001A", "001B"}
    # string keys are suffixed per copy; int keys are stride-offset — all unique
    assert len({r["_key"] for r in scaled}) == 6
    assert len({r["seq"] for r in scaled}) == 6
    assert {r["_key"] for r in scaled[2:4]} == {"doc-1-s1", "doc-2-s1"}
    # stride clears the 1x range: max(seq)=7 -> stride 8
    assert {r["seq"] for r in scaled[2:4]} == {9, 15}


def test_determinism_same_factor_same_rows():
    assert scale_rows(ROWS, 5, perturb_keys=("_key",)) == scale_rows(
        ROWS, 5, perturb_keys=("_key",)
    )


def test_unperturbed_duplicates_are_allowed():
    # Tables with a database-side synthetic PK duplicate business rows verbatim.
    scaled = scale_rows(ROWS, 2)
    assert scaled[2:] == ROWS


# ----------------------------------------------------------------- refusals


def test_spine_perturb_overlap_is_a_caller_bug():
    with pytest.raises(ValueError, match="both spine and perturbed"):
        scale_rows(ROWS, 2, spine_keys=("_key",), perturb_keys=("_key",))


def test_boolean_and_exotic_identifier_types_are_refused():
    rows = [{"account_id": "001A", "flag": True}]
    with pytest.raises(ValueError, match="boolean"):
        scale_rows(rows, 2, perturb_keys=("flag",))
    rows = [{"account_id": "001A", "blob": ["x"]}]
    with pytest.raises(ValueError, match="must be str or int"):
        scale_rows(rows, 2, perturb_keys=("blob",))


def test_missing_perturb_values_ride_along_as_none():
    rows = [{"account_id": "001A", "_key": None}]
    scaled = scale_rows(rows, 3, perturb_keys=("_key",))
    assert [r["_key"] for r in scaled] == [None, None, None]
