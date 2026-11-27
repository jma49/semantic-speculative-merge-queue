"""Discrete-event CI runner, strategy policies, and paired fault injection."""

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
    "UberSubmitQueue",
    "FlakeAwareBatch",
    "SSMQ",
    "SSMQ-NoGraph",
    "SSMQ-NoFlaky",
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


def _defect_probabilities(prs: Iterable[PRRecord], rate: float) -> Dict[int, float]:
    # A small floor lets zero-risk records participate even at extreme rates.
    weights = {pr.number: max(1e-6, pr.risk_score) for pr in prs}
    if not weights or rate in (0.0, 1.0):
        return dict.fromkeys(weights, rate)
    low, high = 0.0, 1.0 / min(weights.values())
    for _ in range(64):
        scale = (low + high) / 2
        mean = fsum(min(1.0, weight * scale) for weight in weights.values()) / len(weights)
        if mean < rate:
            low = scale
        else:
            high = scale
    return {number: min(1.0, weight * high) for number, weight in weights.items()}


def build_ground_truth(
    graph: DependencyGraph, prs: Iterable[PRRecord], config: SimConfig, seed: int
) -> GroundTruth:
    """Sample strategy-independent defects and observed/latent conflict labels."""
    records = sorted(prs, key=lambda pr: pr.number)
    probabilities = _defect_probabilities(records, config.base_defect_rate)
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
    visible = set()
    same_root = []
    other_roots = []
    overlap_count = 0
    recall = config.graph_recall
    for first, second in combinations(cones, 2):
        if not cones[first].isdisjoint(cones[second]):
            overlap_count += 1
            if recall and _uniform(_key(seed, "conflict", first, second)) < probability:
                visible.add((first, second))
        elif recall < 1:
            candidates = other_roots if roots[first].isdisjoint(roots[second]) else same_root
            candidates.append((first, second))

    latent = set()
    if recall < 1 and (same_root or other_roots):
        if recall:
            # Anchor the expected latent count to the realized visible count.
            # A sparse budget still permits latent edges in an overlap-free trace.
            reference = len(visible) or probability * (overlap_count or len(records) / 2)
            target = reference * ((1 - recall) / recall)
        else:
            # Zero recall is an off-graph-only control at the configured density.
            target = probability * (len(same_root) + len(other_roots))
        # Give same-root pairs four times the probability until they saturate.
        shared_probability = min(1.0, target / (len(same_root) + 0.25 * len(other_roots)))
        random_probability = (
            min(1.0, (target - len(same_root) * shared_probability) / len(other_roots))
            if other_roots else 0.0
        )
        for candidates, chance in ((same_root, shared_probability), (other_roots, random_probability)):
            latent.update(
                pair for pair in candidates if _uniform(_key(seed, "latent-conflict", *pair)) < chance
            )
    conflicts = frozenset(visible | latent)
    persistent = frozenset(
        pair for pair in conflicts
        if _uniform(_key(seed, "persistent", *pair)) < config.persistent_conflict_share
    )
    return GroundTruth(defects, conflicts, persistent, frozenset(latent))


@dataclass
class _Recovery:
    # The simulator uses this label only when aggregating metrics.
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
class _SuitePosterior:
    # Each trace represents a single observed test suite. Evidence comes from
    # passing verifications and failures subsequently confirmed as transient.
    alpha: float = 1.0
    beta: float = 19.0

    def failure_probability(self, attempts: int) -> float:
        """Beta-binomial predictive probability of consecutive flaky outcomes."""
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
        self.defect_risk = _defect_probabilities(prs, config.base_defect_rate)
        self.arrivals = {
            pr.number: pr.arrival_min / config.velocity_multiplier for pr in prs
        }
        self.use_graph = strategy.startswith("SSMQ") and strategy != "SSMQ-NoGraph"
        self.use_blame = strategy.startswith("SSMQ") and strategy != "SSMQ-NoBlame"
        self.adaptive_rerun = strategy.startswith("SSMQ") and strategy != "SSMQ-NoFlaky"
        self.fixed_rerun = strategy == "FlakeAwareBatch"
        self.uber = strategy == "UberSubmitQueue"
        self.linear = strategy == "SpeculativeLinear"
        self.worker_limit = 1 if strategy == "SerialQueue" else config.workers
        self.ready: List[int] = []
        self.pending: Deque[_Job] = deque()
        self.protected = set()
        self.train: Deque[_TrainNode] = deque()
        self.barrier = set()
        self.outcome_root: Optional[_OutcomeNode] = None
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
        self.rejected = set()
        self.false_rejections = 0
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
        return _Job(members, _Recovery())

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
        if self.uber:
            self._resolve_uber()
        elif any(run is node.run for node in self.train):
            self._resolve_train()
        else:
            self._resolve_batch(run)

    def _dispatch(self) -> None:
        while self.pending and self.running < self.worker_limit:
            self._start(self.pending.popleft())
        if self.uber:
            self._dispatch_uber()
        elif self.linear:
            self._dispatch_train()
        else:
            while self.ready and self.running < self.worker_limit:
                self._start(self._root_job(self._take_batch()))

    def _take_batch(self) -> _Members:
        if self.strategy == "SerialQueue":
            return (self.ready.pop(0),)
        if not self.use_graph:
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
            overlap = not combined.isdisjoint(self.cones[number])
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
        return number in self.protected or self.records[number].risk_score >= _FAST_LANE_RISK

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
                # This failed verification included an already-merged prefix.
                # Validate the tail on its own rebased snapshot first.
                self.pending.append(_Job(head.members, run.job.recovery, round=1))
            else:
                self._resolve_batch(run)
            break

    def _uber_pass_probability(self, members: _Members, prefix: _Members) -> float:
        # Independent risk estimates use only scheduler-visible cone overlap.
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
                self._merge(run, run.job.members)
                self._discard_outcomes(head.on_fail)
            else:
                self._discard_outcomes(head.on_pass)
                if len(run.job.members) == 1:
                    self._reject(run.job.members[0])
            self.outcome_root = self._uber_child(head, run.passes, tuple(self.ready))
            if run.passes or len(run.job.members) == 1:
                del self.ready[:len(run.job.members)]

    def _record_outcome(self, run: _Run) -> None:
        recovery = run.job.recovery
        recovery.rounds = max(recovery.rounds, run.job.round)
        if not run.passes:
            if not recovery.observed_failure:
                self.recoveries.append(recovery)
            recovery.observed_failure = True
            recovery.faulty |= bool(run.culprits)

    def _should_rerun(self, job: _Job) -> bool:
        if self.fixed_rerun:
            return job.failures == 0
        if not self.adaptive_rerun or job.failures >= 2:
            return False
        fault = self._failure_risk(job.members)
        transient = (1 - fault) * self.suite.failure_probability(job.failures + 1)
        evidence = fault + transient
        # Guard singletons aggressively against false rejection; rerun multi-PR batches
        # only when posterior flakiness exceeds 50% (otherwise attribute immediately).
        threshold = _FLAKE_RERUN_THRESHOLD if len(job.members) == 1 else 0.50
        return evidence > 0 and transient / evidence >= threshold

    def _resolve_batch(self, run: _Run) -> None:
        job = run.job
        self._record_outcome(run)
        if run.passes:
            # A concurrent commit can invalidate the result if it introduces a
            # persistent interaction. Rebase and run CI on the newer mainline.
            if self.truth.persistent_culprits(job.members, self.merged_mainline - run.mainline):
                self._discard(run, invalidated=True)
                self.pending.appendleft(replace(job, overhead=0.0))
                return
            if self.adaptive_rerun:
                self.suite.alpha += job.failures
                self.suite.beta += 1
            self.wasted_runs += job.failures
            self.invalidated_runs += job.failures
            self._merge(run, job.members)
            return

        if self._should_rerun(job):
            self.pending.appendleft(replace(
                job, failures=job.failures + 1, round=job.round + 1, overhead=0.0
            ))
        elif len(job.members) == 1:
            self._reject(job.members[0])
        elif self.use_blame and job.allow_blame:
            self._attribute(run)
        else:
            self.pending.extend(self._split_jobs(job))

    def _attribute(self, run: _Run) -> None:
        job = run.job
        top, second = self._rank_suspects(job.members, run.culprits)[:2]
        remaining = tuple(number for number in job.members if number != top)
        self.pending.extend((
            _Job(
                remaining, job.recovery, job.round + 1, allow_blame=False,
                next_suspect=second, overhead=self.config.blame_overhead_min,
            ),
            # Verify the evicted PR independently. Conflicting or misattributed
            # clean PRs can still pass once isolated.
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
        if not self.truth.culprits((number,), self.merged_mainline):
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
    """Simulate one queue from its first arrival until all PRs are resolved.

    Independent jobs verify against a snapshot of cumulative mainline and rerun
    if an intervening merge introduces a persistent conflict. Speculation uses
    real mainline plus the PRs assumed to have merged along that path. Outcomes
    can commit only after their assumptions are resolved.

    Canceled runs consume CI minutes up to cancellation. Invalidations include
    discarded speculation, stale results and failures confirmed as flaky.
    Mean batch size counts members at dispatch, including canceled jobs.
    Utilization uses all configured runner slots. The false rejection rate
    divides the number of nondefective, unblocked rejections by all nondefective
    input PRs. Blame rounds count recovery depth after observed faulty runs.
    Empty denominators yield zero.
    """
    if strategy not in ALL_STRATEGIES:
        raise ValueError(f"Unknown queue strategy: {strategy}")
    records = list(prs)
    _validate(config, records)
    return _Simulation(graph, records, strategy, config, seed).run()
