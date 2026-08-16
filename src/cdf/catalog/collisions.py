"""Cross-source label-integrity analysis for a merged catalog.

Two producers — r2g per relational source, arango-schema-analyzer per graph —
each run against ONE source with no view of the others, so a label carried by
two entities in *different* sources cannot be detected until the CSI documents
are merged under one manifest. :func:`~cdf.catalog.builder.validate_manifest`
is the first (and only) point in CI where the question "does this catalog say
one word about two things?" is answerable at all.

This module reports three neighbouring smells a curator wants to see together:

- **collisions** — one label carried by more than one entity (excluding the
  structural join keys ``id`` / ``uri`` and the shared key itself). A dropped
  distinction here is what makes a three-source question route to the wrong leg.
- **synonyms** — several labels for one quantity across entities (this catalog
  counts seats four ways: ``seatCount`` / ``seatsSold`` / ``seatsActive`` /
  ``contractedSeatsMirror``).
- **hubs** — a join key most entities carry (``accountId`` is on all ten), so
  any pair of entities is *expressible* to join even when the join is not
  meaningful.

These are **warnings, not failures** (see the CLI's ``--fail-on-label-collisions``
gate): a check that fails the build the day it lands is a check people disable.
Report, count, and let the number come down — drift announces itself rather than
being discovered during a demo.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — import guard, avoids a runtime cycle
    from .model import LoadedCatalog

#: Structural keys every entity carries — never a "collision".
_STRUCTURAL_LABELS = frozenset({"id", "uri"})

#: Generic / unit tokens that don't distinguish a quantity (synonym clustering).
_SYNONYM_STOPWORDS = frozenset(
    {
        "id", "uri", "url", "date", "name", "type", "status", "count", "code",
        "flag", "usd", "ms", "pct", "num", "at", "of", "the", "is", "per", "to",
        "by", "and", "in", "on", "for",
    }
)

#: Split camelCase and letter→digit boundaries: ``avgLatencyMs`` → ``avg latency ms``.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])")


def humanize(name: str) -> str:
    """Property name → human label: ``eventDate`` → ``event date``, ``_uri`` → ``uri``."""
    spaced = _CAMEL_BOUNDARY.sub(" ", name.lstrip("_"))
    return " ".join(spaced.replace("_", " ").split()).lower()


def _content_tokens(label: str) -> set[str]:
    """The distinguishing (non-generic) word stems of a label."""
    out: set[str] = set()
    for word in label.split():
        if word in _SYNONYM_STOPWORDS:
            continue
        stem = word[:-1] if word.endswith("s") and len(word) > 3 else word
        if len(stem) >= 3 and stem not in _SYNONYM_STOPWORDS:
            out.add(stem)
    return out


@dataclass(frozen=True)
class Attribute:
    """One (entity, property) carried by one source — the unit of the analysis."""

    entity: str
    source_id: str
    producer: str
    name: str
    """Raw CSI property name (camelCase)."""
    label: str
    """Humanized label (:func:`humanize`)."""

    @property
    def qualified(self) -> str:
        return f"{self.entity}.{self.name}"


@dataclass(frozen=True)
class LabelCollision:
    """One label carried by ≥2 distinct entities.

    ``accepted`` marks a collision a curator has *intentionally* allowed (e.g. a
    PK/FK pair that legitimately shares a key across two entities). Accepted
    collisions still surface in the report — with their reason — so the decision
    stays visible, but they do not trip the ``--fail-on-label-collisions`` gate.
    """

    label: str
    attributes: tuple[Attribute, ...]
    accepted: bool = False
    accepted_reason: str | None = None

    @property
    def cross_source(self) -> bool:
        return len({a.source_id for a in self.attributes}) > 1


@dataclass(frozen=True)
class SynonymCluster:
    """Several labels for one quantity, sharing a content token across entities."""

    token: str
    attributes: tuple[Attribute, ...]


@dataclass(frozen=True)
class JoinHub:
    """A join key most entities carry — any pair is expressible to join."""

    label: str
    entity_count: int
    total_entities: int


@dataclass(frozen=True)
class LabelReport:
    collisions: tuple[LabelCollision, ...]
    synonyms: tuple[SynonymCluster, ...]
    hubs: tuple[JoinHub, ...]
    total_entities: int

    def is_empty(self) -> bool:
        return not (self.collisions or self.synonyms or self.hubs)

    @property
    def unexpected_collisions(self) -> tuple[LabelCollision, ...]:
        """Collisions that are *not* on the allowlist — what the gate fails on."""
        return tuple(c for c in self.collisions if not c.accepted)

    @property
    def accepted_collisions(self) -> tuple[LabelCollision, ...]:
        """Collisions a curator has intentionally allowed."""
        return tuple(c for c in self.collisions if c.accepted)


def analyze(
    attributes: list[Attribute],
    join_key_labels: frozenset[str],
    allowed: Mapping[str, str] | None = None,
) -> LabelReport:
    """Group labels across the whole merged catalog and report the three smells.

    ``allowed`` maps an intentionally-tolerated collision label to the reason it
    is tolerated; those collisions are marked ``accepted`` (still reported, but
    excluded from the gate). Pure over its inputs (no I/O), so it is
    unit-testable without a manifest.
    """
    allowed = allowed or {}
    entities = {(a.source_id, a.entity) for a in attributes}
    excluded = _STRUCTURAL_LABELS | join_key_labels

    # -- collisions: one label on ≥2 distinct entities (structural keys aside) --
    by_label: dict[str, list[Attribute]] = defaultdict(list)
    for attr in attributes:
        by_label[attr.label].append(attr)
    collisions: list[LabelCollision] = []
    for label, attrs in sorted(by_label.items()):
        if label in excluded:
            continue
        # one representative per distinct entity, stably ordered
        by_entity = {(a.source_id, a.entity): a for a in attrs}
        if len(by_entity) > 1:
            reps = sorted(by_entity.values(), key=lambda a: (a.source_id, a.entity))
            collisions.append(
                LabelCollision(
                    label=label,
                    attributes=tuple(reps),
                    accepted=label in allowed,
                    accepted_reason=allowed.get(label),
                )
            )

    # -- synonyms: a content token in ≥2 distinct labels spanning ≥2 entities --
    by_token: dict[str, dict[str, Attribute]] = defaultdict(dict)
    for attr in attributes:
        if attr.label in excluded:
            continue
        for token in _content_tokens(attr.label):
            by_token[token].setdefault(attr.label, attr)
    synonyms: list[SynonymCluster] = []
    for token, label_map in sorted(by_token.items()):
        if len(label_map) < 3:
            continue  # "several labels for one quantity" — a 2-label morpheme
            # coincidence (eventDate/eventId) is too weak to be worth a warning.
        if len({(a.source_id, a.entity) for a in label_map.values()}) < 2:
            continue  # same-entity naming prefix (survey date/period/year), not a synonym
        cluster = sorted(
            label_map.values(), key=lambda a: (a.label, a.source_id, a.entity)
        )
        synonyms.append(SynonymCluster(token=token, attributes=tuple(cluster)))

    # -- hubs: join-key labels and how many entities carry them --
    hubs: list[JoinHub] = []
    for label in sorted(join_key_labels):
        carriers = {(a.source_id, a.entity) for a in attributes if a.label == label}
        if len(carriers) >= 2:
            hubs.append(
                JoinHub(
                    label=label,
                    entity_count=len(carriers),
                    total_entities=len(entities),
                )
            )

    return LabelReport(
        collisions=tuple(collisions),
        synonyms=tuple(synonyms),
        hubs=tuple(hubs),
        total_entities=len(entities),
    )


def attributes_from_loaded(loaded: LoadedCatalog) -> list[Attribute]:
    """Flatten every (entity, property) across the loaded catalog's CSI documents.

    The CSI documents are materialized in manifest-source order, so each pairs
    with its :class:`~cdf.catalog.model.CatalogSource` for the source id/producer.
    """
    attributes: list[Attribute] = []
    for source, document in zip(
        loaded.manifest.sources, loaded.csi_documents, strict=True
    ):
        conceptual = document["conceptualModel"]
        for entity in conceptual["entities"]:
            for prop in entity.get("properties") or []:
                name = prop["name"]
                attributes.append(
                    Attribute(
                        entity=entity["name"],
                        source_id=source.source_id,
                        producer=source.csi.producer,
                        name=name,
                        label=humanize(name),
                    )
                )
    return attributes


def label_report(
    loaded: LoadedCatalog, allowed: Mapping[str, str] | None = None
) -> LabelReport:
    """Run the cross-source label analysis over a loaded manifest."""
    attributes = attributes_from_loaded(loaded)
    join_key_labels = frozenset(
        humanize(key) for source in loaded.manifest.sources for key in source.join_keys
    )
    return analyze(attributes, join_key_labels, allowed=allowed)


def load_allowlist(path: Path) -> dict[str, str]:
    """Load an intentional-collision allowlist: ``{label: reason}``.

    File shape (JSON)::

        {"allowed": [{"label": "contract id", "reason": "PK/FK by design"}, ...]}

    ``reason`` is optional (defaults to ``""``). Unknown top-level keys, a
    non-list ``allowed``, non-object entries, missing/blank labels, and duplicate
    labels are all rejected so a malformed allowlist fails loudly rather than
    silently tolerating the wrong collisions.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: allowlist must be a JSON object")
    unexpected = set(raw) - {"allowed"}
    if unexpected:
        raise ValueError(
            f"{path}: allowlist has unknown fields: {', '.join(sorted(unexpected))}"
        )
    entries = raw.get("allowed", [])
    if not isinstance(entries, list):
        raise ValueError(f"{path}: allowlist 'allowed' must be an array")
    result: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: allowlist entries must be objects")
        unknown = set(entry) - {"label", "reason"}
        if unknown:
            raise ValueError(
                f"{path}: allowlist entry has unknown fields: {', '.join(sorted(unknown))}"
            )
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"{path}: allowlist entry requires a non-empty 'label'")
        if label in result:
            raise ValueError(f"{path}: duplicate allowlist label {label!r}")
        reason = entry.get("reason", "")
        if not isinstance(reason, str):
            raise ValueError(f"{path}: allowlist reason for {label!r} must be a string")
        result[label] = reason
    return result


def render_report(report: LabelReport) -> str:
    """Human-readable warning block (curator-facing; goes to stderr from the CLI)."""
    if report.is_empty():
        return "catalog label integrity: clean (no collisions, synonyms, or hubs)"
    accepted = len(report.accepted_collisions)
    accepted_note = f" ({accepted} accepted)" if accepted else ""
    lines = [
        f"catalog label integrity — {len(report.collisions)} collision(s){accepted_note}, "
        f"{len(report.synonyms)} synonym cluster(s), {len(report.hubs)} hub(s) "
        f"across {report.total_entities} entities"
    ]
    for collision in report.collisions:
        where = ", ".join(f"{a.entity} ({a.source_id})" for a in collision.attributes)
        tags = ""
        if collision.cross_source:
            tags += "  [cross-source]"
        if collision.accepted:
            reason = f" — {collision.accepted_reason}" if collision.accepted_reason else ""
            tags += f"  [accepted{reason}]"
        lines.append(f'  collision  "{collision.label}"  {where}{tags}')
    for cluster in report.synonyms:
        members = ", ".join(a.qualified for a in cluster.attributes)
        lines.append(f'  synonym    "{cluster.token}"  {members}')
    for hub in report.hubs:
        lines.append(
            f'  hub        "{hub.label}"  carried by '
            f"{hub.entity_count}/{hub.total_entities} entities — any pair is joinable"
        )
    return "\n".join(lines)
