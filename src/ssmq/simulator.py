"""Discrete-event CI runner, strategy policies, and paired fault injection."""

from collections import Counter, deque
from dataclasses import dataclass, replace
from hashlib import sha256
from heapq import heappop, heappush
from itertools import combinations, count
from math import fsum, isfinite, prod
from random import Random
from typing import Deque, Dict, FrozenSet, Iterable, List, Optional, Tuple

from .graph import DependencyGraph
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
_UBER_RISK_BUDGET = 0.30
_UBER_HEDGE_RISK = 0.10
_FLAKE_RERUN_THRESHOLD = 0.25


@dataclass(frozen=True)
class GroundTruth:
    defective_prs: FrozenSet[int]
    conflicting_pairs: FrozenSet[Tuple[int, int]]

    def culprits(self, members: _Members) -> _Members:
        """Return defects and the later endpoint of each intra-batch conflict."""
        suspects = set(self.defective_prs.intersection(members))
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
    """Sample strategy-independent labels, with a calibrated expected defect rate."""
    records = sorted(prs, key=lambda pr: pr.number)
    probabilities = _defect_probabilities(records, config.base_defect_rate)
    defects = frozenset(
        number
        for number, probability in probabilities.items()
        if _uniform(_key(seed, "defect", number)) < probability
    )
    cones = {
        pr.number: pr.impact_cone or frozenset(graph.impact_cone(pr.files))
        for pr in records
    }
    conflicts = frozenset(
        (first, second)
        for first, second in combinations(cones, 2)
        if not cones[first].isdisjoint(cones[second])
        and _uniform(_key(seed, "conflict", first, second)) < config.semantic_conflict_prob
    )
    return GroundTruth(defects, conflicts)


@dataclass
class _Recovery:
    # The simulator uses this label only when aggregating metrics.
    faulty: bool
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


@dataclass
class _Run:
    job: _Job
    started: float
    finishes: float
    passes: bool
    completed: bool = False
    wasted: bool = False


@dataclass
class _TrainNode:
    members: _Members
    run: _Run


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
        self.speculative = self.uber or strategy == "SpeculativeLinear"
        self.worker_limit = 1 if strategy == "SerialQueue" else config.workers
        self.ready: List[int] = []
        self.pending: Deque[_Job] = deque()
        self.protected = set()
        self.train: Deque[_TrainNode] = deque()
        self.barrier = set()
        self.hedge: Optional[_Run] = None
        self.suite = _SuitePosterior()
        self.sequence = count()
        self.events = []
        self.recoveries: List[_Recovery] = []
        self.attempts: Counter = Counter()
        self.now = 0.0
        self.running = 0
        self.ci_time = 0.0
        self.total_runs = 0
        self.wasted_runs = 0
        self.invalidated_runs = 0
        self.escaped = 0
        self.merged: Dict[int, float] = {}
        self.rejected = set()
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
        recovery = _Recovery(faulty=bool(self.truth.culprits(members)))
        self.recoveries.append(recovery)
        return _Job(members, recovery)

    def _start(self, job: _Job) -> _Run:
        canonical = tuple(sorted(job.members))
        attempt = self.attempts[canonical]
        self.attempts[canonical] += 1
        key = _key(self.seed, "ci", attempt, *canonical)
        rng = Random(key)
        duration = max(
            0.01, rng.gauss(self.config.ci_duration_min, self.config.ci_duration_std)
        ) + job.overhead
        passes = not self.truth.culprits(job.members) and rng.random() >= self.config.flaky_rate
        run = _Run(job, self.now, self.now + duration, passes)
        heappush(self.events, (run.finishes, next(self.sequence), run))
        job.recovery.rounds = max(job.recovery.rounds, job.round)
        self.running += 1
        self.total_runs += 1
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
        if run is self.hedge:
            return
        if any(run is node.run for node in self.train):
            self._resolve_train()
        else:
            self._resolve_batch(run)

    def _dispatch(self) -> None:
        while self.pending and self.running < self.worker_limit:
            self._start(self.pending.popleft())
        if self.speculative:
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
            if self.uber and prefix and self._failure_risk(prefix + (self.ready[0],)) > _UBER_RISK_BUDGET:
                break
            size = 1
            if self.uber:
                for number in self.ready[1:self.config.max_batch_size]:
                    if self._failure_risk(prefix + tuple(self.ready[:size]) + (number,)) > _UBER_RISK_BUDGET:
                        break
                    size += 1
            members = tuple(self.ready[:size])
            del self.ready[:size]
            prefix += members
            self.train.append(_TrainNode(members, self._start(self._root_job(prefix))))

            # Uber speculates on the first bisection branch alongside a risky
            # root. A failed root can reuse that verification and run its sibling.
            if (
                self.uber and len(self.train) == 1 and len(members) > 1
                and self.running < self.worker_limit
                and self._failure_risk(members) >= _UBER_HEDGE_RISK
            ):
                left = members[:len(members) // 2]
                self.hedge = self._start(_Job(left, self.train[0].run.job.recovery))

    def _resolve_train(self) -> None:
        while self.train and self.train[0].run.completed:
            head = self.train.popleft()
            run = head.run
            hedge, self.hedge = self.hedge, None
            if run.passes:
                self._merge(run, head.members)
                if hedge is not None:
                    self._discard(hedge, invalidated=False)
                continue

            requeued = []
            for descendant in self.train:
                self._discard(descendant.run, invalidated=True)
                requeued.extend(descendant.members)
            self.train.clear()
            self.ready[:0] = requeued
            self.barrier.update(head.members)
            run.job.recovery.observed_failure = True
            if head.members != run.job.members:
                # This failed verification included an already-merged prefix.
                # Validate the tail on its own rebased snapshot first.
                self.pending.append(_Job(head.members, run.job.recovery, round=1))
            elif hedge is not None:
                job = run.job
                hedge.job = replace(hedge.job, round=job.round + 1)
                job.recovery.rounds = max(job.recovery.rounds, hedge.job.round)
                self.pending.append(_Job(
                    head.members[len(hedge.job.members):], job.recovery, round=job.round + 1
                ))
                if hedge.completed:
                    self._resolve_batch(hedge)
            else:
                self._resolve_batch(run)
            break

    def _should_rerun(self, job: _Job) -> bool:
        if self.fixed_rerun:
            return job.failures == 0
        if not self.adaptive_rerun or job.failures >= 2:
            return False
        fault = self._failure_risk(job.members)
        transient = (1 - fault) * self.suite.failure_probability(job.failures + 1)
        evidence = fault + transient
        return evidence > 0 and transient / evidence >= _FLAKE_RERUN_THRESHOLD

    def _resolve_batch(self, run: _Run) -> None:
        job = run.job
        if run.passes:
            if self.adaptive_rerun:
                self.suite.alpha += job.failures
                self.suite.beta += 1
            self.wasted_runs += job.failures
            self.invalidated_runs += job.failures
            self._merge(run, job.members)
            return

        job.recovery.observed_failure = True
        if self._should_rerun(job):
            self.pending.appendleft(replace(
                job, failures=job.failures + 1, round=job.round + 1, overhead=0.0
            ))
        elif len(job.members) == 1:
            number = job.members[0]
            self.rejected.add(number)
            self.barrier.discard(number)
        elif self.use_blame and job.allow_blame:
            self._attribute(job)
        else:
            self._bisect(job)

    def _attribute(self, job: _Job) -> None:
        top, second = self._rank_suspects(job.members)[:2]
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

    def _rank_suspects(self, members: _Members) -> _Members:
        rng = Random(_key(self.seed, "blame", *sorted(members)))
        ranking = sorted(
            members,
            key=lambda number: (self.records[number].risk_score, len(self.cones[number]), rng.random()),
            reverse=True,
        )
        culprits = self.truth.culprits(members)
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

    def _bisect(self, job: _Job) -> None:
        midpoint = len(job.members) // 2
        halves = (job.members[:midpoint], job.members[midpoint:])
        if job.next_suspect in halves[1]:
            halves = halves[::-1]
        self.pending.extend(
            _Job(members, job.recovery, job.round + 1, allow_blame=False)
            for members in halves
        )

    def _merge(self, run: _Run, members: _Members) -> None:
        if not run.completed or not run.passes or not set(members).issubset(run.job.members):
            raise RuntimeError("Merge attempted without a passing CI verification")
        self.escaped += len(self.truth.culprits(members))
        for number in members:
            if number in self.merged or number in self.rejected:
                raise RuntimeError("Pull request was already resolved")
            self.merged[number] = self.now
            self.barrier.discard(number)

    def _metrics(self) -> RunMetrics:
        merged = len(self.merged)
        duration = (self.now - min(self.arrivals.values(), default=0.0)) / 60
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
        "blame_top1_acc", "blame_top2_acc", "warmup_fraction",
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

    Each independent verification uses a fresh rebase context. Within a train,
    commit requires a passing prefix and all predecessor nodes to have committed.
    Canceled runs consume CI minutes up to cancellation. Invalidations count
    discarded train descendants and failures confirmed as flaky by a passing
    rerun. Wasted runs also include unused speculative bisection branches.
    Blame rounds count the deepest recovery path after a faulty batch fails.
    Empty denominators yield zero.
    """
    if strategy not in ALL_STRATEGIES:
        raise ValueError(f"Unknown queue strategy: {strategy}")
    records = list(prs)
    _validate(config, records)
    return _Simulation(graph, records, strategy, config, seed).run()
