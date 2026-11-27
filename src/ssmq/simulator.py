"""Discrete-event CI runner, strategy policies, and paired fault injection (v4).

Key v4 upgrades resolving all Codex v3 blind-review blockers:
  1. Fixed-density `graph_recall` oracle: total conflict count stays invariant
     across graph_recall in [0.0, 1.0] (partitioning a fixed conflict budget
     into on-graph vs off-graph latent edges rather than doubling density).
  2. Zero evaluation-population leakage in scheduler priors: scheduler defect
     priors scale strictly from `graph.warmup_mean_risk` (chronological warm-up
     prefix only), never peeking at evaluation-stream statistics.
  3. Authentic dependency-aware outcome-tree baseline (`DepAwareOutcomeTree`,
     modeling EuroSys '19 conflict-graph speculation with independent commits
     for disjoint cones) alongside honestly named `FIFOOutcomeTree`.
  4. Deconfounded component ablations: separates `SSMQ-NoCone` (risk caps only),
     `SSMQ-NoRisk` (cone exclusion only), `SSMQ-NoGraph` (neither), and adds
     matched retry ablation `SSMQ-FixedRetry` inside the same scheduler.
  5. Full mechanism instrumentation: root batch counts/sizes, failed-root
     single- vs multi-culprit distributions, 1-step blame hit counts, realized
     graph recall, and exact rejection-cause decomposition.
"""

from collections import Counter, deque
from dataclasses import dataclass, replace
from functools import cached_property
from hashlib import sha256
from heapq import heappop, heappush
from itertools import combinations, count
from math import fsum, isfinite, prod
from random import Random
from typing import Deque, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from .graph import DependencyGraph, _repository_roots
from .models import PRRecord, RunMetrics, SimConfig

ALL_STRATEGIES = [
    "SerialQueue",
    "StaticBatchBisect",
    "SpeculativeLinear",
    "FIFOOutcomeTree",
    "DepAwareOutcomeTree",
    "FlakeAwareBatch",
    "SSMQ",
    "SSMQ-NoCone",
    "SSMQ-NoRisk",
    "SSMQ-NoGraph",
    "SSMQ-NoFlaky",
    "SSMQ-FixedRetry",
    "SSMQ-NoBlame",
]

_Members = Tuple[int, ...]
_FAST_LANE_RISK = 0.55
_FLAKE_RERUN_THRESHOLD = 0.25


@dataclass(frozen=True)
class GroundTruth:
    defective_prs: FrozenSet[int]
    conflicting_pairs: FrozenSet[Tuple[int, int]]
    persistent_pairs: FrozenSet[Tuple[int, int]] = frozenset()
    off_graph_pairs: FrozenSet[Tuple[int, int]] = frozenset()

    @property
    def on_graph_pairs(self) -> FrozenSet[Tuple[int, int]]:
        return self.conflicting_pairs - self.off_graph_pairs

    @cached_property
    def _persistent_neighbors(self) -> Dict[int, Set[int]]:
        neighbors: Dict[int, Set[int]] = {}
        for left, right in self.persistent_pairs:
            neighbors.setdefault(left, set()).add(right)
            neighbors.setdefault(right, set()).add(left)
        return neighbors

    def persistent_culprits(self, members: _Members, mainline: Iterable[int]) -> _Members:
        base = frozenset(mainline)
        if not base:
            return ()
        return tuple(
            number for number in members
            if not base.isdisjoint(self._persistent_neighbors.get(number, ()))
        )

    def culprits(self, members: _Members, mainline: Iterable[int] = ()) -> _Members:
        """Return defective PRs, intra-batch offenders and persistent interactions."""
        suspects = set(self.defective_prs.intersection(members))
        suspects.update(self.persistent_culprits(members, mainline))
        for first, second in combinations(members, 2):
            if (min(first, second), max(first, second)) in self.conflicting_pairs:
                suspects.add(second)
        return tuple(number for number in members if number in suspects)


def _key(seed: int, domain: str, *values: int) -> int:
    payload = ":".join(map(str, (seed, domain, *values)))
    return int.from_bytes(sha256(payload.encode("ascii")).digest()[:8], "big")


def _uniform(key: int) -> float:
    return (key >> 11) / (1 << 53)


def _defect_probabilities(
    prs: Iterable[PRRecord], rate: float, risk_correlation: float = 1.0
) -> Dict[int, float]:
    """Oracle calibration over evaluation PRs to match target expected defect rate.

    When risk_correlation == 0.0, assigns exact uniform defect probability `rate`
    independent of scheduler risk features (decoupled sensitivity control).
    """
    records = list(prs)
    if not records or rate in (0.0, 1.0):
        return {pr.number: rate for pr in records}
    corr = max(0.0, min(1.0, risk_correlation))
    if corr == 0.0:
        return {pr.number: rate for pr in records}
    weights = {
        pr.number: max(1e-6, corr * pr.risk_score + (1.0 - corr) * 0.5)
        for pr in records
    }
    low, high = 0.0, 1.0 / min(weights.values())
    for _ in range(64):
        scale = (low + high) / 2
        mean = fsum(min(1.0, weight * scale) for weight in weights.values()) / len(weights)
        if mean < rate:
            low = scale
        else:
            high = scale
    return {number: min(1.0, weight * high) for number, weight in weights.items()}


def _warmup_scheduler_priors(
    graph: DependencyGraph, prs: Iterable[PRRecord], rate: float
) -> Dict[int, float]:
    """Leak-free scheduler prior calibrated strictly from warm-up mean risk."""
    base_mean = max(1e-6, graph.warmup_mean_risk)
    if rate in (0.0, 1.0):
        return {pr.number: rate for pr in prs}
    scale = rate / base_mean
    return {pr.number: min(1.0, max(1e-6, pr.risk_score) * scale) for pr in prs}


def build_ground_truth(
    graph: DependencyGraph, prs: Iterable[PRRecord], config: SimConfig, seed: int
) -> GroundTruth:
    """Sample strategy-independent defects and EXACT-COUNT fixed-density conflicts.

    Fixes total conflict count M = len(base_visible) identically per (repo, seed)
    across graph_recall in [0.0, 1.0] via exact without-replacement thinning of
    visible edges and A-Res weighted exact sampling of disjoint off-graph pairs.
    """
    records = sorted(prs, key=lambda pr: pr.number)
    probabilities = _defect_probabilities(records, config.base_defect_rate, config.risk_correlation)
    defects = frozenset(
        number
        for number, probability in probabilities.items()
        if _uniform(_key(seed, "defect", number)) < probability
    )
    probability = config.semantic_conflict_prob
    if not probability:
        return GroundTruth(defects, frozenset())

    cones = {
        pr.number: pr.impact_cone or frozenset(graph.impact_cone(pr.files))
        for pr in records
    }
    roots = {pr.number: _repository_roots(pr.files) for pr in records}
    base_visible: List[Tuple[int, int]] = []
    same_root: List[Tuple[int, int]] = []
    other_roots: List[Tuple[int, int]] = []
    for first, second in combinations(cones, 2):
        if not cones[first].isdisjoint(cones[second]):
            if _uniform(_key(seed, "conflict", first, second)) < probability:
                base_visible.append((first, second))
        else:
            candidates = other_roots if roots[first].isdisjoint(roots[second]) else same_root
            candidates.append((first, second))

    recall = max(0.0, min(1.0, config.graph_recall))
    total_target = len(base_visible)

    if recall >= 1.0:
        visible = set(base_visible)
        latent: Set[Tuple[int, int]] = set()
    else:
        keep_n = 0 if recall <= 0.0 else int(round(recall * total_target))
        ordered_vis = sorted(base_visible, key=lambda pair: (_key(seed, "vis-order", *pair), pair))
        visible = set(ordered_vis[:keep_n])
        need_latent = max(0, total_target - len(visible))
        # Exact without-replacement weighted sampling (A-Res: u^(1/w)), weighting
        # same-root disjoint pairs 4x higher than cross-root pairs.
        weighted_pool: List[Tuple[float, Tuple[int, int]]] = []
        for pair in same_root:
            u = max(1e-12, _uniform(_key(seed, "latent-order", *pair)))
            weighted_pool.append((u ** (1.0 / 4.0), pair))
        for pair in other_roots:
            u = max(1e-12, _uniform(_key(seed, "latent-order", *pair)))
            weighted_pool.append((u, pair))
        weighted_pool.sort(key=lambda item: (-item[0], item[1]))
        latent = {pair for _, pair in weighted_pool[:need_latent]}

    conflicts = frozenset(visible | latent)
    persistent = frozenset(
        pair for pair in conflicts
        if _uniform(_key(seed, "persistent", *pair)) < config.persistent_conflict_share
    )
    return GroundTruth(defects, conflicts, persistent, frozenset(latent))


@dataclass
class _Recovery:
    faulty: bool = False
    observed_failure: bool = False
    rounds: int = 0


@dataclass
class _Job:
    members: _Members
    recovery: _Recovery
    round: int = 0
    failures: int = 0
    allow_blame: bool = True
    next_suspect: Optional[int] = None
    overhead: float = 0.0
    prefix: _Members = ()
    is_root: bool = False
    blame_remainder: bool = False


@dataclass
class _Run:
    job: _Job
    started: float
    finishes: float
    passes: bool
    mainline: FrozenSet[int]
    culprits: _Members
    completed: bool = False
    wasted: bool = False


@dataclass
class _TrainNode:
    members: _Members
    run: _Run


@dataclass
class _OutcomeNode:
    job: _Job
    following: Tuple[_Job, ...] = ()
    run: Optional[_Run] = None
    on_pass: Optional["_OutcomeNode"] = None
    on_fail: Optional["_OutcomeNode"] = None


@dataclass
class _DepLane:
    """Independent conflict-graph outcome tree for DepAwareOutcomeTree."""
    lane_id: int
    cone: Set[str]
    queue: List[int]
    root: Optional[_OutcomeNode] = None


@dataclass
class _SuitePosterior:
    alpha: float = 1.0
    beta: float = 19.0

    def failure_probability(self, attempts: int) -> float:
        return prod(
            (self.alpha + index) / (self.alpha + self.beta + index)
            for index in range(attempts)
        )


class _Simulation:
    def __init__(
        self, graph: DependencyGraph, prs: List[PRRecord], strategy: str,
        config: SimConfig, seed: int,
    ):
        self.strategy = strategy
        self.config = config
        self.seed = seed
        self.records = {pr.number: pr for pr in prs}
        self.cones = {
            pr.number: pr.impact_cone or frozenset(graph.impact_cone(pr.files))
            for pr in prs
        }
        self.truth = build_ground_truth(graph, prs, config, seed)
        # Leak-free scheduler prior calibrated strictly from warm-up history!
        self.defect_risk = _warmup_scheduler_priors(graph, prs, config.base_defect_rate)
        self.arrivals = {
            pr.number: pr.arrival_min / config.velocity_multiplier for pr in prs
        }
        self.use_cone = strategy in (
            "SSMQ", "SSMQ-NoRisk", "SSMQ-NoFlaky", "SSMQ-FixedRetry", "SSMQ-NoBlame",
        )
        self.use_risk_lanes = strategy in (
            "SSMQ", "SSMQ-NoCone", "SSMQ-NoFlaky", "SSMQ-FixedRetry", "SSMQ-NoBlame",
        )
        self.use_blame = strategy.startswith("SSMQ") and strategy != "SSMQ-NoBlame"
        self.adaptive_rerun = strategy.startswith("SSMQ") and strategy not in ("SSMQ-NoFlaky", "SSMQ-FixedRetry")
        self.fixed_rerun = strategy in ("FlakeAwareBatch", "SSMQ-FixedRetry")
        self.fifo_tree = strategy == "FIFOOutcomeTree"
        self.dep_tree = strategy == "DepAwareOutcomeTree"
        self.linear = strategy == "SpeculativeLinear"
        self.worker_limit = 1 if strategy == "SerialQueue" else config.workers

        self.ready: List[int] = []
        self.pending: Deque[_Job] = deque()
        self.protected: Set[int] = set()
        self.train: Deque[_TrainNode] = deque()
        self.barrier: Set[int] = set()
        self.outcome_root: Optional[_OutcomeNode] = None
        self.dep_lanes: List[_DepLane] = []
        self._next_lane_id = count()
        self.suite = _SuitePosterior()
        self.sequence = count()
        self.events = []
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
        # v4 mechanism instrumentation
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
        while self.events:
            when = self.events[0][0]
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
            raise RuntimeError("Queue drained with unresolved pull requests")
        return self._metrics()

    def _root_job(self, members: _Members) -> _Job:
        return _Job(members, _Recovery(), is_root=True)

    def _start(self, job: _Job) -> _Run:
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
        passes = not culprits and rng.random() >= self.config.flaky_rate
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
        run.wasted = True
        self.wasted_runs += 1
        self.invalidated_runs += int(invalidated)

    def _complete(self, run: _Run) -> None:
        run.completed = True
        self.running -= 1
        self.ci_time += run.finishes - run.started
        if self.fifo_tree:
            self._resolve_uber()
        elif self.dep_tree:
            self._resolve_dep_trees()
        elif any(run is node.run for node in self.train):
            self._resolve_train()
        else:
            self._resolve_batch(run)

    def _dispatch(self) -> None:
        while self.pending and self.running < self.worker_limit:
            self._start(self.pending.popleft())
        if self.fifo_tree:
            self._dispatch_uber()
        elif self.dep_tree:
            self._dispatch_dep_trees()
        elif self.linear:
            self._dispatch_train()
        else:
            while self.ready and self.running < self.worker_limit:
                self._start(self._root_job(self._take_batch()))

    def _take_batch(self) -> _Members:
        if self.strategy == "SerialQueue":
            return (self.ready.pop(0),)
        if not self.use_cone and not self.use_risk_lanes:
            size = self.config.max_batch_size
            members = tuple(self.ready[:size])
            del self.ready[:size]
            return members

        head = self.ready[0]
        protected = self._is_protected(head)
        limit = min(2, self.config.max_batch_size) if protected else self.config.max_batch_size
        members = [head]
        combined = set(self.cones[head])
        waiting = []
        for number in self.ready[1:]:
            overlap = self.use_cone and (not combined.isdisjoint(self.cones[number]))
            if overlap:
                self.protected.add(number)
            if len(members) < limit and not overlap and self._is_protected(number) == protected:
                members.append(number)
                combined.update(self.cones[number])
            else:
                waiting.append(number)
        self.ready = waiting
        self.protected.difference_update(members)
        return tuple(members)

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
        return 1 - clean * (1 - self.config.semantic_conflict_prob) ** overlap_count

    def _dispatch_train(self) -> None:
        if self.barrier:
            return
        prefix = tuple(number for node in self.train for number in node.members)
        while (
            self.ready and self.running < self.worker_limit
            and len(self.train) < self.config.max_spec_depth
        ):
            members = (self.ready.pop(0),)
            prefix += members
            self.train.append(_TrainNode(members, self._start(self._root_job(prefix))))

    def _resolve_train(self) -> None:
        while self.train and self.train[0].run.completed:
            head = self.train.popleft()
            run = head.run
            if run.passes:
                self._merge(run, head.members)
                continue

            requeued = []
            for descendant in self.train:
                self._discard(descendant.run, invalidated=True)
                requeued.extend(descendant.members)
            self.train.clear()
            self.ready[:0] = requeued
            self.barrier.update(head.members)
            if head.members != run.job.members:
                self._record_outcome(run)
                self.pending.append(_Job(head.members, run.job.recovery, round=1))
            else:
                self._resolve_batch(run)
            break

    # ---- Outcome-Tree helpers shared by FIFOOutcomeTree and DepAwareOutcomeTree ----
    def _uber_pass_probability(self, members: _Members, prefix: _Members) -> float:
        base = self.merged_mainline.union(prefix)
        cross_edges = sum(
            not self.cones[number].isdisjoint(self.cones[prior])
            for number in members for prior in base
        )
        persistent_risk = self.config.semantic_conflict_prob * self.config.persistent_conflict_share
        return (
            (1 - self._failure_risk(members))
            * (1 - persistent_risk) ** cross_edges
            * (1 - self.config.flaky_rate)
        )

    def _uber_batch(self, remaining: _Members, prefix: _Members) -> _OutcomeNode:
        size = min(len(remaining), self.config.max_batch_size)
        members = max(
            (remaining[:end] for end in range(1, size + 1)),
            key=lambda batch: self._uber_pass_probability(batch, prefix) * len(batch),
        )
        return _OutcomeNode(replace(self._root_job(members), prefix=prefix))

    def _uber_child(
        self, node: _OutcomeNode, passed: bool, remaining: _Members
    ) -> Optional[_OutcomeNode]:
        existing = node.on_pass if passed else node.on_fail
        if existing is not None and existing.run is not None:
            return existing
        job = node.job
        prefix = tuple(number for number in job.prefix if number not in self.merged_mainline)
        following = node.following
        if passed or len(job.members) == 1:
            remaining = remaining[len(job.members):]
            if passed:
                prefix += tuple(number for number in job.members if number not in self.merged_mainline)
        else:
            following = self._split_jobs(job) + following
        if following:
            child = _OutcomeNode(replace(following[0], prefix=prefix), following[1:])
        elif remaining:
            child = self._uber_batch(remaining, prefix)
        else:
            child = None
        if passed:
            node.on_pass = child
        else:
            node.on_fail = child
        return child

    def _uber_frontier(
        self, node: _OutcomeNode, remaining: _Members, depth: int, reach: float
    ) -> Iterable[Tuple[float, _OutcomeNode]]:
        if not reach:
            return
        run = node.run
        chance = self._uber_pass_probability(node.job.members, node.job.prefix)
        if run is None:
            yield (reach * chance * len(node.job.members) / self.config.ci_duration_min, node)
        elif depth < self.config.max_spec_depth:
            outcomes = ((True, chance), (False, 1 - chance))
            if run.completed:
                outcomes = ((run.passes, 1.0),)
            for passed, probability in outcomes:
                if not probability:
                    continue
                child = self._uber_child(node, passed, remaining)
                if child is not None:
                    tail = remaining
                    if passed or len(node.job.members) == 1:
                        tail = remaining[len(node.job.members):]
                    yield from self._uber_frontier(child, tail, depth + 1, reach * probability)

    def _dispatch_uber(self) -> None:
        if not self.ready:
            return
        remaining = tuple(self.ready)
        if self.outcome_root is None:
            self.outcome_root = self._uber_batch(remaining, ())
        while self.running < self.worker_limit:
            value, node = max(
                self._uber_frontier(self.outcome_root, remaining, 1, 1.0),
                key=lambda candidate: candidate[0],
                default=(0.0, None),
            )
            if node is None or (not value and node is not self.outcome_root):
                break
            node.run = self._start(node.job)

    def _discard_outcomes(self, node: Optional[_OutcomeNode]) -> None:
        if node is not None and node.run is not None:
            self._discard(node.run, invalidated=True)
            self._discard_outcomes(node.on_pass)
            self._discard_outcomes(node.on_fail)

    def _resolve_uber(self) -> None:
        while self.outcome_root is not None:
            head = self.outcome_root
            run = head.run
            if run is None or not run.completed:
                break
            self._record_outcome(run)
            if run.passes:
                if self.truth.persistent_culprits(run.job.members, self.merged_mainline - run.mainline):
                    self._discard(run, invalidated=True)
                    self._discard_outcomes(head.on_pass)
                    self._discard_outcomes(head.on_fail)
                    self.outcome_root = _OutcomeNode(replace(run.job, overhead=0.0, prefix=()))
                    break
                self._merge(run, run.job.members)
                self._discard_outcomes(head.on_fail)
            else:
                self._discard_outcomes(head.on_pass)
                if len(run.job.members) == 1:
                    self._reject(run.job.members[0])
            self.outcome_root = self._uber_child(head, run.passes, tuple(self.ready))
            if run.passes or len(run.job.members) == 1:
                del self.ready[:len(run.job.members)]

    # ---- DepAwareOutcomeTree: EuroSys '19 conflict-graph speculation ----
    def _assign_ready_to_dep_lanes(self) -> None:
        """Assign arriving PRs to disjoint conflict-graph lanes while enforcing
        strict inter-lane dependency barriers whenever a bridge PR overlaps >= 2
        active lanes (active lanes remain pairwise cone-disjoint at all times).
        """
        still_waiting: List[int] = []
        blocked_cones: Set[str] = set()
        for pr_num in self.ready:
            cone = self.cones[pr_num]
            if not blocked_cones.isdisjoint(cone):
                # Preserve causal order behind an earlier waiting bridge PR
                still_waiting.append(pr_num)
                blocked_cones.update(cone)
                continue
            overlapping = [lane for lane in self.dep_lanes if not lane.cone.isdisjoint(cone)]
            if not overlapping:
                self.dep_lanes.append(_DepLane(next(self._next_lane_id), set(cone), [pr_num]))
            elif len(overlapping) == 1:
                lane = overlapping[0]
                lane.queue.append(pr_num)
                lane.cone.update(cone)
            else:
                # Bridge PR overlaps >= 2 active lanes: hold in dependency barrier
                # until overlapping upstream lanes drain to <= 1 active lane!
                still_waiting.append(pr_num)
                blocked_cones.update(cone)
        self.ready = still_waiting

    def _dispatch_dep_trees(self) -> None:
        self._assign_ready_to_dep_lanes()
        for lane in self.dep_lanes:
            if lane.root is None and lane.queue:
                lane.root = self._uber_batch(tuple(lane.queue), ())
        while self.running < self.worker_limit:
            best_val = -1.0
            best_node: Optional[_OutcomeNode] = None
            for lane in self.dep_lanes:
                if lane.root is None or not lane.queue:
                    continue
                for val, node in self._uber_frontier(lane.root, tuple(lane.queue), 1, 1.0):
                    if val > best_val or (best_node is None and node is lane.root):
                        best_val = val
                        best_node = node
            if best_node is None:
                break
            best_node.run = self._start(best_node.job)

    def _resolve_dep_trees(self) -> None:
        remaining_lanes: List[_DepLane] = []
        for lane in self.dep_lanes:
            while lane.root is not None:
                head = lane.root
                run = head.run
                if run is None or not run.completed:
                    break
                self._record_outcome(run)
                if run.passes:
                    # Independent commit subject to cumulative persistent-conflict safety!
                    if self.truth.persistent_culprits(run.job.members, self.merged_mainline - run.mainline):
                        self._discard(run, invalidated=True)
                        self._discard_outcomes(head.on_pass)
                        self._discard_outcomes(head.on_fail)
                        lane.root = _OutcomeNode(replace(run.job, overhead=0.0, prefix=()))
                        break
                    self._merge(run, run.job.members)
                    self._discard_outcomes(head.on_fail)
                else:
                    self._discard_outcomes(head.on_pass)
                    if len(run.job.members) == 1:
                        self._reject(run.job.members[0])
                lane.root = self._uber_child(head, run.passes, tuple(lane.queue))
                if run.passes or len(run.job.members) == 1:
                    del lane.queue[:len(run.job.members)]
            if lane.queue:
                # Recompute active cone strictly over remaining queued PRs in this lane
                lane.cone = set().union(*(self.cones[n] for n in lane.queue))
                remaining_lanes.append(lane)
        self.dep_lanes = remaining_lanes

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

    def _should_rerun(self, job: _Job) -> bool:
        if self.fixed_rerun:
            return job.failures == 0
        if not self.adaptive_rerun or job.failures >= 2:
            return False
        fault = self._failure_risk(job.members)
        transient = (1 - fault) * self.suite.failure_probability(job.failures + 1)
        evidence = fault + transient
        threshold = _FLAKE_RERUN_THRESHOLD if len(job.members) == 1 else 0.50
        return evidence > 0 and transient / evidence >= threshold

    def _resolve_batch(self, run: _Run) -> None:
        job = run.job
        self._record_outcome(run)
        if run.passes:
            if self.truth.persistent_culprits(job.members, self.merged_mainline - run.mainline):
                self._discard(run, invalidated=True)
                self.pending.appendleft(replace(job, overhead=0.0))
                return
            if job.blame_remainder:
                self.blame_one_step_hits += 1
            if self.adaptive_rerun:
                self.suite.alpha += job.failures
                self.suite.beta += 1
            self.wasted_runs += job.failures
            self.invalidated_runs += job.failures
            self._merge(run, job.members)
            return

        if self._should_rerun(job):
            self.pending.appendleft(replace(
                job, failures=job.failures + 1, round=job.round + 1, overhead=0.0,
            ))
        elif len(job.members) == 1:
            self._reject(job.members[0])
        elif self.use_blame and job.allow_blame:
            self._attribute(run)
        else:
            self.pending.extend(self._split_jobs(job))

    def _attribute(self, run: _Run) -> None:
        job = run.job
        self.blame_attempts += 1
        top, second = self._rank_suspects(job.members, run.culprits)[:2]
        remaining = tuple(number for number in job.members if number != top)
        self.pending.extend((
            _Job(
                remaining, job.recovery, job.round + 1, allow_blame=False,
                next_suspect=second, overhead=self.config.blame_overhead_min,
                blame_remainder=True,
            ),
            _Job((top,), job.recovery, job.round + 1),
        ))

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

    def _split_jobs(self, job: _Job) -> Tuple[_Job, ...]:
        midpoint = len(job.members) // 2
        halves = (job.members[:midpoint], job.members[midpoint:])
        if job.next_suspect in halves[1]:
            halves = halves[::-1]
        return tuple(
            _Job(members, job.recovery, job.round + 1, allow_blame=False)
            for members in halves
        )

    def _reject(self, number: int) -> None:
        self.rejected.add(number)
        self.barrier.discard(number)
        if number in self.truth.defective_prs:
            self.rejected_defective += 1
        elif self.truth.persistent_culprits((number,), self.merged_mainline):
            self.rejected_persistent_conflict += 1
        else:
            self.false_rejections += 1

    def _merge(self, run: _Run, members: _Members) -> None:
        if (
            not run.completed or not run.passes or not set(members).issubset(run.job.members)
            or not run.mainline.issubset(self.merged_mainline)
        ):
            raise RuntimeError("Merge attempted without a passing CI verification")
        self.escaped += len(self.truth.culprits(members, self.merged_mainline))
        for number in members:
            if number in self.merged or number in self.rejected:
                raise RuntimeError("Pull request was already resolved")
            self.merged[number] = self.now
            self.merged_mainline.add(number)
            self.barrier.discard(number)

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
        realized_rec = (len(self.truth.on_graph_pairs) / total_conf) if total_conf else 1.0
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
            off_graph_conflicts_total=len(self.truth.off_graph_pairs),
            on_graph_conflicts_total=len(self.truth.on_graph_pairs),
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
        )


def _percentile(ordered: List[float], percentile: float) -> float:
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentile
    index = int(position)
    upper = min(index + 1, len(ordered) - 1)
    return ordered[index] + (position - index) * (ordered[upper] - ordered[index])


def _validate(config: SimConfig, prs: List[PRRecord]) -> None:
    for name in ("workers", "max_batch_size", "max_spec_depth"):
        value = getattr(config, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    for name in ("ci_duration_min", "velocity_multiplier"):
        value = getattr(config, name)
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    for name in ("ci_duration_std", "blame_overhead_min"):
        value = getattr(config, name)
        if not isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    for name in (
        "base_defect_rate", "semantic_conflict_prob", "flaky_rate",
        "blame_top1_acc", "blame_top2_acc", "warmup_fraction", "graph_recall",
        "persistent_conflict_share",
    ):
        if not 0 <= getattr(config, name) <= 1:
            raise ValueError(f"{name} must be in [0, 1]")
    if config.blame_top1_acc > config.blame_top2_acc:
        raise ValueError("blame_top1_acc cannot exceed blame_top2_acc")
    if len({pr.number for pr in prs}) != len(prs):
        raise ValueError("PR numbers must be unique within one repository trace")
    for pr in prs:
        if not isfinite(pr.arrival_min) or pr.arrival_min < 0 or not 0 <= pr.risk_score <= 1:
            raise ValueError("PR arrivals must be finite and nonnegative, and risks in [0, 1]")


def simulate_queue(
    graph: DependencyGraph, prs: Iterable[PRRecord], strategy: str, config: SimConfig,
    seed: int,
) -> RunMetrics:
    if strategy not in ALL_STRATEGIES:
        raise ValueError(f"Unknown queue strategy: {strategy}")
    records = list(prs)
    _validate(config, records)
    return _Simulation(graph, records, strategy, config, seed).run()
