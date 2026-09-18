"""Shape sampler: seeded shape families → ontology + partition map (ADR-0006 D-2 §2).

A *shape* is a conceptual ontology (the CSI v1 ``conceptualModel`` r2g's forge
accepts) plus a partition map assigning every concept to exactly one system.
Families are the ones the roadmap names: 2–6 legs, hub-heavy, chain joins,
wide/narrow entities. Everything stochastic flows from the seed (D-5).

Naming follows r2g's forge (PLAN F-2) so the forward pipeline re-derives the
same names: entities singular PascalCase, properties lowerCamel, relationship
types ``expected_relationship_type(child, parent)``. The forge adds the ``id``
spine column and one ``<parent>_id`` FK column per relationship itself, so the
sampler never declares those as properties.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from r2g.csi import owl_entity_name
from r2g.forge import column_name, expected_relationship_type, table_name

from cdf.catalog.capabilities import NO_CAPABILITIES, AggregationCapability, SourceCapabilities

#: Dialects r2g's forge seam accepts (Postgres shipped in S1; the other three
#: are the S2 dialect plugins). The kind is the routing id CDF's catalog derives
#: from the CSI provenance; what a system can *execute* is not derived from it —
#: see :func:`_declared_capabilities`.
DIALECT_KIND: dict[str, str] = {
    "postgres": "postgresql",
    "snowflake": "snowflake",
    "clickhouse": "clickhouse",
    "arango": "arango",
}
_SYSTEM_PREFIX = {"postgres": "pg", "snowflake": "sf", "clickhouse": "ch", "arango": "ar"}

#: Singular PascalCase class names (CC-12). Deliberately generic — generated
#: shapes carry no customer-derived vocabulary (D-5 publishability).
ENTITY_VOCABULARY: tuple[str, ...] = (
    "Account",
    "Order",
    "Invoice",
    "Shipment",
    "Product",
    "Warehouse",
    "Device",
    "Session",
    "Ticket",
    "Region",
    "Contract",
    "Payment",
    "Customer",
    "Vendor",
    "Campaign",
    "Asset",
)

#: lowerCamel property names per conceptual JSON type (the four r2g's forge
#: synthesises, PLAN F-3). None may collide with ``id`` or an FK column name.
PROPERTY_VOCABULARY: dict[str, tuple[str, ...]] = {
    "string": (
        "name",
        "status",
        "label",
        "code",
        "segment",
        "category",
        "tier",
        "channel",
        "currency",
        "sku",
        "notes",
        "region",
    ),
    "integer": ("quantity", "score", "priority", "rank", "seats", "retries", "version", "units"),
    "float": ("amount", "total", "ratio", "weight", "latencyMs", "discount", "balance", "margin"),
    "boolean": (
        "active",
        "flagged",
        "verified",
        "archived",
        "enabled",
        "primary",
        "expired",
        "billable",
    ),
}


def _roundtrips(entity: str) -> bool:
    """r2g's forge refuses a class whose table name does not normalise back to
    it (PLAN F-2). Its singularizer is imperfect — ``warehouses`` → ``Warehous``
    — so the sampler asks r2g rather than assuming (r2g hardening list #6)."""
    return owl_entity_name(table_name(entity)) == entity


#: The vocabulary the sampler actually draws from: only names r2g round-trips.
USABLE_ENTITY_VOCABULARY: tuple[str, ...] = tuple(e for e in ENTITY_VOCABULARY if _roundtrips(e))

#: Words no target accepts as an unquoted column name. r2g's dialects emit
#: identifiers unquoted (Snowflake folds them to UPPERCASE, so quoting would
#: change the estate's spelling), so a property whose snake_case column is a
#: reserved word yields DDL Postgres rejects (``primary boolean`` — found the
#: first time a Snowflake-owned entity ran on Postgres under
#: ``--substitute-unavailable``) and Snowflake would reject too. The set is the
#: intersection of "reserved in Postgres, Snowflake or ClickHouse" with words
#: that could plausibly enter the vocabulary; r2g's ``plan_schema`` does not
#: check this (r2g hardening list, filed with PR #8), so the sampler does.
SQL_RESERVED_COLUMN_WORDS: frozenset[str] = frozenset(
    {
        "all",
        "and",
        "any",
        "array",
        "as",
        "asc",
        "between",
        "both",
        "by",
        "case",
        "cast",
        "check",
        "collate",
        "column",
        "constraint",
        "create",
        "cross",
        "current",
        "default",
        "desc",
        "distinct",
        "do",
        "else",
        "end",
        "except",
        "exists",
        "false",
        "fetch",
        "for",
        "foreign",
        "from",
        "full",
        "grant",
        "group",
        "having",
        "in",
        "index",
        "inner",
        "intersect",
        "into",
        "is",
        "join",
        "key",
        "leading",
        "left",
        "like",
        "limit",
        "localtime",
        "minus",
        "natural",
        "not",
        "null",
        "offset",
        "on",
        "only",
        "or",
        "order",
        "outer",
        "primary",
        "qualify",
        "references",
        "right",
        "row",
        "rows",
        "sample",
        "schema",
        "select",
        "some",
        "table",
        "then",
        "to",
        "trailing",
        "true",
        "union",
        "unique",
        "user",
        "using",
        "values",
        "view",
        "when",
        "where",
        "window",
        "with",
    }
)


def _column_is_plain(prop: str) -> bool:
    return column_name(prop).lower() not in SQL_RESERVED_COLUMN_WORDS


#: Property labels the sampler actually draws from: only those whose physical
#: column name every dialect accepts unquoted.
USABLE_PROPERTY_VOCABULARY: dict[str, tuple[str, ...]] = {
    t: tuple(p for p in names if _column_is_plain(p)) for t, names in PROPERTY_VOCABULARY.items()
}

FAMILIES: tuple[str, ...] = ("two_leg", "chain", "hub", "wide_narrow", "six_leg")


@dataclass(frozen=True)
class System:
    """One generated system: a dialect, a stable id such as ``pg1``, and the
    capabilities it *declares* (ADR-0005 D4, manifest format).

    Declared, not derived from the engine kind (PR #34 review, item 3): in
    fixture mode there is no engine to probe, so the descriptor's declaration is
    the only truth, and sampling it independently of ``kind`` means the
    aggregation goldens exercise the registry's override path in both
    directions instead of the legacy per-kind default. In live mode the
    onboarding probe (CC-14) is what ties a declaration to reality.
    """

    name: str
    dialect: str
    capabilities: SourceCapabilities = NO_CAPABILITIES

    @property
    def kind(self) -> str:
        return DIALECT_KIND[self.dialect]

    @property
    def source_id(self) -> str:
        """The id CDF's catalog derives from the CSI provenance: ``<kind>:<ref>``."""
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True)
class Shape:
    """A sampled shape: ontology + partition map, plus the sampling record."""

    name: str
    family: str
    seed: int
    ontology: dict[str, Any]
    """Bare CSI v1 conceptual model: ``{"entities": [...], "relationships": [...]}``."""
    systems: tuple[System, ...]
    owner: dict[str, str] = field(default_factory=dict)
    """concept name → system name (single-owner ownership, D-2)."""

    def system(self, name: str) -> System:
        for s in self.systems:
            if s.name == name:
                return s
        raise KeyError(name)

    def owner_system(self, entity: str) -> System:
        return self.system(self.owner[entity])

    def entities(self) -> list[dict[str, Any]]:
        return list(self.ontology["entities"])

    def relationships(self) -> list[dict[str, Any]]:
        return list(self.ontology.get("relationships", []))

    def entity(self, name: str) -> dict[str, Any]:
        for e in self.entities():
            if e["name"] == name:
                return e
        raise KeyError(name)

    def cross_system_relationships(self) -> list[dict[str, Any]]:
        return [
            r
            for r in self.relationships()
            if self.owner[r["fromEntity"]] != self.owner[r["toEntity"]]
        ]

    def partition_map(self) -> dict[str, dict[str, str]]:
        """The D-1 ``partitionMap`` block: concept → {dialect, system}."""
        return {
            e["name"]: {
                "dialect": self.owner_system(e["name"]).dialect,
                "system": self.owner[e["name"]],
            }
            for e in self.entities()
        }


def _systems(rng: random.Random, count: int, *, distinct_dialects: bool) -> tuple[System, ...]:
    dialects = list(DIALECT_KIND)
    if distinct_dialects and count <= len(dialects):
        chosen = rng.sample(dialects, count)
    else:
        chosen = [rng.choice(dialects) for _ in range(count)]
    counters: dict[str, int] = {}
    out: list[System] = []
    for d in chosen:
        counters[d] = counters.get(d, 0) + 1
        out.append(System(name=f"{_SYSTEM_PREFIX[d]}{counters[d]}", dialect=d))
    return tuple(out)


class _PropertyPool:
    """Draws property labels without replacement across the whole shape: r2g's
    forge refuses a label that appears on two entities (PLAN F-6 — deliberate
    collisions are the denormalizer's job, not a skeleton input)."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._free = [(t, p) for t, names in USABLE_PROPERTY_VOCABULARY.items() for p in names]

    def draw(self, count: int) -> list[dict[str, str]]:
        if count > len(self._free):
            raise ValueError("property vocabulary exhausted; enlarge PROPERTY_VOCABULARY")
        picked = self._rng.sample(self._free, count)
        for item in picked:
            self._free.remove(item)
        # Deterministic order inside the entity: by name, so a re-run is
        # byte-identical regardless of sample order.
        return sorted(({"name": p, "type": t} for t, p in picked), key=lambda x: x["name"])


def _entity_names(rng: random.Random, count: int) -> list[str]:
    return rng.sample(USABLE_ENTITY_VOCABULARY, count)


def _declared_capabilities(rng: random.Random, systems: tuple[System, ...]) -> tuple[System, ...]:
    """Draw each system's declared capabilities — a coin per system, independent
    of its kind, so both admission branches appear on every kind over a suite.
    Drawn last so the entity/property/ownership sampling before it is unchanged
    by this addition (the committed suite diff stays reviewable)."""
    out: list[System] = []
    for system in systems:
        group_by = rng.random() < 0.5
        declared = SourceCapabilities(
            aggregation=AggregationCapability(
                group_by=group_by, having=group_by, count_distinct=group_by
            )
        )
        out.append(System(name=system.name, dialect=system.dialect, capabilities=declared))
    return tuple(out)


def _relationship(child: str, parent: str) -> dict[str, str]:
    return {
        "type": expected_relationship_type(child, parent),
        "fromEntity": child,
        "toEntity": parent,
    }


def _assign_round_robin(
    names: list[str], systems: tuple[System, ...], rng: random.Random
) -> dict[str, str]:
    """Every system owns at least one concept; the rest are spread at random."""
    order = list(names)
    rng.shuffle(order)
    owner: dict[str, str] = {}
    for i, name in enumerate(order):
        if i < len(systems):
            owner[name] = systems[i].name
        else:
            owner[name] = rng.choice(systems).name
    return owner


def sample_shape(seed: int, family: str, *, name: str | None = None) -> Shape:
    """Sample one shape of ``family`` from ``seed`` (deterministic)."""
    if family not in FAMILIES:
        raise ValueError(f"unknown shape family {family!r}; known: {list(FAMILIES)}")
    rng = random.Random(f"{family}:{seed}")
    pool = _PropertyPool(rng)
    shape_name = name or f"{family}-{seed}"

    if family == "two_leg":
        systems = _systems(rng, 2, distinct_dialects=True)
        names = _entity_names(rng, rng.randint(3, 4))
        rels = [_relationship(child, names[0]) for child in names[1:]]
        owner = _assign_round_robin(names, systems, rng)
        # Guarantee a cross-system join: the root and its first child differ.
        if owner[names[0]] == owner[names[1]]:
            owner[names[1]] = next(s.name for s in systems if s.name != owner[names[0]])
        props = {n: pool.draw(rng.randint(2, 4)) for n in names}

    elif family == "chain":
        k = rng.randint(3, 4)
        systems = _systems(rng, k, distinct_dialects=True)
        names = _entity_names(rng, k)
        rels = [_relationship(names[i], names[i - 1]) for i in range(1, k)]
        owner = {n: systems[i].name for i, n in enumerate(names)}
        props = {n: pool.draw(rng.randint(2, 3)) for n in names}

    elif family == "hub":
        systems = _systems(rng, rng.randint(3, 4), distinct_dialects=True)
        n_children = rng.randint(3, 5)
        names = _entity_names(rng, 1 + n_children)
        hub, children = names[0], names[1:]
        rels = [_relationship(c, hub) for c in children]
        owner = _assign_round_robin(children, systems[1:], rng)
        owner[hub] = systems[0].name
        props = {hub: pool.draw(rng.randint(3, 5))}
        props.update({c: pool.draw(rng.randint(1, 3)) for c in children})

    elif family == "wide_narrow":
        systems = _systems(rng, rng.randint(2, 3), distinct_dialects=True)
        n_narrow = rng.randint(2, 3)
        names = _entity_names(rng, 1 + n_narrow)
        wide, narrow = names[0], names[1:]
        rels = [_relationship(n, wide) for n in narrow]
        owner = _assign_round_robin(narrow, systems[1:], rng)
        owner[wide] = systems[0].name
        props = {wide: pool.draw(rng.randint(10, 14))}
        props.update({n: pool.draw(rng.randint(1, 2)) for n in narrow})

    else:  # six_leg
        systems = _systems(rng, 6, distinct_dialects=False)
        names = _entity_names(rng, rng.randint(6, 7))
        rels = []
        for i in range(1, len(names)):
            parents = rng.sample(names[:i], min(i, rng.randint(1, 2)))
            rels.extend(_relationship(names[i], p) for p in parents)
        owner = _assign_round_robin(names, systems, rng)
        props = {n: pool.draw(rng.randint(2, 4)) for n in names}

    entities = [{"name": n, "properties": props[n]} for n in sorted(names)]
    rels_sorted = sorted(rels, key=lambda r: (r["fromEntity"], r["toEntity"]))
    systems = _declared_capabilities(rng, systems)
    return Shape(
        name=shape_name,
        family=family,
        seed=seed,
        ontology={"entities": entities, "relationships": rels_sorted},
        systems=systems,
        owner=dict(sorted(owner.items())),
    )
