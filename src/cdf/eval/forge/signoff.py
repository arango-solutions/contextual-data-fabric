"""Golden sign-off ledger — hand-maintained by the SE lane, never generated.

PR #34 review, item 2: sign-off used to be a ``signedOff: false`` key inside
every generated golden, and :func:`~cdf.eval.forge.suite.emit_shape` removes and
rewrites the whole shape directory, so one ``make forge-suite`` reset every
recorded sign-off. The ledger lives *outside* the generated tree, so
regeneration cannot touch it, and generated files stay a pure function of code
plus seed — which is also what keeps CI's drift check unambiguous.

Ledger format (``deploy/forge/signoff.yaml``)::

    forgeSignoffVersion: 1
    goldens:
      chain-422--join--Asset-Vendor:
        signedOff: true
        signedBy: <person>
        signedOn: "2026-09-20"  # ISO date
        note: optional free text

(The keys are ``signedBy`` / ``signedOn`` rather than ``by`` / ``on`` because
YAML 1.1 — what PyYAML implements — reads a bare ``on`` as the boolean ``true``;
a hand-written ledger would silently lose its dates.)

A golden absent from the ledger is unsigned. A ledger entry naming a golden the
suite no longer emits is reported as *unknown* and fails the suite: a signed-off
golden that vanished is exactly the regeneration drift the SE must see.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

SIGNOFF_VERSION = 1
DEFAULT_SIGNOFF_FILE = Path("deploy/forge/signoff.yaml")


@dataclass(frozen=True)
class SignoffEntry:
    """One hand-recorded sign-off."""

    golden: str
    signed_off: bool
    signed_by: str | None = None
    signed_on: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class SignoffStatus:
    """The ledger joined against the goldens a suite run actually emitted."""

    signed: tuple[str, ...]
    unsigned: tuple[str, ...]
    unknown: tuple[str, ...]
    """Ledger entries naming goldens that were not emitted — a failure."""


def _optional_str(entry: Mapping[str, Any], key: str, path: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, date):  # an unquoted YAML date is fine; normalise to ISO text
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}.{key} must be a non-empty string")
    return value


def parse_signoff(doc: Any, *, source: str = "<signoff>") -> dict[str, SignoffEntry]:
    """Validate a ledger document strictly; return entries keyed by golden name."""
    if not isinstance(doc, dict):
        raise ValueError(f"{source}: ledger must be a mapping")
    if doc.get("forgeSignoffVersion") != SIGNOFF_VERSION:
        raise ValueError(f"{source}: forgeSignoffVersion must be {SIGNOFF_VERSION}")
    unknown_keys = set(doc) - {"forgeSignoffVersion", "goldens"}
    if unknown_keys:
        raise ValueError(f"{source}: unknown top-level keys {sorted(unknown_keys)}")
    goldens = doc.get("goldens")
    if goldens is None:
        goldens = {}
    if not isinstance(goldens, dict):
        raise ValueError(f"{source}: goldens must be a mapping of golden name -> entry")
    out: dict[str, SignoffEntry] = {}
    for name, raw in goldens.items():
        path = f"{source}: goldens[{name!r}]"
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{source}: golden names must be non-empty strings")
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must be a mapping")
        if any(not isinstance(k, str) for k in raw):
            raise ValueError(
                f"{path} has a non-string key {[k for k in raw if not isinstance(k, str)]}: "
                "YAML 1.1 reads bare `on`/`yes`/`no` as booleans — use signedBy / signedOn"
            )
        extra = set(raw) - {"signedOff", "signedBy", "signedOn", "note"}
        if extra:
            raise ValueError(f"{path} has unknown keys {sorted(extra)}")
        signed = raw.get("signedOff")
        if not isinstance(signed, bool):
            raise ValueError(f"{path}.signedOff must be a boolean")
        signed_by = _optional_str(raw, "signedBy", path)
        signed_on = _optional_str(raw, "signedOn", path)
        note = _optional_str(raw, "note", path)
        if signed_on is not None:
            try:
                date.fromisoformat(signed_on)
            except ValueError as exc:
                raise ValueError(f"{path}.signedOn must be an ISO date (YYYY-MM-DD)") from exc
        if signed and (signed_by is None or signed_on is None):
            raise ValueError(
                f"{path}: a sign-off needs both `signedBy` and `signedOn` — who, and when"
            )
        out[name] = SignoffEntry(
            golden=name, signed_off=signed, signed_by=signed_by, signed_on=signed_on, note=note
        )
    return out


def load_signoff(path: Path) -> dict[str, SignoffEntry]:
    """Read and validate the ledger at ``path``."""
    if not path.exists():
        raise FileNotFoundError(f"sign-off ledger not found: {path}")
    return parse_signoff(yaml.safe_load(path.read_text(encoding="utf-8")), source=str(path))


def signoff_status(
    ledger: Mapping[str, SignoffEntry], golden_names: Iterable[str]
) -> SignoffStatus:
    """Join the ledger against the goldens a run emitted."""
    names = set(golden_names)
    signed = tuple(sorted(n for n in names if n in ledger and ledger[n].signed_off))
    unsigned = tuple(sorted(names - set(signed)))
    unknown = tuple(sorted(set(ledger) - names))
    return SignoffStatus(signed=signed, unsigned=unsigned, unknown=unknown)
