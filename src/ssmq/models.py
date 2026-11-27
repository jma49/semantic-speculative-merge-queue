"""Records, configuration, and metrics for the merge queue simulation (v7)."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, FrozenSet, Optional, Tuple


@dataclass(frozen=True)
class PRRecord:
    number: int
    files: Tuple[str, ...]
    arrival_min: float = 0.0
    additions: int = 0
    deletions: int = 0
    changed_files_count: int = 0
    repo: str = ""
    created_at: str = ""
    merged_at: str = ""
    commit_count: int = 0
    author: str = ""
    risk_score: float = 0.0
    impact_cone: FrozenSet[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class SimConfig:
    workers: int = 8
    ci_duration_min: float = 12.0
    ci_duration_std: float = 2.0
    max_batch_size: int = 8
    max_spec_depth: int = 4
    velocity_multiplier: float = 5.0
    base_defect_rate: float = 0.08
    semantic_conflict_prob: float = 0.22
    flaky_rate: float = 0.05
    blame_top1_acc: float = 0.85
    blame_top2_acc: float = 0.95
    blame_overhead_min: float = 0.5
    warmup_fraction: float = 0.25
    graph_recall: float = 0.80
    persistent_conflict_share: float = 0.25
    risk_correlation: float = 1.0
    # v6 controls:
    # - ground_truth_mode: "parametric" (controlled recall sweep) or
    #   "independent_eval_coupling" (derived strictly from evaluation-window file/subdir/cochange
    #   structure without ever reading scheduler warm-up graph G_hat).
    # - feasible_recall_budget: when True in parametric mode, caps per-seed total conflict target
    #   at min(len(base_visible), len(disjoint_pairs)) so EVERY repo (including dense TypeScript)
    #   maintains EXACT identical total conflict counts across the entire recall sweep [0%, 100%].
    ground_truth_mode: str = "parametric"
    feasible_recall_budget: bool = False
    # v7 decoupled fixed scheduler priors (never read ground-truth fault rates)
    scheduler_defect_prior: float = 0.10
    scheduler_conflict_prior: float = 0.10
    scheduler_persistent_prior: float = 0.02


@dataclass(frozen=True)
class RunMetrics:
    strategy: str
    seed: int
    throughput_prs_per_hr: float
    p50_latency_min: float
    p95_latency_min: float
    ci_minutes_per_merged_pr: float
    invalidation_rate: float
    mean_blame_rounds: float
    merged_prs: int
    rejected_prs: int
    escaped_defects: int
    total_ci_runs: int
    wasted_ci_runs: int
    sim_duration_hr: float
    clean_prs_total: int = 0
    clean_prs_merged: int = 0
    false_rejection_rate: float = 0.0
    mean_batch_size: float = 0.0
    runner_utilization: float = 0.0
    off_graph_conflicts_total: int = 0
    on_graph_conflicts_total: int = 0
    # Mechanism & acceptance instrumentation
    root_batches_dispatched: int = 0
    mean_root_batch_size: float = 0.0
    failed_root_batches: int = 0
    failed_root_single_culprit: int = 0
    failed_root_multi_culprit: int = 0
    mean_failed_root_size: float = 0.0
    mean_culprits_per_failed_root: float = 0.0
    blame_attempts: int = 0
    blame_one_step_hits: int = 0
    rejected_defective: int = 0
    rejected_persistent_conflict: int = 0
    rejected_false_flaky: int = 0
    realized_graph_recall: Optional[float] = None
    # v6 non-circular graph quality & comparator diagnostics
    realized_graph_precision: Optional[float] = None
    observed_graph_edge_density: float = 0.0
    bridge_wait_min_total: float = 0.0
    canceled_ci_minutes: float = 0.0
    stale_rechecks: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
