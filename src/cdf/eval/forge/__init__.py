"""Federation Forge orchestration — CDF's half of ADR-0006 (M15).

r2g owns *generation* (``r2g.forge``: ontology → DDL / loader / rows behind
one dialect seam). This package owns *orchestration*: sampling shape families,
emitting the shape descriptor that is the contract and the oracle (D-1),
partitioning concepts onto systems, computing the expected catalog and the
expected goldens from the pre-partition dataset (D-2), and running them.

Two modes, per D-4:

* **fixture mode** (per-PR, no engines): the generated dataset is projected
  into per-source fixture rows and each golden runs through the real planner,
  executor and grounding via :func:`cdf.eval.golden.run_golden`. The per-system
  CSI documents are synthesised here and stamped ``producer: cdf-forge-fixture``
  so nobody mistakes them for analyzer output — fixture mode tests the fabric's
  partition → execute → ground pipeline, not the estate's introspection.
* **live mode** (nightly): the same descriptor drives real deployments through
  the estate (r2g dialects → RSA/ASA introspection → r2g export-csi/r2rml →
  CDF catalog → gate). Live mode lands in a later slice; the descriptor is
  written so nothing about it has to change.

Determinism is a requirement (D-5): every artifact is a pure function of the
descriptor, and ``suite --check-determinism`` regenerates and byte-compares.
"""

from __future__ import annotations

from cdf.eval.forge.sampler import FAMILIES, Shape, System, sample_shape

__all__ = ["FAMILIES", "Shape", "System", "sample_shape"]
