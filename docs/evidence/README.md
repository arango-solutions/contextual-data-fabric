# Reproducible evidence artifacts

This directory contains checked-in benchmark summaries referenced by the SOTA
scorecard. Reports must include corpus, evaluator, dependency, Git, and
environment provenance; exclude credentials and raw prompts; and carry a
canonical SHA-256 digest. A digest provides integrity checking when anchored by
the tracked repository history; it is not a signature or an independent audit.

The CK25 report derives from the external
[eccenca CK25 dataset](https://github.com/eccenca/ck25-dataset), pinned at
`cb928b2f201e4bdbbde9a1cd0653152779736395` and licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The owned
`arango-sparql-py` sibling retains the full attribution notice, corpus, instance
graph, and execution-based answer-set judge.

`ck25-gpt-4o-mini-3x.json` is the 2026-08-06 GPT-4o-mini zero-shot result:
17/147 correct (11.6%) across three repetitions, with latency, token, call, and
cost evidence. It is a diagnostic baseline, not a passing accuracy claim or a
score promotion. It records `arango-sparql-py@623aa24` with a dirty owned
checkout; `benchmark_paths_dirty: false` establishes that the benchmark files
themselves were clean, but the run must not be described as a clean dependency
reproduction.

The machine-readable dimension levels and weights live separately at
`src/cdf/eval/corpora/sota-dimensions-v1.json`. `cdf-sota` validates and emits
that computed score alongside evidence checks. Its `passed` field means required
checks completed successfully, not that the project crossed a SOTA promotion
gate.
