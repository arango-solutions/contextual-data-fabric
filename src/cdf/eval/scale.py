"""Corpus scale knob v0 (roadmap S1 item 3; feeds the S4 scale program).

One shared seam for every corpus loader: multiply *dependent* rows by
``CDF_SCALE_FACTOR`` while preserving the ``account_id`` join spine, so a
scaled corpus stresses scans and bind-joins without breaking a single
cross-source join. Two invariants every caller relies on:

- **Copy 0 is the original corpus, byte-identical.** A scaled load is a strict
  superset of the 1x load, so any 1x-true fact stays true (only counts grow).
- **Spine keys are never perturbed; declared unique keys always are** (for
  copies 1..N-1), deterministically — the same factor reproduces the same rows.

The demo's golden gate asserts exact 1x counts, so gating at scale is a
category error: ``deploy/demo/gate.py`` refuses to run when the knob is set.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

ENV_VAR = "CDF_SCALE_FACTOR"
MAX_FACTOR = 10_000  # 100x is the S1 target; leave headroom, refuse absurdity


def scale_factor_from_env(environ: Mapping[str, str] | None = None) -> int:
    """Read and validate the knob (default 1 = the untouched demo corpus)."""
    env = os.environ if environ is None else environ
    raw = env.get(ENV_VAR, "1").strip() or "1"
    try:
        factor = int(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_VAR} must be an integer, got {raw!r}") from exc
    if not 1 <= factor <= MAX_FACTOR:
        raise ValueError(f"{ENV_VAR} must be in [1, {MAX_FACTOR}], got {factor}")
    return factor


def scale_rows(
    rows: Sequence[Mapping[str, Any]],
    factor: int,
    *,
    spine_keys: Sequence[str] = ("account_id",),
    perturb_keys: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Return ``factor`` deterministic copies of ``rows`` (copy 0 verbatim).

    ``spine_keys`` are carried unchanged into every copy (join integrity).
    ``perturb_keys`` name per-row unique identifiers: string values gain a
    ``-s<i>`` suffix on copies 1..N-1; integer values are offset by
    ``i * stride`` where ``stride`` clears the key's 1x value range. A key in
    both lists is a caller bug and refused loudly.
    """
    if factor < 1:
        raise ValueError(f"factor must be >= 1, got {factor}")
    overlap = set(spine_keys) & set(perturb_keys)
    if overlap:
        raise ValueError(f"keys cannot be both spine and perturbed: {sorted(overlap)}")

    strides: dict[str, int] = {}
    for key in perturb_keys:
        values = [r[key] for r in rows if isinstance(r.get(key), int)]
        strides[key] = (max(values) + 1) if values else 0

    out: list[dict[str, Any]] = [dict(r) for r in rows]
    for i in range(1, factor):
        for row in rows:
            copy = dict(row)
            for key in perturb_keys:
                value = copy.get(key)
                if value is None:
                    continue
                if isinstance(value, bool):
                    raise ValueError(f"perturb key {key!r} is a boolean — not an identifier")
                if isinstance(value, int):
                    copy[key] = value + i * strides[key]
                elif isinstance(value, str):
                    copy[key] = f"{value}-s{i}"
                else:
                    raise ValueError(
                        f"perturb key {key!r} must be str or int, got {type(value).__name__}"
                    )
            out.append(copy)
    return out
