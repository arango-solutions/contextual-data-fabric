# SOP — Standard Operating Procedure for Developers

> New to the project? This is the one document to read end-to-end. It gets you
> from a fresh laptop to a green golden gate and your first reviewed PR.
> Estimated time: ~45 minutes, most of it downloads.

Orientation, in one breath: read [NORTH_STAR.md](NORTH_STAR.md) (the fixed
point), skim the [PRD](docs/contextual-data-fabric-prd.md) §1–§5 (what we're
building now), then set up below.

---

## 1. Development Harness Configuration

We develop with AI coding agents (Claude Code, Cursor, Antigravity) connected
to a **team shared memory** — one shared ArangoDB where verified solutions,
corrections, and per-project drift state are visible to everyone, attributed
per person.

### 1.1 Shared memory + MCP (the canonical guide lives upstream)

Follow **`arango-shared-memory/ONBOARDING.md`**
(https://github.com/ArthurKeen/arango-shared-memory) — it is the maintained,
single-source setup for this and deliberately not duplicated here. The
10-minute path:

1. Clone `arango-solutions-mcp-server` and `arango-shared-memory` under
   `~/code/`; `poetry install` the MCP server.
2. Get **your own shared-cluster credentials from the team lead** (each
   developer gets a scoped account — everything you save/search/apply is
   attributed to your username). Credentials are handed over out-of-band;
   they never appear in a repo, a chat, or a commit.
3. Register the `arangodb-memory-mcp` server in your client(s), pasting the
   JSON block from ONBOARDING.md (with your credentials and your own
   OpenAI key for semantic search):
   - **Claude Code:** under `"mcpServers"` in `~/.claude.json`
   - **Cursor:** under `"mcpServers"` in `~/.cursor/mcp.json`
   - **Antigravity:** same server entry via *Settings → MCP servers → manage
     config* (Antigravity's managed `mcp_config.json`; the JSON shape is the
     same — verify the exact panel name in your version, it moves fast)
4. Restart the client and confirm the memory tools appear.

### 1.2 What shared memory does for you

- **`/pattern-search`** before solving anything non-trivial — a teammate may
  have already solved it, verified, across any of our projects.
- **`/pattern-save`** after solving something reusable — credited to you when
  others apply it.
- **`/prd-sync`** — audits code against the PRD both directions; gaps are
  tracked, and when code legitimately outgrows the spec you get a reviewable
  PRD patch instead of silent divergence.
- **Automatic session digest** — open drift gaps, PRD staleness, relevant
  patterns injected at session start; **automatic capture** queues likely
  lessons at session end for you to triage.
- **A drift stop gate** — edited implementation files without `/prd-sync`?
  Session end is blocked once with instructions.

### 1.3 Harness pieces specific to this repo

- **`AGENTS.md` is the canonical agent instruction file** (`CLAUDE.md` is a
  symlink to it — the convention across all our repos). Read it: it carries
  the repo topology and PRD location your agent must respect.
- **`.claude/rules/`** — ten always-on rules for agents *and humans*. The
  load-bearing ones: *read-before-write* (match the codebase), *test-what-you-
  touch*, *verify-before-done* (if you didn't run it, you didn't ship it),
  *incremental-over-atomic* (small verified slices), *surface-don't-guess*
  (ask on ambiguity), *mock-fidelity* (mocks mirror real signatures),
  *checkpoint-regularly* (commit early, push often).
- **`.claude/skills/`** — project skills your agent can invoke:
  `pre-demo-gate` (mandatory before any demo), `integrate-owned-lib` (CC-9
  pin bumps — its first step is checking BOTH org mirrors),
  `add-source-instance` / `add-source-kind` (onboarding new federated
  sources), `arangodb-visualizer-customizer`, plus the shared-memory trio
  (`pattern-search`, `pattern-save`, `prd-sync`).
- **`gh` CLI, authenticated** — the whole task flow (issues, PRs, checks)
  runs through it.
- **Python 3.11+, Docker Desktop, node** (node only for the demo-editor
  checks).

## 2. Project-specific Configuration (this repo)

### 2.1 Repo topology — where code lives and flows

- **PRIMARY: `arango-solutions/contextual-data-fabric`** — clone it, branch
  on it, open every PR there. Reviews happen there.
- `ArthurKeen/contextual-data-fabric` is a maintainer-synced secondary; you
  never need it.

```bash
cd ~/code && git clone https://github.com/arango-solutions/contextual-data-fabric.git
cd contextual-data-fabric
```

### 2.2 The four test databases run in Docker

Everything local is `make`-driven; credentials for the one cloud source
(Snowflake) live in a git-ignored `.env` you get from the team lead
(**key-pair auth only — there are no passwords**; see CC-7 in the PRD).

```bash
make install    # venv + pinned deps (CC-9 pin for arango-sparql-py included)
make up         # four stacks: ArangoDB :8530, Postgres :5433, Ontop :18090, ClickHouse :8123
make seed       # load the demo corpus into all four + regenerate the reverse CSI
make gate       # THE acceptance bar: 20 live golden cases, all green
make demo       # browser UI at http://127.0.0.1:8099  (IPv4 — not "localhost")
make test       # pytest + ruff + mypy
make down       # stop the stacks (named volumes keep the data)
```

Ports are overridable (`CDF_ARANGO_PORT`, `CDF_POSTGRES_PORT`,
`CDF_ONTOP_PORT`, `CDF_CLICKHOUSE_HTTP_PORT`, `CDF_UI_PORT`) — override via
env, never by editing files; a port-consistency test enforces agreement
between the Makefile, compose files, CI, and the demo server.

Worth knowing early:

- **`make gate` is the definition of "it works".** Run it before demos
  (`pre-demo-gate` skill), after dependency bumps, before claiming a task
  done. A red gate blocks merge, regardless of author.
- `make catalog-integrity` (offline) verifies the catalog manifest rebuilds
  byte-identically; `make catalog-probe` (live) verifies every declared
  source capability against the real engines (CC-14).
- `CDF_SCALE_FACTOR=10 make seed` multiplies the corpus for scale work
  (`make scale-baseline` records evidence). The gate **refuses** to run at
  scale ≠ 1 — reseed at 1× first.
- `make sync-secondary` (maintainers) fast-forwards the ArthurKeen mirror
  after an org merge.

## 3. Project Artifacts — what's where

| Artifact | Where | What it is |
|---|---|---|
| North star | [NORTH_STAR.md](NORTH_STAR.md) → [full doc](docs/contextual-data-fabric-north-star.md) | The fixed end-goal; scope-ambiguity tiebreaker |
| **The PRD (the spec)** | [docs/contextual-data-fabric-prd.md](docs/contextual-data-fabric-prd.md) | **Source of truth.** Requirements carry stable ids (`FR-*`, `CC-*` cross-cutting, `RD-*` readiness gates) that code and commits cite. *Do not point `/prd-sync` at the product-strategy PRD.* |
| Module specs | `docs/architecture/module-01…10/` | One folder per module: `specification.md` (+ `implementation-plan.md`, `adr/`). New modules start from `_TEMPLATE-module-spec.md`; `docs/architecture/README.md` is the index |
| ADRs | `docs/architecture/module-05-…/adr/ADR-0001…0006` | Decision records — read before touching the area they govern |
| Roadmap | [docs/roadmap-2026H2.md](docs/roadmap-2026H2.md) | Eight sprints (S1–S8), three workstreams (WS-A estate hardening, WS-B the Forge, WS-C customer-evaluation readiness). Sprint sections name concrete tasks and exit gates |
| Scorecards | `docs/architecture/project-scorecard.md`, `project-sota-scorecard.md` | "Did we build it?" vs "can we prove it's good?" — evidence-tiered, honest |
| Evidence | `docs/evidence/` | Machine-checked evidence files (CK25, scale baselines) validated in CI |
| Goldens | `deploy/golden/`, `deploy/questions.json` | The gate's 20 live contracts + the prepared-question registry |
| Catalog | `deploy/catalog/manifest.json` | The authoritative, hash-pinned source catalog (ADR-0003) |

## 4. The specification-driven design process

**The specifications are paramount and are always kept current.**

1. **No feature starts before its spec — and the spec change is its own
   PR.** Update the PRD / module spec / ADR in a small, dedicated PR; get it
   reviewed and **merged to main before the implementation branch starts**.
   The requirement gets an id, the design gets recorded, reviewers approve
   the *contract* before any code exists — and the spec on main is always
   the one everyone audits against. Then implement, citing the id in code
   and commits. (ADR-0006 was accepted before one line of Forge code:
   that's the pattern.)
   - *Why merged-first, not same-PR:* the shared memory's drift baseline
     (`prd_sha256`, `drift_alerts`) is **per-project, not per-branch**. A
     PRD edited on a branch plus `/prd-sync` moves the shared baseline to
     unmerged content and feeds phantom gaps into every teammate's session
     digest. Corollary: **run `/prd-sync` only when your branch's PRD is
     identical to main's** — i.e., your spec PR has merged. On a feature
     branch it then audits your code against the agreed contract, which is
     exactly what you want.
   - **How the docs relate** (the traceability chain): North Star → **PRD**
     (the WHAT — requirement ids, cross-cutting CC-*, readiness RD-*) →
     **module specs** in `docs/architecture/module-NN/` (the per-module
     decomposition: scoped FRs, interfaces, acceptance criteria) →
     **implementation plans + ADRs** (the HOW and the decided trade-offs) →
     code citing the ids → **goldens** pinning the behavior. A change
     ripples top-down: touch the PRD when the *requirement* changes, the
     module spec when the *module contract* changes, an ADR when a
     *decision* is made — and reconcile upward in the same PR if a lower
     layer contradicts a higher one.
2. **When an issue surfaces during implementation or testing, interrogate the
   spec before the code.** Classify it first:
   - *Ambiguous spec* → sharpen the spec, then fix.
   - *Incomplete spec* → extend the spec (new requirement id), then fix.
   - *Wrong spec* → correct the spec via a reviewed change, then fix.
   - Only when the spec is unambiguous, complete, and right is it a plain
     code bug.
3. **The machinery enforces this**: `/prd-sync` audits drift in both
   directions and the session gate nags until you run it; the golden gate
   pins behavioral contracts; refusal goldens must be rewritten *in the same
   PR* that changes admission behavior.

## 5. Working on tasks

- **Sprints are 2 weeks** (a ceiling, not a target — decided by team
  consensus 2026-09-08; a sprint ends the moment its exit gate is green).
  The current sprint's tasks live in the roadmap's sprint section plus
  GitHub issues on the primary repo. Pick from there; if it isn't an issue
  yet, file it first — work is issue-driven.
- **One branch per task**, named `feat/…`, `fix/…`, `docs/…`, `ops/…`.
- **Every change lands by PR on `arango-solutions/contextual-data-fabric`**,
  reviewed by another team member. CI (`check` + `live-local`, which stands
  up the Docker stacks and runs the gate) must be green; the gate is the
  merge arbiter.
- **Slice small** (incremental-over-atomic): each commit compiles, tests
  green, describable in one sentence. Commit early, push often.
- **Cross-repo dependencies move by pin, not by HEAD** (CC-9): bump
  `deploy/pins/*`, re-run the goldens in the same PR, and use the
  `integrate-owned-lib` skill — its first step (check both org mirrors)
  exists because a teammate's work once lived 148 commits ahead on the
  mirror.
- **Definition of done**: spec updated → code + tests → `make test` green →
  `make gate` green (if behavior touched) → PR merged → evidence/docs
  updated if you changed what the system can do.

## 6. Getting help

- `docs/architecture/README.md` — the map of everything.
- `/pattern-search` — someone probably hit your problem already.
- The demo UI's *Provenance & Execution* panel — the fastest way to
  understand what the engine actually does with a question.
- Ask in the team channel; better yet, file the issue and link it.
