"""Federated executor over a :class:`PartitionPlan` (M5 / E2).

Runs each per-source sub-query through a pluggable :class:`SourceExecutor`,
inner-joins the per-source result sets on the plan's cross-source **join keys**,
projects the query's variables, and assembles a **retrieval path** for E3/M7 to
cite. The genuinely net-new logic lives here — the join/reassembly, the
retrieval path, and the partial-failure semantics — while *how* a sub-query
becomes SQL/AQL against a live source is the adapter's job (Ontop for the
relational leg, ``arango-sparql-py`` for the AQL leg), injected as a
:class:`SourceExecutor`.

Design choices tied to the M5 spec:

- **No data movement** — each leg runs where its data lives; only result rows
  (bindings) come back and are joined in-engine.
- **Never silent omission** (FR-11) — a failed or unroutable leg is *declared*
  in the result (``partial``, ``failed_sources``, ``unavailable_vars``,
  ``unresolved``), never dropped. The caller (M7) decides partial-answer vs.
  refuse.
- **As-of stamps** (FR-12) — every retrieval step carries the source's as-of
  time when the adapter supplies one.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from time import monotonic, perf_counter
from typing import Any, Protocol

from cdf.auth import RequestContext, RequestMetadata, anonymous_request_context
from cdf.connectors.delegation import (
    BaseSourceIdentity,
    DelegationBroker,
    DelegationError,
    SourceAuthMode,
    SourceExecutionContext,
)
from cdf.connectors.redaction import scrub_exception

# Deep imports from the governance LEAF modules (contracts/runtime), not the
# package facade: cdf.governance/__init__ itself imports cdf.query (via
# composition), so a facade import here re-enters a partially initialized
# package whenever cdf.governance is imported first (issue #20).
from cdf.governance.contracts import (
    AuthorizationEvent,
    AuthorizationFailure,
    AuthorizationRefusal,
    PlanAuthorization,
)
from cdf.governance.runtime import (
    authorization_events_for_source,
    verify_authorized_rows,
)
from cdf.resolution import (
    EntityResolver,
    PlanResolutionRuntime,
    ResolutionEvent,
    ResolutionLegMetrics,
    ResolutionPlanMetrics,
    ResolutionRefusal,
    ResolutionRowsResult,
    ResolutionShortfall,
    RuntimeResolutionBinding,
    rollup_resolution_metrics,
)

from .admission import AdmissionRefusal, PlanAdmissionPolicy, runtime_refusal
from .assembly import (
    AssemblyExecution,
    AssemblyFailure,
    AssemblyMetrics,
    AssemblyRefusal,
    virtual_metrics,
)
from .optimizer import PlanEstimate
from .types import PartitionPlan, SubQuery

# A single result row: SPARQL variable name (bare, no "?") -> value.
Binding = dict[str, Any]


@dataclass(frozen=True)
class SourceResult:
    """What a :class:`SourceExecutor` returns for one sub-query."""

    rows: tuple[Binding, ...]
    native_query: str | None = None
    """The actual SQL/AQL the adapter ran (for the retrieval path / citations)."""
    as_of: str | None = None
    """Source freshness stamp (execution time for live legs; last-ingest time
    for the Arango graph). Recorded per FR-12."""
    source_objects: tuple[str, ...] = ()
    """The physical objects the leg touched (e.g. ``"public.orders"``, a
    collection or document id) — the adapter knows them; E3/M7 cite them
    (FR-2)."""
    bytes_processed: int | None = None
    """Bytes read/scanned when the source reports them; otherwise ``None``."""
    cost_usd: float | None = None
    """Source-reported execution cost. ``None`` means unavailable, never free."""
    retry_count: int = 0
    """Retries performed inside the source adapter, when known."""
    truncated: bool = False
    """Whether the source adapter deliberately truncated its returned rows."""


class SourceExecutor(Protocol):
    """Runs one sub-query against a single source and returns its bindings.

    Concrete adapters: an Ontop-backed executor (SPARQL→SQL) for a relational
    source, an ``arango-sparql-py``-backed executor (SPARQL→AQL) for the graph.
    Tests supply in-memory fakes.
    """

    def execute(self, subquery: SubQuery) -> SourceResult: ...


class ContextAwareSourceExecutor(Protocol):
    """Optional executor extension for explicit request/source authority."""

    def execute_with_context(
        self,
        subquery: SubQuery,
        context: SourceExecutionContext,
    ) -> SourceResult: ...


@dataclass(frozen=True)
class RetrievalStep:
    """One leg of the retrieval path — what ran where, and whether it succeeded."""

    source_id: str
    kind: str
    sparql: str
    status: str  # "ok" | "failed"
    row_count: int = 0
    native_query: str | None = None
    as_of: str | None = None
    source_objects: tuple[str, ...] = ()
    error: str | None = None
    seeded_vars: tuple[str, ...] = ()
    """Join variables bind-joined into this leg (FR-13): the prior legs'
    distinct key rows were pushed down as a trailing ``VALUES`` clause, visible
    in :attr:`sparql`. Empty when the leg ran unseeded."""
    seed_strategy: str = "none"
    seed_batch_count: int = 0
    resolution_events: tuple[ResolutionEvent, ...] = ()
    resolution_metrics: ResolutionLegMetrics | None = None
    authorization_events: tuple[AuthorizationEvent, ...] = ()


@dataclass(frozen=True)
class LegExecutionMetrics:
    """Additive execution telemetry for one attempted source leg (P2 / G2)."""

    source_id: str
    kind: str
    status: str  # "ok" | "failed"
    duration_ms: float
    row_count: int
    bytes_processed: int | None = None
    cost_usd: float | None = None
    retry_count: int = 0
    seed_row_count: int = 0
    seed_cap: int = 0
    seed_cap_exceeded: bool = False
    seed_strategy: str = "none"
    seed_batch_count: int = 0
    seed_overflow: bool = False
    max_seed_rows: int = 0
    truncated: bool = False
    resolution_metrics: ResolutionLegMetrics | None = None
    authorization_events: tuple[AuthorizationEvent, ...] = ()


@dataclass(frozen=True)
class PlanExecutionMetrics:
    """Wall-clock and aggregate source telemetry for one partition plan.

    ``total_duration_ms`` is elapsed wall time, while ``leg_duration_sum_ms``
    is the sum of independently measured leg durations. The latter can exceed
    wall time when legs overlap in a concurrent stage. Partition timing is
    ``None`` when :func:`execute_plan` is called directly because partitioning
    happens outside this executor; the service fills it in.
    """

    total_duration_ms: float
    partition_duration_ms: float | None
    execution_duration_ms: float
    reassembly_duration_ms: float
    leg_duration_sum_ms: float
    row_count: int
    legs: tuple[LegExecutionMetrics, ...]
    bytes_processed: int | None = None
    cost_usd: float | None = None
    retry_count: int = 0
    seed_cap: int = 0
    seed_cap_exceeded: bool = False
    truncated: bool = False
    strategy: str = "legacy-no-statistics"
    seed_batch_count: int = 0
    seed_overflow: bool = False
    max_seed_rows: int = 0
    plan_estimate: PlanEstimate | None = None
    admission_refusal: AdmissionRefusal | None = None
    resolution_metrics: ResolutionPlanMetrics = ResolutionPlanMetrics()
    assembly_metrics: AssemblyMetrics = AssemblyMetrics(
        mode="virtual", cleanup_status="not_applicable"
    )
    request_metadata: RequestMetadata | None = None
    authorization_events: tuple[AuthorizationEvent, ...] = ()


@dataclass(frozen=True)
class FederatedResult:
    """The assembled answer plus the retrieval path that produced it."""

    bindings: tuple[Binding, ...]
    retrieval_path: tuple[RetrievalStep, ...]
    partial: bool = False
    failed_sources: tuple[str, ...] = ()
    unavailable_vars: tuple[str, ...] = ()
    """Projected variables that no *successful* leg could supply."""
    unresolved: tuple[Any, ...] = ()
    """Triples the plan could not route to any source (carried through from the
    :class:`PartitionPlan`)."""
    execution_metrics: PlanExecutionMetrics | None = None
    """Per-plan and per-source execution telemetry. Present for executed plans."""
    plan_estimate: PlanEstimate | None = None
    admission_refusal: AdmissionRefusal | None = None
    resolution_events: tuple[ResolutionEvent, ...] = ()
    resolution_shortfalls: tuple[ResolutionShortfall, ...] = ()
    resolution_metrics: ResolutionPlanMetrics = ResolutionPlanMetrics()
    resolution_refusal: ResolutionRefusal | None = None
    assembly_metrics: AssemblyMetrics = AssemblyMetrics(
        mode="virtual", cleanup_status="not_applicable"
    )
    assembly_refusal: AssemblyRefusal | None = None
    request_metadata: RequestMetadata | None = None
    authorization: PlanAuthorization | None = None
    authorization_events: tuple[AuthorizationEvent, ...] = ()
    authorization_refusal: AuthorizationRefusal | None = None
    withheld_sources: tuple[str, ...] = ()


def _bare(var: str) -> str:
    return var[1:] if var.startswith("?") else var


def _inner_join(left: list[Binding], right: list[Binding]) -> list[Binding]:
    """Inner-join two binding lists on their shared variables (SPARQL BGP
    conjunction semantics). No shared variables → cartesian product.

    Shared variables are read with ``dict.get``: an OPTIONAL leg returns
    heterogeneous rows (some rows lack the optional column), and while the
    planner's well-designedness guard keeps optional columns *out* of the shared
    join keys, the defensive ``.get`` means a missing key degrades to ``None``
    instead of raising."""
    if not left or not right:
        return []
    shared = [k for k in left[0] if k in right[0]]
    index: dict[tuple, list[Binding]] = {}
    for rb in right:
        index.setdefault(tuple(rb.get(k) for k in shared), []).append(rb)
    out: list[Binding] = []
    for ra in left:
        for rb in index.get(tuple(ra.get(k) for k in shared), []):
            out.append({**rb, **ra})
    return out


def _inner_join_with_inputs(
    left: list[Binding],
    right: list[Binding],
    left_ids: list[tuple[str, ...]],
    right_ids: list[str],
) -> tuple[list[Binding], list[tuple[str, ...]]]:
    """Run the proven deterministic join while retaining direct row lineage."""
    if not left or not right:
        return [], []
    shared = [key for key in left[0] if key in right[0]]
    index: dict[tuple[Any, ...], list[tuple[Binding, str]]] = {}
    for row, row_id in zip(right, right_ids, strict=True):
        index.setdefault(tuple(row.get(key) for key in shared), []).append((row, row_id))
    joined: list[Binding] = []
    inputs: list[tuple[str, ...]] = []
    for left_row, lineage_ids in zip(left, left_ids, strict=True):
        for right_row, right_id in index.get(
            tuple(left_row.get(key) for key in shared), []
        ):
            joined.append({**right_row, **left_row})
            inputs.append((*lineage_ids, right_id))
    return joined, inputs


def _sparql_term(value: Any) -> str:
    """Serialize a Python value as a SPARQL VALUES term (plain-literal default)."""
    if value is None:
        return "UNDEF"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _with_values(sparql: str, variables: list[str], rows: list[Binding]) -> str:
    """Append a trailing ``VALUES`` clause (SPARQL 1.1 inline data) to a SELECT.

    This is the bind-join carrier (CC-11 / FR-13): the earlier leg's join-key
    rows constrain the later leg *inside its own engine* — arango-sparql-py and
    Ontop both accept the trailing-VALUES form — so the seeded leg never
    over-fetches. The seeded SPARQL is what gets executed and therefore what
    gets *cited*, keeping the pushdown visible in the retrieval path.
    """
    var_list = " ".join(f"?{v}" for v in variables)
    row_terms = " ".join(
        "(" + " ".join(_sparql_term(r.get(v)) for v in variables) + ")" for r in rows
    )
    return f"{sparql.rstrip()}\nVALUES ({var_list}) {{ {row_terms} }}"


def _distinct_rows(rows: list[Binding], variables: list[str]) -> list[Binding]:
    seen: set[tuple[Any, ...]] = set()
    out: list[Binding] = []
    for r in rows:
        key = tuple(r.get(v) for v in variables)
        if key not in seen and any(k is not None for k in key):
            seen.add(key)
            out.append({v: r.get(v) for v in variables})
    return out


#: Default number of distinct bind keys per deterministic ``VALUES`` batch.
#: The separate admission policy hard-cap defaults to 10,000 total seed rows.
SEED_CAP = 1000


def _deduplicate_bindings(rows: list[Binding]) -> list[Binding]:
    """Stable de-duplication used when deterministic seed batches are merged."""
    seen: set[tuple[tuple[str, str], ...]] = set()
    out: list[Binding] = []
    for row in rows:
        key = tuple(sorted((name, repr(value)) for name, value in row.items()))
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def execute_plan(
    plan: PartitionPlan,
    executors: Mapping[str, SourceExecutor],
    *,
    seed_cap: int = SEED_CAP,
    max_workers: int = 8,
    strategy: PlanEstimate | None = None,
    admission_policy: PlanAdmissionPolicy | None = None,
    allow_partial_limits: bool = False,
    assembly: AssemblyExecution | None = None,
    entity_resolver: EntityResolver | None = None,
    resolution_bindings: Mapping[str, RuntimeResolutionBinding] | None = None,
    request_context: RequestContext | None = None,
    source_auth_modes: Mapping[str, SourceAuthMode] | None = None,
    delegation_broker: DelegationBroker | None = None,
    source_base_identities: Mapping[str, BaseSourceIdentity] | None = None,
    plan_authorization: PlanAuthorization | None = None,
) -> FederatedResult:
    """Execute a partition plan and reassemble one federated result.

    When ``strategy`` is supplied, legs run in its deterministic cost-based
    stages. Direct callers that omit it retain the backward-compatible P1
    relational-then-Arango order. Later stages are **bind-joined** using
    distinct accumulated keys in deterministic ``VALUES`` batches.

    Within a stage, independent legs run **concurrently** on a thread pool —
    legs are I/O-bound network calls, so the GIL is released and threads
    genuinely overlap; wall clock per stage ≈ the slowest leg, not the sum.
    Retrieval-path order follows the chosen physical plan and remains stable
    regardless of completion order. A failed leg or batch is declared (never
    hidden), and a seed set above ``max_seed_rows`` refuses rather than ever
    executing the target unseeded.

    Args:
        plan: the :class:`PartitionPlan` from :func:`cdf.query.partition_query`.
        executors: source_id → :class:`SourceExecutor`. A source with no
            executor is treated as a failed (declared) leg.
        seed_cap: backward-compatible alias for the ``VALUES`` batch size.
        max_workers: upper bound on concurrent legs within a stage.

    Returns:
        A :class:`FederatedResult`. When every leg succeeds and the plan is
        fully resolved, ``partial`` is ``False`` and ``bindings`` is the joined,
        projected answer; otherwise the shortfalls are declared, never hidden.
    """
    context = request_context or anonymous_request_context()
    plan_started = perf_counter()
    policy = admission_policy or PlanAdmissionPolicy(
        seed_batch_rows=seed_cap,
        max_seed_rows=max(seed_cap, 10_000),
    )
    if seed_cap != SEED_CAP and admission_policy is not None:
        policy = replace(policy, seed_batch_rows=seed_cap)
    steps: list[RetrievalStep] = []
    leg_metrics: list[LegExecutionMetrics] = []
    successful: list[tuple[SubQuery, SourceResult]] = []
    failed: list[str] = []
    join_vars = {_bare(v) for v in plan.join_keys}
    accumulated: list[Binding] = []
    accumulated_row_ids: list[tuple[str, ...]] = []
    execution_duration_ms = 0.0
    reassembly_duration_ms = 0.0
    assembly_refusal: AssemblyRefusal | None = None
    resolution_refusal: ResolutionRefusal | None = None
    resolution_events: list[ResolutionEvent] = []
    resolution_shortfalls: list[ResolutionShortfall] = []
    resolution_leg_metrics: list[ResolutionLegMetrics] = []
    authorization_refusal: AuthorizationRefusal | None = None
    authorization_events: list[AuthorizationEvent] = []
    configured_resolution = {
        source_id: binding
        for source_id, binding in (resolution_bindings or {}).items()
        if binding.mode == "canonical_hub"
    }
    auth_modes = source_auth_modes or {}
    base_identities = source_base_identities or {}

    by_source = {sq.source.source_id: sq for sq in plan.sub_queries}
    if strategy is not None:
        stages = [[by_source[source_id] for source_id in stage] for stage in strategy.stages]
    else:
        # Backward-compatible no-statistics behavior.
        ordered = sorted(plan.sub_queries, key=lambda s: s.source.kind == "arango")
        stages = [
            [sq for sq in ordered if sq.source.kind != "arango"],
            [sq for sq in ordered if sq.source.kind == "arango"],
        ]
    if configured_resolution:
        # A leg whose native key still needs normalization must never receive a
        # canonical VALUES seed. Pull every such leg into the initial unseeded
        # stage; only their normalized outputs can seed later canonical legs.
        resolution_ids = set(configured_resolution)
        first = list(stages[0]) if stages else []
        first_ids = {item.source.source_id for item in first}
        first.extend(
            item
            for stage in stages[1:]
            for item in stage
            if item.source.source_id in resolution_ids
            and item.source.source_id not in first_ids
        )
        stages = [
            first,
            *[
                [
                    item
                    for item in stage
                    if item.source.source_id not in resolution_ids
                ]
                for stage in stages[1:]
            ],
        ]
    deadline_at = monotonic() + policy.resolution_deadline_ms / 1000
    if policy.runtime_wall_time_ms is not None:
        deadline_at = min(
            deadline_at,
            monotonic() + policy.runtime_wall_time_ms / 1000,
        )
    resolution_runtime = (
        PlanResolutionRuntime(
            entity_resolver,
            max_calls=policy.max_resolution_calls,
            batch_size=policy.resolution_batch_size,
            deadline_at=deadline_at,
            source_order=tuple(
                item.source.source_id
                for stage in stages
                for item in stage
                if item.source.source_id in configured_resolution
            ),
        )
        if configured_resolution and entity_resolver is not None
        else None
    )

    def _skip_resolution(source_id: str) -> None:
        if resolution_runtime is not None and source_id in configured_resolution:
            resolution_runtime.skip(source_id)

    def _run_leg(
        prepared: tuple[
            SubQuery,
            tuple[str, ...],
            int,
            bool,
            tuple[SubQuery, ...],
            str,
        ],
    ) -> tuple[
        SubQuery,
        RetrievalStep,
        SourceResult | None,
        LegExecutionMetrics,
        AdmissionRefusal | None,
        ResolutionRowsResult | None,
        AuthorizationRefusal | None,
    ]:
        original_sq, seeded_vars, seed_row_count, seed_cap_exceeded, batches, seed_strategy = (
            prepared
        )
        sq = batches[0] if batches else original_sq
        leg_started = perf_counter()
        executor = executors.get(original_sq.source.source_id)
        if executor is None:
            step = RetrievalStep(
                source_id=sq.source.source_id,
                kind=sq.source.kind,
                sparql=sq.sparql,
                status="failed",
                error="no executor registered for source",
            )
            metrics = LegExecutionMetrics(
                source_id=sq.source.source_id,
                kind=sq.source.kind,
                status="failed",
                duration_ms=(perf_counter() - leg_started) * 1000,
                row_count=0,
                seed_row_count=seed_row_count,
                seed_cap=policy.seed_batch_rows,
                seed_cap_exceeded=seed_cap_exceeded,
                seed_strategy=seed_strategy,
                seed_batch_count=len(batches),
                seed_overflow=seed_row_count > policy.max_seed_rows,
                max_seed_rows=policy.max_seed_rows,
            )
            _skip_resolution(original_sq.source.source_id)
            return original_sq, step, None, metrics, None, None, None

        if seed_row_count > policy.max_seed_rows:
            refusal = runtime_refusal(
                "max_seed_rows_exceeded",
                "seed_rows",
                seed_row_count,
                policy.max_seed_rows,
            )
            step = RetrievalStep(
                source_id=original_sq.source.source_id,
                kind=original_sq.source.kind,
                sparql=original_sq.sparql,
                status="failed",
                error=refusal.message,
                seeded_vars=seeded_vars,
                seed_strategy="refused-overflow",
            )
            metrics = LegExecutionMetrics(
                source_id=original_sq.source.source_id,
                kind=original_sq.source.kind,
                status="failed",
                duration_ms=(perf_counter() - leg_started) * 1000,
                row_count=0,
                seed_row_count=seed_row_count,
                seed_cap=policy.seed_batch_rows,
                seed_cap_exceeded=True,
                seed_strategy="refused-overflow",
                seed_batch_count=0,
                seed_overflow=True,
                max_seed_rows=policy.max_seed_rows,
            )
            _skip_resolution(original_sq.source.source_id)
            return original_sq, step, None, metrics, refusal, None, None

        results: list[SourceResult] = []
        failed_error: str | None = None
        batch_refusal: AdmissionRefusal | None = None
        source_id = original_sq.source.source_id
        raw_mode = auth_modes.get(source_id, "service")
        source_context: SourceExecutionContext | None = None
        delegated_secret: str | None = None
        try:
            if raw_mode not in ("service", "delegated"):
                raise DelegationError(f"source {source_id!r} has an invalid auth mode")
            mode: SourceAuthMode = raw_mode
            if context.expired:
                raise DelegationError("request deadline expired before source execution")
            identity = None
            if mode == "delegated":
                if delegation_broker is None:
                    raise DelegationError(
                        f"source {source_id!r} requires a delegation broker"
                    )
                base_identity = base_identities.get(source_id)
                if base_identity is None:
                    raise DelegationError(
                        f"source {source_id!r} requires a base identity"
                    )
                if base_identity.source_id != source_id:
                    raise DelegationError(
                        f"source {source_id!r} has a mismatched base identity"
                    )
                contextual_execute = getattr(executor, "execute_with_context", None)
                if not callable(contextual_execute):
                    raise DelegationError(
                        f"source {source_id!r} does not support delegated identity"
                    )
                supports_context = getattr(
                    executor,
                    "supports_execution_context",
                    None,
                )
                if callable(supports_context) and not supports_context():
                    raise DelegationError(
                        f"source {source_id!r} does not support delegated identity"
                    )
                identity = delegation_broker.exchange(
                    context.principal,
                    source_id,
                    base_identity,
                    deadline=context.deadline,
                )
                if identity.source_id != source_id:
                    raise DelegationError(
                        "delegation broker returned an identity for another source"
                    )
                if identity.expires_at <= datetime.now(timezone.utc):
                    raise DelegationError("delegation broker returned an expired identity")
                delegated_secret = identity.material.reveal()
            source_context = SourceExecutionContext(
                request=context,
                source_id=source_id,
                auth_mode=mode,
                identity=identity,
            )
        except Exception as exc:  # noqa: BLE001
            failed_error = scrub_exception(exc)

        for batch in batches if failed_error is None else ():
            if context.expired:
                failed_error = "request deadline expired during source execution"
                break
            if (
                policy.runtime_wall_time_ms is not None
                and (perf_counter() - plan_started) * 1000 > policy.runtime_wall_time_ms
            ):
                batch_refusal = runtime_refusal(
                    "runtime_wall_time_exceeded",
                    "runtime_wall_time_ms",
                    (perf_counter() - plan_started) * 1000,
                    policy.runtime_wall_time_ms,
                )
                failed_error = batch_refusal.message
                break
            try:
                assert source_context is not None
                contextual_execute = getattr(executor, "execute_with_context", None)
                if callable(contextual_execute):
                    results.append(contextual_execute(batch, source_context))
                else:
                    results.append(executor.execute(batch))
            except Exception as exc:  # noqa: BLE001
                failed_error = scrub_exception(
                    exc,
                    known_values=(delegated_secret,) if delegated_secret else (),
                )
                break
            except BaseException:
                _skip_resolution(original_sq.source.source_id)
                raise
        if failed_error is not None:
            # A failed batch invalidates the whole leg; successful earlier
            # batches are intentionally discarded so partiality is never hidden.
            step = RetrievalStep(
                source_id=original_sq.source.source_id,
                kind=original_sq.source.kind,
                sparql="\n# seed batch\n".join(batch.sparql for batch in batches),
                status="failed",
                error=failed_error,
                seeded_vars=seeded_vars,
                seed_strategy=seed_strategy,
                seed_batch_count=len(results),
            )
            metrics = LegExecutionMetrics(
                source_id=original_sq.source.source_id,
                kind=original_sq.source.kind,
                status="failed",
                duration_ms=(perf_counter() - leg_started) * 1000,
                row_count=0,
                seed_row_count=seed_row_count,
                seed_cap=policy.seed_batch_rows,
                seed_cap_exceeded=seed_cap_exceeded,
                seed_strategy=seed_strategy,
                seed_batch_count=len(results),
                max_seed_rows=policy.max_seed_rows,
            )
            _skip_resolution(original_sq.source.source_id)
            return original_sq, step, None, metrics, batch_refusal, None, None

        merged_rows = [dict(row) for result in results for row in result.rows]
        if len(results) > 1:
            merged_rows = _deduplicate_bindings(merged_rows)
        if plan_authorization is not None:
            try:
                verify_authorized_rows(
                    plan_authorization,
                    original_sq.source.source_id,
                    tuple(merged_rows),
                )
            except AuthorizationFailure as exc:
                step = RetrievalStep(
                    source_id=original_sq.source.source_id,
                    kind=original_sq.source.kind,
                    sparql="\n# seed batch\n".join(batch.sparql for batch in batches),
                    status="failed",
                    error=exc.refusal.message,
                    seeded_vars=seeded_vars,
                    seed_strategy=seed_strategy,
                    seed_batch_count=len(batches),
                )
                metrics = LegExecutionMetrics(
                    source_id=original_sq.source.source_id,
                    kind=original_sq.source.kind,
                    status="failed",
                    duration_ms=(perf_counter() - leg_started) * 1000,
                    row_count=0,
                    seed_row_count=seed_row_count,
                    seed_cap=policy.seed_batch_rows,
                    seed_cap_exceeded=seed_cap_exceeded,
                    seed_strategy=seed_strategy,
                    seed_batch_count=len(batches),
                    max_seed_rows=policy.max_seed_rows,
                )
                _skip_resolution(original_sq.source.source_id)
                return original_sq, step, None, metrics, None, None, exc.refusal
        resolution_result: ResolutionRowsResult | None = None
        binding = configured_resolution.get(original_sq.source.source_id)
        if binding is not None:
            if resolution_runtime is None:
                resolution_result = ResolutionRowsResult(
                    rows=(),
                    events=(),
                    shortfalls=(),
                    metrics=ResolutionLegMetrics(
                        source_id=original_sq.source.source_id,
                        removed_rows=len(merged_rows),
                    ),
                    refusal=ResolutionRefusal(
                        code="entity_resolver_unconfigured",
                        phase="preflight",
                        source_id=original_sq.source.source_id,
                        reason="entity_resolver_unconfigured",
                        message=(
                            "runtime resolution is configured for source "
                            f"{original_sq.source.source_id} but no CDF entity_resolver "
                            "was injected"
                        ),
                    ),
                )
            else:
                resolution_result = resolution_runtime.normalize(
                    original_sq.source.source_id,
                    merged_rows,
                    binding,
                )
            merged_rows = list(resolution_result.rows)
            if resolution_result.refusal is not None:
                step = RetrievalStep(
                    source_id=original_sq.source.source_id,
                    kind=original_sq.source.kind,
                    sparql="\n# seed batch\n".join(batch.sparql for batch in batches),
                    status="failed",
                    row_count=0,
                    native_query="\n-- seed batch\n".join(
                        item.native_query
                        for item in results
                        if item.native_query is not None
                    )
                    or None,
                    as_of=next(
                        (item.as_of for item in reversed(results) if item.as_of),
                        None,
                    ),
                    source_objects=tuple(
                        dict.fromkeys(
                            obj for item in results for obj in item.source_objects
                        )
                    ),
                    error=resolution_result.refusal.message,
                    seeded_vars=seeded_vars,
                    seed_strategy=seed_strategy,
                    seed_batch_count=len(batches),
                    resolution_events=resolution_result.events,
                    resolution_metrics=resolution_result.metrics,
                )
                metrics = LegExecutionMetrics(
                    source_id=original_sq.source.source_id,
                    kind=original_sq.source.kind,
                    status="failed",
                    duration_ms=(perf_counter() - leg_started) * 1000,
                    row_count=0,
                    seed_row_count=seed_row_count,
                    seed_cap=policy.seed_batch_rows,
                    seed_cap_exceeded=seed_cap_exceeded,
                    seed_strategy=seed_strategy,
                    seed_batch_count=len(batches),
                    max_seed_rows=policy.max_seed_rows,
                    resolution_metrics=resolution_result.metrics,
                )
                return original_sq, step, None, metrics, None, resolution_result, None
        result = SourceResult(
            rows=tuple(merged_rows),
            native_query="\n-- seed batch\n".join(
                item.native_query for item in results if item.native_query is not None
            )
            or None,
            as_of=next((item.as_of for item in reversed(results) if item.as_of), None),
            source_objects=tuple(
                dict.fromkeys(obj for item in results for obj in item.source_objects)
            ),
            bytes_processed=(
                sum(item.bytes_processed for item in results if item.bytes_processed is not None)
                if results and all(item.bytes_processed is not None for item in results)
                else None
            ),
            cost_usd=(
                sum(item.cost_usd for item in results if item.cost_usd is not None)
                if results and all(item.cost_usd is not None for item in results)
                else None
            ),
            retry_count=sum(item.retry_count for item in results),
            truncated=any(item.truncated for item in results),
        )
        cited_sparql = "\n# seed batch\n".join(batch.sparql for batch in batches)
        step = RetrievalStep(
            source_id=original_sq.source.source_id,
            kind=original_sq.source.kind,
            sparql=cited_sparql,
            status="ok",
            row_count=len(result.rows),
            native_query=result.native_query,
            as_of=result.as_of,
            source_objects=result.source_objects,
            seeded_vars=seeded_vars,
            seed_strategy=seed_strategy,
            seed_batch_count=len(batches),
            resolution_events=(
                resolution_result.events if resolution_result is not None else ()
            ),
            resolution_metrics=(
                resolution_result.metrics if resolution_result is not None else None
            ),
        )
        metrics = LegExecutionMetrics(
            source_id=original_sq.source.source_id,
            kind=original_sq.source.kind,
            status="ok",
            duration_ms=(perf_counter() - leg_started) * 1000,
            row_count=len(result.rows),
            bytes_processed=result.bytes_processed,
            cost_usd=result.cost_usd,
            retry_count=result.retry_count,
            seed_row_count=seed_row_count,
            seed_cap=policy.seed_batch_rows,
            seed_cap_exceeded=seed_cap_exceeded,
            seed_strategy=seed_strategy,
            seed_batch_count=len(batches),
            max_seed_rows=policy.max_seed_rows,
            truncated=result.truncated,
            resolution_metrics=(
                resolution_result.metrics if resolution_result is not None else None
            ),
        )
        return original_sq, step, result, metrics, None, resolution_result, None

    admission_refusal: AdmissionRefusal | None = None
    for stage in stages:
        if not stage:
            continue
        # Bind-join: seed every leg in this stage with the join-key rows
        # already in hand from prior stages.
        prepared_legs: list[
            tuple[
                SubQuery,
                tuple[str, ...],
                int,
                bool,
                tuple[SubQuery, ...],
                str,
            ]
        ] = []
        for sq in stage:
            shared = sorted(join_vars & {_bare(v) for v in sq.variables})
            seeded_vars: tuple[str, ...] = ()
            seed_row_count = 0
            seed_cap_exceeded = False
            batches: tuple[SubQuery, ...] = (sq,)
            seed_strategy = "unseeded"
            if shared and accumulated:
                seed_rows = _distinct_rows(accumulated, shared)
                seed_row_count = len(seed_rows)
                if seed_rows and len(seed_rows) <= policy.max_seed_rows:
                    batches = tuple(
                        replace(
                            sq,
                            sparql=_with_values(
                                sq.sparql,
                                shared,
                                seed_rows[offset : offset + policy.seed_batch_rows],
                            ),
                        )
                        for offset in range(0, len(seed_rows), policy.seed_batch_rows)
                    )
                    seeded_vars = tuple(shared)
                    seed_strategy = "values" if len(batches) == 1 else "values-batched"
                if len(seed_rows) > policy.seed_batch_rows:
                    seed_cap_exceeded = True
                if len(seed_rows) > policy.max_seed_rows:
                    seed_strategy = "refused-overflow"
                    seeded_vars = tuple(shared)
                    batches = ()
            prepared_legs.append(
                (
                    sq,
                    seeded_vars,
                    seed_row_count,
                    seed_cap_exceeded,
                    batches,
                    seed_strategy,
                )
            )

        stage_started = perf_counter()
        if len(prepared_legs) == 1:
            outcomes = [_run_leg(prepared_legs[0])]
        else:
            with ThreadPoolExecutor(
                max_workers=min(len(prepared_legs), max_workers)
            ) as pool:
                # pool.map preserves input order, so the retrieval path and the
                # running join below stay deterministic regardless of which leg
                # finishes first.
                outcomes = list(pool.map(_run_leg, prepared_legs))
        execution_duration_ms += (perf_counter() - stage_started) * 1000

        for (
            sq,
            step,
            result,
            metrics,
            leg_refusal,
            leg_resolution,
            leg_authorization_refusal,
        ) in outcomes:
            leg_authorization_events = (
                authorization_events_for_source(
                    plan_authorization,
                    sq.source.source_id,
                )
                if plan_authorization is not None
                else ()
            )
            step = replace(step, authorization_events=leg_authorization_events)
            metrics = replace(metrics, authorization_events=leg_authorization_events)
            authorization_events.extend(leg_authorization_events)
            steps.append(step)
            leg_metrics.append(metrics)
            if leg_refusal is not None:
                admission_refusal = leg_refusal
            if leg_resolution is not None:
                resolution_events.extend(leg_resolution.events)
                resolution_shortfalls.extend(leg_resolution.shortfalls)
                resolution_leg_metrics.append(leg_resolution.metrics)
                if leg_resolution.refusal is not None:
                    resolution_refusal = leg_resolution.refusal
            if leg_authorization_refusal is not None:
                authorization_refusal = leg_authorization_refusal
            if result is None:
                failed.append(sq.source.source_id)
                continue
            source_row_ids: tuple[str, ...] = ()
            if assembly is not None:
                try:
                    source_row_ids = assembly.materialize_source(
                        result.rows,
                        source_id=sq.source.source_id,
                        subquery=step.sparql,
                        native_query=result.native_query,
                        as_of=result.as_of,
                        resolution_events=step.resolution_events,
                    )
                except AssemblyFailure as exc:
                    assembly_refusal = exc.refusal
                    break
            successful.append((sq, result))
            join_started = perf_counter()
            result_rows = [dict(row) for row in result.rows]
            if not accumulated:
                accumulated = result_rows
                if assembly is not None:
                    accumulated_row_ids = [(row_id,) for row_id in source_row_ids]
            elif assembly is None:
                accumulated = _inner_join(accumulated, result_rows)
            else:
                joined_rows, input_ids = _inner_join_with_inputs(
                    accumulated,
                    result_rows,
                    accumulated_row_ids,
                    list(source_row_ids),
                )
                try:
                    joined_row_ids = assembly.materialize_join(joined_rows, input_ids)
                except AssemblyFailure as exc:
                    assembly_refusal = exc.refusal
                    break
                accumulated = joined_rows
                accumulated_row_ids = [(row_id,) for row_id in joined_row_ids]
            reassembly_duration_ms += (perf_counter() - join_started) * 1000
            if (
                policy.max_intermediate_rows is not None
                and len(accumulated) > policy.max_intermediate_rows
            ):
                admission_refusal = runtime_refusal(
                    "max_intermediate_rows_exceeded",
                    "intermediate_rows",
                    len(accumulated),
                    policy.max_intermediate_rows,
                )
        if (
            admission_refusal is not None
            or assembly_refusal is not None
            or resolution_refusal is not None
            or authorization_refusal is not None
        ):
            break
        if (
            policy.runtime_wall_time_ms is not None
            and (perf_counter() - plan_started) * 1000 > policy.runtime_wall_time_ms
        ):
            admission_refusal = runtime_refusal(
                "runtime_wall_time_exceeded",
                "runtime_wall_time_ms",
                (perf_counter() - plan_started) * 1000,
                policy.runtime_wall_time_ms,
            )
            break

    final_reassembly_started = perf_counter()
    # The accumulated running join *is* the joined result.
    joined: list[Binding] = accumulated

    # Variables any *successful* leg can supply (independent of row count, so a
    # legitimately empty leg still "provides" its variables).
    available: set[str] = set()
    for sq, _result in successful:
        available.update(_bare(v) for v in sq.variables)

    projection = [_bare(v) for v in plan.projection]
    unavailable = tuple(v for v in projection if v not in available)

    if projection:
        bindings = tuple(
            {k: row[k] for k in projection if k in row} for row in joined
        )
    else:
        bindings = tuple(joined)

    final_rows_truncated = False
    if policy.max_final_rows is not None and len(bindings) > policy.max_final_rows:
        if policy.allow_partial_on_runtime_cap and allow_partial_limits:
            bindings = bindings[: policy.max_final_rows]
            final_rows_truncated = True
        else:
            admission_refusal = runtime_refusal(
                "max_final_rows_exceeded",
                "final_rows",
                len(bindings),
                policy.max_final_rows,
            )

    partial = (
        bool(failed)
        or bool(plan.unresolved)
        or bool(unavailable)
        or admission_refusal is not None
        or final_rows_truncated
        or bool(resolution_shortfalls)
        or resolution_refusal is not None
        or authorization_refusal is not None
    )
    reassembly_duration_ms += (perf_counter() - final_reassembly_started) * 1000

    known_bytes = [metric.bytes_processed for metric in leg_metrics]
    bytes_processed = (
        sum(value for value in known_bytes if value is not None)
        if known_bytes and all(value is not None for value in known_bytes)
        else None
    )
    known_costs = [metric.cost_usd for metric in leg_metrics]
    cost_usd = (
        sum(value for value in known_costs if value is not None)
        if known_costs and all(value is not None for value in known_costs)
        else None
    )
    resolution_metrics = rollup_resolution_metrics(resolution_leg_metrics)
    execution_metrics = PlanExecutionMetrics(
        total_duration_ms=(perf_counter() - plan_started) * 1000,
        partition_duration_ms=None,
        execution_duration_ms=execution_duration_ms,
        reassembly_duration_ms=reassembly_duration_ms,
        leg_duration_sum_ms=sum(metric.duration_ms for metric in leg_metrics),
        row_count=len(bindings),
        legs=tuple(leg_metrics),
        bytes_processed=bytes_processed,
        cost_usd=cost_usd,
        retry_count=sum(metric.retry_count for metric in leg_metrics),
        seed_cap=policy.seed_batch_rows,
        seed_cap_exceeded=any(metric.seed_cap_exceeded for metric in leg_metrics),
        truncated=any(metric.truncated for metric in leg_metrics) or final_rows_truncated,
        strategy=strategy.strategy if strategy is not None else "legacy-no-statistics",
        seed_batch_count=sum(metric.seed_batch_count for metric in leg_metrics),
        seed_overflow=any(metric.seed_overflow for metric in leg_metrics),
        max_seed_rows=policy.max_seed_rows,
        plan_estimate=strategy,
        admission_refusal=admission_refusal,
        resolution_metrics=resolution_metrics,
        assembly_metrics=assembly.metrics() if assembly is not None else virtual_metrics(),
        request_metadata=context.safe_metadata(),
        authorization_events=tuple(authorization_events),
    )
    return FederatedResult(
        bindings=bindings,
        retrieval_path=tuple(steps),
        partial=partial,
        failed_sources=tuple(failed),
        unavailable_vars=unavailable,
        unresolved=plan.unresolved,
        execution_metrics=execution_metrics,
        plan_estimate=strategy,
        admission_refusal=admission_refusal,
        resolution_events=tuple(resolution_events),
        resolution_shortfalls=tuple(resolution_shortfalls),
        resolution_metrics=resolution_metrics,
        resolution_refusal=resolution_refusal,
        assembly_metrics=assembly.metrics() if assembly is not None else virtual_metrics(),
        assembly_refusal=assembly_refusal,
        request_metadata=context.safe_metadata(),
        authorization=plan_authorization,
        authorization_events=tuple(authorization_events),
        authorization_refusal=authorization_refusal,
        withheld_sources=(
            plan_authorization.withheld_sources if plan_authorization is not None else ()
        ),
    )
