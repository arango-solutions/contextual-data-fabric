"""The pre-partition dataset (ADR-0006 D-2): synthesised once, by r2g's forge.

Data is generated against the whole ontology before anything is partitioned,
so join-spine values agree across systems by construction. CDF does not
synthesise rows itself — it calls the generator the estate ships
(``r2g.forge.generate``), whose rows are dialect-independent for a given
``(ontology, seed, rows_per_entity)``. The oracle computes expected answers
over these rows, never over any engine's output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from r2g.forge import ForgeOntology, column_name, foreign_key_column, generate, table_name

from cdf.eval.forge.sampler import Shape


@dataclass(frozen=True)
class Dataset:
    """Rows per physical table, exactly as r2g's forge synthesised them."""

    rows_per_entity: int
    seed: int
    tables: dict[str, list[dict[str, Any]]]
    generator: str
    """``r2g-arango <version>`` — recorded in the descriptor for provenance."""

    def rows(self, entity: str) -> list[dict[str, Any]]:
        return list(self.tables[table_name(entity)])


def synthesize(shape: Shape, *, rows_per_entity: int) -> Dataset:
    """Run r2g's generator once for the shape's ontology (Postgres spelling of
    the DDL is irrelevant here; only ``rows`` are used)."""
    ontology = ForgeOntology.from_conceptual(shape.ontology)
    artifacts = generate(
        ontology, dialect="postgres", seed=shape.seed, rows_per_entity=rows_per_entity
    )
    try:
        from importlib.metadata import version

        generator = f"r2g-arango {version('r2g-arango')}"
    except Exception:  # pragma: no cover - metadata missing in odd installs
        generator = "r2g-arango"
    return Dataset(
        rows_per_entity=rows_per_entity,
        seed=shape.seed,
        tables={k: [dict(r) for r in v] for k, v in artifacts.rows.items()},
        generator=generator,
    )


def fk_column(parent: str) -> str:
    """The FK column r2g's forge emits on a child table for ``parent``."""
    return foreign_key_column(parent)


def col(prop: str) -> str:
    """Physical column for a conceptual property (CC-12 inverse)."""
    return column_name(prop)
