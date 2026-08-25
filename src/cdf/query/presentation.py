"""Presentation directives in natural-language questions (issue #17).

An utterance like *"count issues by source and display in a pie chart"* fuses
two requests with different truth semantics: a **query** (what is true — goes
through the ontology, the planner, cite-or-refuse) and a **presentation
directive** (how to show it — no truth content, no grounding, no place in the
conceptual IR). This module splits them: the directive is parsed and stripped
**deterministically** (the vocabulary is tiny and closed — no LLM, so the
golden gate stays deterministic and the clause slots naturally into a future
CNL grammar), the cleaned question flows to the registry/NL path unchanged,
and the hint rides the answer envelope as advisory metadata BESIDE the
bindings — never inside them.

The renderer (demo page) enforces the honesty rules: a directive never rescues
a refusal, the hint is validated against the result shape, and an unsuitable
chart is overridden with a stated reason. A model never summarizes numbers
into a chart — every mark is a deterministic rendering of cited bindings.
"""

from __future__ import annotations

import re
from typing import Any

#: The closed directive vocabulary → canonical chart kind. "column" is a
#: common synonym for a vertical bar chart; "timeseries" implies line.
_KIND_ALIASES = {
    "pie": "pie",
    "bar": "bar",
    "column": "bar",
    "line": "line",
    "timeseries": "line",
    "table": "table",
}

#: A trailing presentation clause: optional joiner, a display verb, an optional
#: object, "as/in a", the chart kind, optional chart-noun, an optional
#: "by <column-ish>" grouping, trailing punctuation. Anchored at the END of the
#: question so a mid-sentence mention ("...the pie chart budget...") is never
#: mistaken for a directive.
_CLAUSE = re.compile(
    r"""
    [\s,;]* (?:\band\b\s+)? (?:please\s+)?
    (?:display|show|render|chart|plot|visualize|visualise|draw|present)\s+
    (?:(?:it|them|the\s+results?|results?|this)\s+)?
    (?:as|in)\s+an?\s*
    (?P<kind>pie|bar|column|line|timeseries|table)
    (?:\s*(?:chart|graph|plot|view))?
    (?:\s+by\s+(?P<by>[A-Za-z0-9_ ]+?))?
    \s*[.!?]*\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def split_presentation(question: str) -> tuple[str, dict[str, Any] | None]:
    """Split a question into (cleaned question, presentation hint or None).

    Deterministic and conservative: only a clause at the very END of the
    question is treated as a directive, and a question that is NOTHING BUT a
    directive is passed through untouched (there is no query to answer — the
    normal refusal path handles it honestly).
    """
    match = _CLAUSE.search(question)
    if match is None:
        return question, None
    cleaned = question[: match.start()].rstrip(" \t,;")
    if not cleaned:
        return question, None
    hint: dict[str, Any] = {
        "requested": _KIND_ALIASES[match.group("kind").lower()],
        "source": "question",
    }
    by = match.group("by")
    if by:
        hint["by"] = by.strip().lower()
    return cleaned, hint
