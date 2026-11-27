"""Records, configuration, and metrics for the merge queue simulation (v4)."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, FrozenSet, Tuple


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
    # v4 Mechanism & Acceptance Instrumentation
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
    realized_graph_recall: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
