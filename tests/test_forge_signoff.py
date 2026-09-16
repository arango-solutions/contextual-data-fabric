"""The Forge sign-off ledger (PR #34 review, item 2) — no r2g needed."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cdf.eval.forge.signoff import (
    DEFAULT_SIGNOFF_FILE,
    SignoffEntry,
    load_signoff,
    parse_signoff,
    signoff_status,
)

ROOT = Path(__file__).resolve().parents[1]


def test_the_committed_ledger_loads_and_validates() -> None:
    ledger = load_signoff(ROOT / DEFAULT_SIGNOFF_FILE)
    assert all(isinstance(e, SignoffEntry) for e in ledger.values())


def test_a_signoff_needs_a_signer_and_a_date() -> None:
    doc = {"forgeSignoffVersion": 1, "goldens": {"s--lookup--A": {"signedOff": True}}}
    with pytest.raises(ValueError, match="both `signedBy` and `signedOn`"):
        parse_signoff(doc)
    doc["goldens"]["s--lookup--A"].update({"signedBy": "se", "signedOn": "not-a-date"})
    with pytest.raises(ValueError, match="ISO date"):
        parse_signoff(doc)
    doc["goldens"]["s--lookup--A"]["signedOn"] = "2026-09-16"
    assert parse_signoff(doc)["s--lookup--A"].signed_off is True


def test_yaml_1_1_boolean_keys_are_refused_by_name() -> None:
    """PyYAML reads a bare ``on:`` as ``True`` — the footgun that made the first
    draft of this ledger silently drop its dates. The loader must name it."""
    doc = yaml.safe_load(
        "forgeSignoffVersion: 1\n"
        "goldens:\n"
        "  s--lookup--A:\n"
        "    signedOff: true\n"
        "    by: se\n"
        "    on: 2026-09-16\n"
    )
    assert True in doc["goldens"]["s--lookup--A"], "the footgun is real"
    with pytest.raises(ValueError, match="signedBy / signedOn"):
        parse_signoff(doc)


def test_unquoted_yaml_dates_are_accepted() -> None:
    doc = yaml.safe_load(
        "forgeSignoffVersion: 1\n"
        "goldens:\n"
        "  s--lookup--A:\n"
        "    signedOff: true\n"
        "    signedBy: se\n"
        "    signedOn: 2026-09-16\n"
    )
    assert parse_signoff(doc)["s--lookup--A"].signed_on == "2026-09-16"


def test_ledger_is_validated_strictly() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        parse_signoff([])
    with pytest.raises(ValueError, match="forgeSignoffVersion"):
        parse_signoff({"forgeSignoffVersion": 2, "goldens": {}})
    with pytest.raises(ValueError, match="unknown top-level keys"):
        parse_signoff({"forgeSignoffVersion": 1, "goldens": {}, "signedOff": True})
    with pytest.raises(ValueError, match="unknown keys"):
        parse_signoff(
            {"forgeSignoffVersion": 1, "goldens": {"g": {"signedOff": False, "who": "x"}}}
        )
    with pytest.raises(ValueError, match="signedOff must be a boolean"):
        parse_signoff({"forgeSignoffVersion": 1, "goldens": {"g": {"signedOff": "yes"}}})


def test_status_joins_ledger_against_emitted_goldens() -> None:
    ledger = parse_signoff(
        {
            "forgeSignoffVersion": 1,
            "goldens": {
                "s--lookup--A": {"signedOff": True, "signedBy": "se", "signedOn": "2026-09-16"},
                "s--lookup--B": {"signedOff": False, "note": "pending a question rewrite"},
                "gone--join--X-Y": {"signedOff": True, "signedBy": "se", "signedOn": "2026-09-16"},
            },
        }
    )
    status = signoff_status(ledger, ["s--lookup--A", "s--lookup--B", "s--lookup--C"])
    assert status.signed == ("s--lookup--A",)
    assert status.unsigned == ("s--lookup--B", "s--lookup--C")
    assert status.unknown == ("gone--join--X-Y",), "a vanished signed-off golden is reported"


def test_missing_ledger_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_signoff(tmp_path / "nope.yaml")
    (tmp_path / "ok.yaml").write_text(
        yaml.safe_dump({"forgeSignoffVersion": 1, "goldens": {}}), encoding="utf-8"
    )
    assert load_signoff(tmp_path / "ok.yaml") == {}
