"""Batch 2 baseline policies under equal exact-tree commit authority (v8).

All strategies share:
  - Identical frozen v7 repository traces, warm-up split, and arrival scaling
  - Identical paired GroundTruth (via src.ssmq.simulator.build_ground_truth)
  - Identical CI duration & flakiness RNG draws (_key(seed, "ci", attempt, *canonical))
  - Identical worker capacity (W=8), max_batch_size (8), max_spec_depth (4)
  - Identical decoupled generic scheduler priors (zero ground-truth rate peeking)
  - Strict exact-tree commit authority (tested_tree == target_tree, zero escapes)

Existing v7 control strategies delegate directly to `src.ssmq.simulate_queue`
without modifying any file under `src/ssmq/` so v7 seals remain immutable.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, fields, replace
from datetime import datetime
from heapq import heappop, heappush
from itertools import combinations, count
import json
from math import fsum, isfinite, prod
from pathlib import Path
from random import Random
import sys
from typing import Deque, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple, Union

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ssmq.graph import DependencyGraph, _module  # noqa: E402
from ssmq.loader import load_trace  # noqa: E402
from ssmq.models import PRRecord, RunMetrics, SimConfig  # noqa: E402
from ssmq.simulator import (  # noqa: E402
    _FAST_LANE_RISK,
    _FLAKE_RERUN_THRESHOLD,
    _Job,
    _Members,
    _Recovery,
    _Run,
    _SuitePosterior,
    _key,
    _percentile,
    _validate,
    _warmup_scheduler_priors,
    build_ground_truth,
    simulate_queue,
)

V7_CONTROL_STRATEGIES: Tuple[str, ...] = (
    "SSMQ",
    "SSMQ-NoCone",
    "FIFOBatchRetry",
    "StaticBatchBisect",
    "SpeculativeLinear",
    "FIFOOutcomeTree",
)

NEW_V8_STRATEGIES: Tuple[str, ...] = (
    "Beheshtian-BatchStop4",
    "Bors-BatchBisect",
    "GitHubMQ-StagedGroup",
    "PRediBatch-RiskGroup",
    "Aviator-DirAffected",
    "SSMQ-FilteredCone",
)

ALL_V8_STRATEGIES: Tuple[str, ...] = V7_CONTROL_STRATEGIES + NEW_V8_STRATEGIES

_BATCHSTOP4_THRESHOLD = 4
_PREDIBATCH_MIN_SURVIVAL = 0.55
_FILTERED_CONE_MAX_FILES = 20
_PR_FIELDS = {f.name for f in fields(PRRecord)}


class FilteredConeGraph:
    """Warm-up-only co-change graph excluding mega-PRs and broad directory cliques.

    Built strictly from chronological warm-up PRs (never peeking at evaluation PRs):
      1. Drops warm-up mega-PRs with > `max_files` unique files (default 20).
      2. Omits two-level directory cliques (which alone saturate monorepo cones).
      3. Retains direct touched files plus 1-hop co-change neighbors meeting
         `min_support` (default 1).
    """

    def __init__(
        self,
        warmup: Iterable[PRRecord] = (),
        *,
        max_files: int = _FILTERED_CONE_MAX_FILES,
        min_support: int = 1,
    ) -> None:
        self.max_files = max_files
        self.min_support = min_support
        pair_counts: Counter = Counter()
        self.warmup_total = 0
        self.warmup_kept = 0
        for pr in warmup:
            self.warmup_total += 1
            files = sorted(set(pr.files))
            if len(files) > max_files:
                continue
            self.warmup_kept += 1
            for first, second in combinations(files, 2):
                pair_counts[(first, second)] += 1
        adj: Dict[str, Set[str]] = defaultdict(set)
        for (first, second), support in pair_counts.items():
            if support >= min_support:
                adj[first].add(second)
                adj[second].add(first)
        self._adj: Dict[str, FrozenSet[str]] = {k: frozenset(v) for k, v in adj.items()}

    def impact_cone(self, files: Iterable[str]) -> FrozenSet[str]:
        cone = set(files)
        for path in list(cone):
            cone.update(self._adj.get(path, ()))
        return frozenset(cone)


def load_trace_with_warmup(
    path: Union[str, Path],
    warmup_fraction: float = SimConfig.warmup_fraction,
    baseline_arrivals_per_hr: float = 6.0,
) -> Tuple[DependencyGraph, FilteredConeGraph, List[PRRecord]]:
    """Load frozen trace identically to v7 plus warm-up-only FilteredConeGraph."""
    graph, records = load_trace(
        path,
        warmup_fraction=warmup_fraction,
        baseline_arrivals_per_hr=baseline_arrivals_per_hr,
    )
    with Path(path).open(encoding="utf-8") as source:
        rows = json.load(source)
    dated = [
        (
            datetime.fromisoformat(row["merged_at"].replace("Z", "+00:00")),
            PRRecord(**{**{k: v for k, v in row.items() if k in _PR_FIELDS}, "files": tuple(row["files"])}),
        )
        for row in rows
    ]
    dated.sort(key=lambda item: (item[0], item[1].number))
    split = int(len(dated) * warmup_fraction)
    filtered_graph = FilteredConeGraph(pr for _, pr in dated[:split])
    return graph, filtered_graph, records


@dataclass
class _StageNode:
    members: _Members
    run: _Run
    is_recovery: bool = False


@dataclass
class _AviatorLane:
    """Direct-directory target lane for Aviator-DirAffected."""

    lane_id: int
    targets: Set[Tuple[str, ...]]
    queue: List[int]
    stages: Deque[_StageNode]


class _V8Simulation:
    """Discrete-event simulation for Batch 2 baselines under exact-tree commit."""

    def __init__(
        self,
        graph: DependencyGraph,
        prs: List[PRRecord],
        strategy: str,
        config: SimConfig,
        seed: int,
        filtered_graph: Optional[FilteredConeGraph] = None,
    ) -> None:
        self.strategy = strategy
        self.config = config
        self.seed = seed
        self.records = {pr.number: pr for pr in prs}
        self.v7_cones = {
            pr.number: pr.impact_cone or frozenset(graph.impact_cone(pr.files))
            for pr in prs
        }
        # Ground truth is ALWAYS paired identically to v7 (never modified by strategy!).
        self.truth = build_ground_truth(graph, prs, config, seed)
        # Leak-free decoupled scheduler prior calibrated strictly from warm-up history.
        self.defect_risk = _warmup_scheduler_priors(graph, prs, config.scheduler_defect_prior)
        self.arrivals = {
            pr.number: pr.arrival_min / config.velocity_multiplier for pr in prs
        }

        if strategy == "SSMQ-FilteredCone":
            fg = filtered_graph if filtered_graph is not None else FilteredConeGraph()
            self.cones: Dict[int, FrozenSet[str]] = {
                pr.number: fg.impact_cone(pr.files) for pr in prs
            }
        else:
            self.cones = self.v7_cones

        # Direct 2-level directory targets (no transitive warm-up co-change expansion).
        self.dir_targets: Dict[int, FrozenSet[Tuple[str, ...]]] = {
            pr.number: frozenset(_module(path) for path in pr.files) for pr in prs
        }

        self.use_cone = strategy == "SSMQ-FilteredCone"
        self.use_risk_lanes = strategy == "SSMQ-FilteredCone"
        self.use_blame = strategy == "SSMQ-FilteredCone"
        self.adaptive_rerun = strategy == "SSMQ-FilteredCone"
        # Historical bors-ng runs one staging branch at a time; all others use W workers.
        self.worker_limit = 1 if strategy == "Bors-BatchBisect" else config.workers

        self.ready: List[int] = []
        self.pending: Deque[_Job] = deque()
        self.protected: Set[int] = set()
        self.stages: Deque[_StageNode] = deque()
        self.pb_recovery: Deque[_Job] = deque()
        self.aviator_lanes: List[_AviatorLane] = []
        self._next_lane_id = count()
        self.bridge_wait = 0.0
        self.bridge_count = 0
        self.canceled_ci = 0.0
        self.stale_rechecks = 0
        self.suite = _SuitePosterior()
        self.sequence = count()
        self.events: List[Tuple[float, int, Union[int, _Run]]] = []
        self.recoveries: List[_Recovery] = []
        self.attempts: Counter = Counter()
        self.now = 0.0
        self.running = 0
        self.ci_time = 0.0
        self.total_runs = 0
        self.total_batch_members = 0
        self.wasted_runs = 0
        self.invalidated_runs = 0
        self.escaped = 0
        self.merged: Dict[int, float] = {}
        self.merged_mainline: Set[int] = set()
        self.rejected: Set[int] = set()
        self.false_rejections = 0
        self.rejected_defective = 0
        self.rejected_persistent_conflict = 0
        self.root_batches_dispatched = 0
        self.root_batch_members_total = 0
        self.failed_root_batches = 0
        self.failed_root_single_culprit = 0
        self.failed_root_multi_culprit = 0
        self.failed_root_members_total = 0
        self.failed_root_culprits_total = 0
        self.blame_attempts = 0
        self.blame_one_step_hits = 0

        for number, arrival in sorted(self.arrivals.items(), key=lambda item: (item[1], item[0])):
            heappush(self.events, (arrival, next(self.sequence), number))

    def run(self) -> RunMetrics:
        previous_event = 0.0
        while self.events:
            when = self.events[0][0]
            self.bridge_wait += (when - previous_event) * self.bridge_count
            previous_event = when
            while self.events and self.events[0][0] == when:
                _, _, event = heappop(self.events)
                if isinstance(event, int):
                    self.now = when
                    self.ready.append(event)
                elif not event.wasted:
                    self.now = when
                    self._complete(event)
            self._dispatch()

        if len(self.merged) + len(self.rejected) != len(self.records):
            raise RuntimeError(
                f"{self.strategy}: queue drained with unresolved pull requests "
                f"({len(self.merged)} merged + {len(self.rejected)} rejected != {len(self.records)})"
            )
        return self._metrics()

    def _root_job(self, members: _Members) -> _Job:
        return _Job(members, _Recovery(), is_root=True)

    def _prefix_has_fault(self, prefix: _Members) -> bool:
        """Check whether an unmerged speculative prefix already contains a breaking fault."""
        if not prefix:
            return False
        base = set(self.merged_mainline)
        for pr_num in prefix:
            if pr_num in self.truth.defective_prs:
                return True
            if self.truth.persistent_culprits((pr_num,), base):
                return True
            base.add(pr_num)
        return False

    def _start(self, job: _Job, *, check_prefix_fault: bool = False) -> _Run:
        canonical = tuple(sorted(job.members))
        attempt = self.attempts[canonical]
        self.attempts[canonical] += 1
        key = _key(self.seed, "ci", attempt, *canonical)
        rng = Random(key)
        duration = max(
            0.01, rng.gauss(self.config.ci_duration_min, self.config.ci_duration_std)
        ) + job.overhead
        mainline = frozenset(self.merged_mainline.union(job.prefix))
        culprits = self.truth.culprits(job.members, mainline)
        prefix_broken = check_prefix_fault and self._prefix_has_fault(job.prefix)
        passes = (not culprits) and (not prefix_broken) and (rng.random() >= self.config.flaky_rate)
        run = _Run(job, self.now, self.now + duration, passes, mainline, culprits)
        heappush(self.events, (run.finishes, next(self.sequence), run))
        self.running += 1
        self.total_runs += 1
        self.total_batch_members += len(job.members)
        if job.is_root and job.failures == 0 and job.round == 0:
            self.root_batches_dispatched += 1
            self.root_batch_members_total += len(job.members)
        return run

    def _discard(self, run: _Run, *, invalidated: bool) -> None:
        if not run.completed:
            self.running -= 1
            self.ci_time += self.now - run.started
            self.canceled_ci += self.now - run.started
        run.wasted = True
        self.wasted_runs += 1
        self.invalidated_runs += int(invalidated)

    def _complete(self, run: _Run) -> None:
        run.completed = True
        self.running -= 1
        self.ci_time += run.finishes - run.started
        if self.strategy == "GitHubMQ-StagedGroup":
            self._resolve_gh_mq()
        elif self.strategy == "PRediBatch-RiskGroup":
            self._resolve_predibatch()
        elif self.strategy == "Aviator-DirAffected":
            self._resolve_aviator()
        else:
            self._resolve_batch(run)

    def _dispatch(self) -> None:
        if self.strategy == "GitHubMQ-StagedGroup":
            self._dispatch_gh_mq()
        elif self.strategy == "PRediBatch-RiskGroup":
            self._dispatch_predibatch()
        elif self.strategy == "Aviator-DirAffected":
            self._dispatch_aviator()
        else:
            while self.pending and self.running < self.worker_limit:
                self._start(self.pending.popleft())
            while self.ready and self.running < self.worker_limit:
                self._start(self._root_job(self._take_batch()))

    # ---- Unstacked batching (Beheshtian-BatchStop4, Bors-BatchBisect, SSMQ-FilteredCone) ----
    def _take_batch(self) -> _Members:
        if self.strategy in ("Beheshtian-BatchStop4", "Bors-BatchBisect"):
            size = self.config.max_batch_size
            members = tuple(self.ready[:size])
            del self.ready[:size]
            return members

        # SSMQ-FilteredCone: identical packing to SSMQ but with filtered warm-up cones.
        head = self.ready[0]
        protected = self._is_protected(head)
        limit = min(2, self.config.max_batch_size) if protected else self.config.max_batch_size
        members_list = [head]
        combined = set(self.cones[head])
        waiting = []
        for number in self.ready[1:]:
            overlap = self.use_cone and (not combined.isdisjoint(self.cones[number]))
            if overlap:
                self.protected.add(number)
            if len(members_list) < limit and not overlap and self._is_protected(number) == protected:
                members_list.append(number)
                combined.update(self.cones[number])
            else:
                waiting.append(number)
        self.ready = waiting
        self.protected.difference_update(members_list)
        return tuple(members_list)

    def _is_protected(self, number: int) -> bool:
        if number in self.protected:
            return True
        if self.use_risk_lanes and self.records[number].risk_score >= _FAST_LANE_RISK:
            return True
        return False

    def _failure_risk(self, members: _Members) -> float:
        clean = prod(1 - self.defect_risk[number] for number in members)
        overlap_count = sum(
            not self.cones[first].isdisjoint(self.cones[second])
            for first, second in combinations(members, 2)
        )
        return 1 - clean * (1 - self.config.scheduler_conflict_prior) ** overlap_count

    def _should_rerun(self, job: _Job) -> bool:
        if not self.adaptive_rerun or job.failures >= 2:
            return False
        fault = self._failure_risk(job.members)
        transient = (1 - fault) * self.suite.failure_probability(job.failures + 1)
        evidence = fault + transient
        threshold = _FLAKE_RERUN_THRESHOLD if len(job.members) == 1 else 0.50
        return evidence > 0 and transient / evidence >= threshold

    def _credit_retry(self, job: _Job) -> None:
        if self.adaptive_rerun:
            self.suite.alpha += job.failures
            self.suite.beta += 1
        self.wasted_runs += job.failures
        self.invalidated_runs += job.failures

    def _split_jobs(self, job: _Job) -> Tuple[_Job, ...]:
        if self.strategy == "Beheshtian-BatchStop4" and len(job.members) <= _BATCHSTOP4_THRESHOLD:
            # Faithful Beheshtian et al. (TSE 2022) BatchStop4 rule:
            # once a failing batch has size <= 4, stop bisecting and test each commit individually.
            return tuple(
                _Job((member,), job.recovery, job.round + 1, allow_blame=False)
                for member in job.members
            )
        midpoint = len(job.members) // 2
        halves = (job.members[:midpoint], job.members[midpoint:])
        if job.next_suspect in halves[1]:
            halves = halves[::-1]
        return tuple(
            _Job(members, job.recovery, job.round + 1, allow_blame=False)
            for members in halves
        )

    def _resolve_batch(self, run: _Run) -> None:
        job = run.job
        self._record_outcome(run)
        if run.passes:
            if self.merged_mainline != run.mainline:
                self._discard(run, invalidated=True)
                self.stale_rechecks += 1
                self.pending.appendleft(replace(job, overhead=0.0))
                return
            if job.blame_remainder and job.recovery.faulty:
                self.blame_one_step_hits += 1
            self._credit_retry(job)
            self._merge(run, job.members)
            return

        if self._should_rerun(job):
            self.pending.appendleft(
                replace(job, failures=job.failures + 1, round=job.round + 1, overhead=0.0)
            )
        elif len(job.members) == 1:
            self._reject(job.members[0])
        elif self.use_blame and job.allow_blame:
            self._attribute(run)
        else:
            children = self._split_jobs(job)
            if self.strategy == "Bors-BatchBisect":
                # Historical bors-ng finishes bisecting the failing batch on staging before
                # advancing to newly queued PRs (depth-first left then right).
                for child in reversed(children):
                    self.pending.appendleft(child)
            else:
                self.pending.extend(children)

    def _attribute(self, run: _Run) -> None:
        job = run.job
        self.blame_attempts += 1
        top, second = self._rank_suspects(job.members, run.culprits)[:2]
        remaining = tuple(number for number in job.members if number != top)
        self.pending.extend(
            (
                _Job(
                    remaining,
                    job.recovery,
                    job.round + 1,
                    allow_blame=False,
                    next_suspect=second,
                    overhead=self.config.blame_overhead_min,
                    blame_remainder=True,
                ),
                _Job((top,), job.recovery, job.round + 1),
            )
        )

    def _rank_suspects(self, members: _Members, culprits: _Members) -> _Members:
        rng = Random(_key(self.seed, "blame", *sorted(members)))
        ranking = sorted(
            members,
            key=lambda number: (self.records[number].risk_score, len(self.cones[number]), rng.random()),
            reverse=True,
        )
        if culprits:
            culprit = rng.choice(culprits)
            ranking.remove(culprit)
            draw = rng.random()
            if draw < self.config.blame_top1_acc:
                rank = 0
            elif draw < self.config.blame_top2_acc or len(members) == 2:
                rank = 1
            else:
                rank = rng.randrange(2, len(members))
            ranking.insert(rank, culprit)
        return tuple(ranking)

    # ---- GitHubMQ-StagedGroup (documented ALLGREEN cumulative staged groups) ----
    def _dispatch_gh_mq(self) -> None:
        max_build = min(self.worker_limit, self.config.max_spec_depth)
        prefix = tuple(member for stage in self.stages for member in stage.members)
        while self.ready and self.running < self.worker_limit and len(self.stages) < max_build:
            pr_num = self.ready.pop(0)
            job = replace(self._root_job((pr_num,)), prefix=prefix)
            run = self._start(job, check_prefix_fault=True)
            self.stages.append(_StageNode((pr_num,), run))
            prefix = prefix + (pr_num,)

    def _resolve_gh_mq(self) -> None:
        while self.stages and self.stages[0].run.completed:
            head = self.stages.popleft()
            run = head.run
            self._record_outcome(run)
            if run.passes:
                self._merge(run, head.members)
                continue
            # Documented GitHub Merge Queue failure semantics (ALLGREEN):
            # evict the failing PR immediately (no bisection, no automatic retry),
            # cancel/destroy all downstream staged merge groups, and rebuild suffix.
            self._reject(head.members[0])
            requeued: List[int] = []
            for descendant in self.stages:
                self._discard(descendant.run, invalidated=True)
                requeued.extend(descendant.members)
            self.stages.clear()
            self.ready[:0] = requeued
            break

    # ---- PRediBatch-RiskGroup (PRediBatch-inspired metadata risk grouping in stacked pipeline) ----
    def _take_predibatch(self) -> _Members:
        head = self.ready[0]
        if self.records[head].risk_score >= _FAST_LANE_RISK:
            # High-risk PR isolated as singleton so it does not poison low-risk batches.
            return (self.ready.pop(0),)

        members = [head]
        survival = max(0.0, 1.0 - self.defect_risk[head])
        low_risk_candidates = [
            number
            for number in self.ready[1:]
            if self.records[number].risk_score < _FAST_LANE_RISK
        ]
        # Pack lowest predicted risk PRs first while preserving cumulative survival budget.
        low_risk_candidates.sort(
            key=lambda number: (self.defect_risk[number], self.arrivals[number], number)
        )
        chosen: Set[int] = {head}
        for candidate in low_risk_candidates:
            if len(members) >= self.config.max_batch_size:
                break
            next_survival = survival * max(0.0, 1.0 - self.defect_risk[candidate])
            if next_survival >= _PREDIBATCH_MIN_SURVIVAL:
                members.append(candidate)
                chosen.add(candidate)
                survival = next_survival
        self.ready = [number for number in self.ready if number not in chosen]
        return tuple(members)

    def _dispatch_predibatch(self) -> None:
        prefix = tuple(member for stage in self.stages for member in stage.members)
        while (
            (self.pb_recovery or self.ready)
            and self.running < self.worker_limit
            and len(self.stages) < self.config.max_spec_depth
        ):
            if self.pb_recovery:
                job = replace(self.pb_recovery.popleft(), prefix=prefix)
                is_rec = True
            else:
                job = replace(self._root_job(self._take_predibatch()), prefix=prefix)
                is_rec = False
            run = self._start(job, check_prefix_fault=True)
            self.stages.append(_StageNode(job.members, run, is_recovery=is_rec))
            prefix = prefix + job.members

    def _resolve_predibatch(self) -> None:
        while self.stages and self.stages[0].run.completed:
            head = self.stages.popleft()
            run = head.run
            self._record_outcome(run)
            if run.passes:
                self._merge(run, head.members)
                continue
            requeued_ready: List[int] = []
            requeued_recovery: List[_Job] = []
            for descendant in self.stages:
                self._discard(descendant.run, invalidated=True)
                if descendant.is_recovery:
                    requeued_recovery.append(replace(descendant.run.job, prefix=(), overhead=0.0))
                else:
                    requeued_ready.extend(descendant.members)
            self.stages.clear()
            if requeued_ready:
                self.ready[:0] = requeued_ready
            for rec_job in reversed(requeued_recovery):
                self.pb_recovery.appendleft(rec_job)
            if len(head.members) == 1:
                self._reject(head.members[0])
            else:
                left, right = self._split_jobs(run.job)
                self.pb_recovery.appendleft(right)
                self.pb_recovery.appendleft(left)
            break

    # ---- Aviator-DirAffected (direct 2-level directory targets + parallel lanes under exact-tree) ----
    def _assign_ready_to_aviator_lanes(self) -> None:
        still_waiting: List[int] = []
        blocked_targets: Set[Tuple[str, ...]] = set()
        for pr_num in self.ready:
            targets = self.dir_targets[pr_num]
            if not blocked_targets.isdisjoint(targets):
                still_waiting.append(pr_num)
                blocked_targets.update(targets)
                continue
            overlapping = [
                lane for lane in self.aviator_lanes if not lane.targets.isdisjoint(targets)
            ]
            if not overlapping:
                self.aviator_lanes.append(
                    _AviatorLane(next(self._next_lane_id), set(targets), [pr_num], deque())
                )
            elif len(overlapping) == 1:
                lane = overlapping[0]
                lane.queue.append(pr_num)
                lane.targets.update(targets)
            else:
                # Bridge PR touches >= 2 active directory lanes: wait until overlapping lanes drain.
                still_waiting.append(pr_num)
                blocked_targets.update(targets)
        self.ready = still_waiting
        self.bridge_count = len(still_waiting)

    def _dispatch_aviator(self) -> None:
        self._assign_ready_to_aviator_lanes()
        while self.running < self.worker_limit:
            # Pick the lane with the earliest-arriving next unstarted PR (prevents lane starvation).
            best_lane: Optional[_AviatorLane] = None
            best_key: Optional[Tuple[int, float, int]] = None
            for lane in self.aviator_lanes:
                started_count = len(lane.stages)
                if started_count >= len(lane.queue) or started_count >= self.config.max_spec_depth:
                    continue
                next_pr = lane.queue[started_count]
                cand_key = (started_count, self.arrivals[next_pr], lane.lane_id)
                if best_key is None or cand_key < best_key:
                    best_key = cand_key
                    best_lane = lane
            if best_lane is None:
                break
            next_pr = best_lane.queue[len(best_lane.stages)]
            prefix = tuple(m for s in best_lane.stages for m in s.members)
            job = replace(self._root_job((next_pr,)), prefix=prefix)
            run = self._start(job, check_prefix_fault=True)
            best_lane.stages.append(_StageNode((next_pr,), run))

    def _resolve_aviator(self) -> None:
        remaining_lanes: List[_AviatorLane] = []
        for lane in self.aviator_lanes:
            while lane.stages and lane.stages[0].run.completed:
                head = lane.stages.popleft()
                run = head.run
                self._record_outcome(run)
                if run.passes:
                    if self.merged_mainline != run.mainline:
                        # Equal exact-tree commit authority: another lane advanced mainline,
                        # so this stale passing run and its lane suffix must be invalidated & rechecked.
                        self._discard(run, invalidated=True)
                        self.stale_rechecks += 1
                        for descendant in lane.stages:
                            self._discard(descendant.run, invalidated=True)
                        lane.stages.clear()
                        break
                    self._merge(run, head.members)
                    lane.queue.pop(0)
                else:
                    self._reject(head.members[0])
                    lane.queue.pop(0)
                    for descendant in lane.stages:
                        self._discard(descendant.run, invalidated=True)
                    lane.stages.clear()
                    break
            if lane.queue:
                lane.targets = set().union(*(self.dir_targets[n] for n in lane.queue))
                remaining_lanes.append(lane)
        self.aviator_lanes = remaining_lanes

    # ---- Shared outcome accounting & strict exact-tree merge authority ----
    def _record_outcome(self, run: _Run) -> None:
        recovery = run.job.recovery
        recovery.rounds = max(recovery.rounds, run.job.round)
        if not run.passes:
            if not recovery.observed_failure:
                self.recoveries.append(recovery)
            recovery.observed_failure = True
            recovery.faulty |= bool(run.culprits)
            if run.job.is_root and run.job.failures == 0 and run.job.round == 0:
                self.failed_root_batches += 1
                self.failed_root_members_total += len(run.job.members)
                n_culp = len(run.culprits)
                self.failed_root_culprits_total += n_culp
                if n_culp == 1:
                    self.failed_root_single_culprit += 1
                elif n_culp >= 2:
                    self.failed_root_multi_culprit += 1

    def _reject(self, number: int) -> None:
        self.rejected.add(number)
        if number in self.truth.defective_prs:
            self.rejected_defective += 1
        elif self.truth.persistent_culprits((number,), self.merged_mainline):
            self.rejected_persistent_conflict += 1
        else:
            self.false_rejections += 1

    def _merge(self, run: _Run, members: _Members) -> None:
        tested_tree = run.mainline | frozenset(run.job.members)
        target_tree = frozenset(self.merged_mainline) | frozenset(members)
        if (
            not run.completed
            or not run.passes
            or not set(members).issubset(run.job.members)
            or not run.mainline.issubset(self.merged_mainline)
            or tested_tree != target_tree
        ):
            raise RuntimeError("Merge attempted without exact-tree CI verification")
        self.escaped += len(self.truth.culprits(members, self.merged_mainline))
        for number in members:
            if number in self.merged or number in self.rejected:
                raise RuntimeError("Pull request was already resolved")
            self.merged[number] = self.now
            self.merged_mainline.add(number)

    def _predictor_sets(self) -> Dict[int, FrozenSet]:
        if self.strategy == "Aviator-DirAffected":
            return self.dir_targets
        return self.cones

    def _metrics(self) -> RunMetrics:
        merged = len(self.merged)
        duration_min = self.now - min(self.arrivals.values(), default=0.0)
        duration = duration_min / 60
        clean_total = len(self.records) - len(self.truth.defective_prs)
        latencies = sorted(time - self.arrivals[number] for number, time in self.merged.items())
        fault_rounds = [
            item.rounds for item in self.recoveries if item.faulty and item.observed_failure
        ]
        total_conf = len(self.truth.conflicting_pairs)
        pred_sets = self._predictor_sets()
        edge_count = sum(
            not pred_sets[a].isdisjoint(pred_sets[b]) for a, b in combinations(pred_sets, 2)
        )
        pair_count = len(pred_sets) * (len(pred_sets) - 1) // 2
        if self.strategy in ("SSMQ-FilteredCone", "Aviator-DirAffected"):
            on_pred = sum(
                1 for a, b in self.truth.conflicting_pairs if not pred_sets[a].isdisjoint(pred_sets[b])
            )
            off_pred = total_conf - on_pred
        else:
            on_pred = len(self.truth.on_graph_pairs)
            off_pred = len(self.truth.off_graph_pairs)
        realized_rec = (on_pred / total_conf) if total_conf else None
        realized_prec = (on_pred / edge_count) if edge_count else None
        return RunMetrics(
            strategy=self.strategy,
            seed=self.seed,
            throughput_prs_per_hr=merged / duration if duration else 0.0,
            p50_latency_min=_percentile(latencies, 0.50),
            p95_latency_min=_percentile(latencies, 0.95),
            ci_minutes_per_merged_pr=self.ci_time / merged if merged else 0.0,
            invalidation_rate=self.invalidated_runs / self.total_runs if self.total_runs else 0.0,
            mean_blame_rounds=fsum(fault_rounds) / len(fault_rounds) if fault_rounds else 0.0,
            merged_prs=merged,
            rejected_prs=len(self.rejected),
            escaped_defects=self.escaped,
            total_ci_runs=self.total_runs,
            wasted_ci_runs=self.wasted_runs,
            sim_duration_hr=duration,
            clean_prs_total=clean_total,
            clean_prs_merged=len(self.merged_mainline - self.truth.defective_prs),
            false_rejection_rate=self.false_rejections / clean_total if clean_total else 0.0,
            mean_batch_size=self.total_batch_members / self.total_runs if self.total_runs else 0.0,
            runner_utilization=self.ci_time / (self.config.workers * duration_min) if duration_min else 0.0,
            off_graph_conflicts_total=off_pred,
            on_graph_conflicts_total=on_pred,
            root_batches_dispatched=self.root_batches_dispatched,
            mean_root_batch_size=(
                self.root_batch_members_total / self.root_batches_dispatched
                if self.root_batches_dispatched else 0.0
            ),
            failed_root_batches=self.failed_root_batches,
            failed_root_single_culprit=self.failed_root_single_culprit,
            failed_root_multi_culprit=self.failed_root_multi_culprit,
            mean_failed_root_size=(
                self.failed_root_members_total / self.failed_root_batches
                if self.failed_root_batches else 0.0
            ),
            mean_culprits_per_failed_root=(
                self.failed_root_culprits_total / self.failed_root_batches
                if self.failed_root_batches else 0.0
            ),
            blame_attempts=self.blame_attempts,
            blame_one_step_hits=self.blame_one_step_hits,
            rejected_defective=self.rejected_defective,
            rejected_persistent_conflict=self.rejected_persistent_conflict,
            rejected_false_flaky=self.false_rejections,
            realized_graph_recall=realized_rec,
            realized_graph_precision=realized_prec,
            observed_graph_edge_density=edge_count / pair_count if pair_count else 0.0,
            bridge_wait_min_total=self.bridge_wait,
            canceled_ci_minutes=self.canceled_ci,
            stale_rechecks=self.stale_rechecks,
        )


def simulate_v8_queue(
    graph: DependencyGraph,
    prs: Iterable[PRRecord],
    strategy: str,
    config: SimConfig,
    seed: int,
    filtered_graph: Optional[FilteredConeGraph] = None,
) -> RunMetrics:
    """Execute one Batch 2 baseline or v7 control under equal exact-tree semantics."""
    if strategy not in ALL_V8_STRATEGIES:
        raise ValueError(f"Unknown v8 strategy: {strategy}")
    records = list(prs)
    if strategy in V7_CONTROL_STRATEGIES:
        return simulate_queue(graph, records, strategy, config, seed)
    _validate(config, records)
    return _V8Simulation(graph, records, strategy, config, seed, filtered_graph=filtered_graph).run()
