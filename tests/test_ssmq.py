import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.ssmq import (
    ALL_STRATEGIES,
    DependencyGraph,
    GroundTruth,
    PRRecord,
    SimConfig,
    build_ground_truth,
    load_trace,
    simulate_queue,
)
from src.ssmq import simulator


CONFIG = SimConfig(
    workers=8,
    ci_duration_min=10,
    ci_duration_std=0,
    velocity_multiplier=1,
    base_defect_rate=0,
    semantic_conflict_prob=0,
    flaky_rate=0,
)
GRAPH = DependencyGraph()


def make_pr(number, *, path=None, risk=0.1, arrival=0.0):
    return PRRecord(
        number=number,
        files=(path or f"module{number}/feature/code.py",),
        risk_score=risk,
        arrival_min=arrival,
    )


class DependencyGraphTests(unittest.TestCase):
    def test_cones_expand_one_hop_across_cochanges_and_modules(self):
        ab = PRRecord(1, ("src/app/a.py", "lib/core/b.py"), additions=5)
        bc = PRRecord(2, ("lib/core/b.py", "test/suite/c.py"), additions=20)
        module_neighbor = PRRecord(3, ("src/app/d.py",))
        graph = DependencyGraph([ab, bc, module_neighbor])
        a = make_pr(9, path="src/app/a.py")
        b = make_pr(10, path="lib/core/b.py")
        self.assertEqual(
            graph.impact_cone(a.files), {"src/app/a.py", "lib/core/b.py", "src/app/d.py"}
        )
        self.assertEqual(
            graph.impact_cone(["src/app/new.py"]),
            {"src/app/new.py", "src/app/a.py", "src/app/d.py"},
        )
        self.assertEqual(graph.overlap_score(a, b), 0.5)
        self.assertGreater(graph.risk_score(replace(a, additions=20)), graph.risk_score(a))
        self.assertGreater(graph.risk_score(b), graph.risk_score(make_pr(11)))
        self.assertLessEqual(graph.risk_score(replace(bc, additions=10**12)), 1.0)

    def test_loader_uses_chronological_warmup_and_preserves_gap_ratios(self):
        def row(number, timestamp, *files):
            return dict(
                repo="test/repo", number=number, created_at=timestamp, merged_at=timestamp,
                additions=2, deletions=1, changed_files_count=len(files),
                commit_count=1, author="test", files=files,
            )

        rows = [
            row(4, "2024-01-03T00:01:00Z", "test/future/c.py"),
            row(1, "2024-01-01T00:00:00Z", "src/app/warmup.py", "lib/core/shared.py"),
            row(3, "2024-01-02T06:00:00Z", "test/future/b.py"),
            row(2, "2024-01-02T00:00:00Z", "src/app/new.py", "test/future/a.py"),
        ]
        with patch.object(Path, "open", return_value=io.StringIO(json.dumps(rows))):
            graph, prs = load_trace("trace.json", baseline_arrivals_per_hr=12)
        self.assertEqual([pr.number for pr in prs], [2, 3, 4])
        self.assertEqual([prs[0].arrival_min, prs[-1].arrival_min], [0.0, 10.0])
        self.assertAlmostEqual(prs[1].arrival_min / prs[-1].arrival_min, 360 / 1441)
        self.assertIn("src/app/warmup.py", prs[0].impact_cone)
        self.assertNotIn("test/future/b.py", prs[0].impact_cone)
        self.assertNotIn("lib/core/shared.py", prs[0].impact_cone)
        self.assertEqual(graph.overlap_score(prs[0], prs[1]), 0.0)
        self.assertEqual(prs[0].risk_score, graph.risk_score(prs[0]))


class SimulatorTests(unittest.TestCase):
    def test_calibrated_expected_defect_rate(self):
        prs = [make_pr(i, risk=risk) for i, risk in enumerate([0.0, 0.1, 0.2, 0.4])]
        for rate in (0.0, 0.08, 0.75, 1.0):
            with self.subTest(rate=rate):
                p = list(simulator._defect_probabilities(prs, rate).values())
                self.assertAlmostEqual(sum(p) / len(p), rate)
                self.assertEqual(p, sorted(p))
                self.assertTrue(all(0 <= value <= 1 for value in p))
        usual = simulator._defect_probabilities(prs, 0.08)
        self.assertAlmostEqual(usual[3], 4 * usual[1])

    def test_determinism_and_paired_ground_truth(self):
        prs = [
            make_pr(i, path=f"project/module{i % 3}/file.py", risk=i / 24, arrival=i % 3)
            for i in range(1, 25)
        ]
        config = replace(CONFIG, base_defect_rate=0.35, semantic_conflict_prob=0.55, flaky_rate=0.15)
        truth = build_ground_truth(GRAPH, prs, config, seed=71)
        self.assertTrue(truth.defective_prs)
        self.assertTrue(truth.conflicting_pairs)
        self.assertTrue(all(a < b and a % 3 == b % 3 for a, b in truth.conflicting_pairs))
        self.assertNotEqual(truth, build_ground_truth(GRAPH, prs, config, seed=72))
        self.assertEqual(
            truth, build_ground_truth(GRAPH, reversed(prs), replace(config, velocity_multiplier=10), 71)
        )

        observed = []

        def capture(*args):
            generated = build_ground_truth(*args)
            observed.append(generated)
            return generated

        with patch.object(simulator, "build_ground_truth", side_effect=capture):
            for strategy in ALL_STRATEGIES:
                with self.subTest(strategy=strategy):
                    first = simulate_queue(GRAPH, prs, strategy, config, 71)
                    again = simulate_queue(GRAPH, reversed(prs), strategy, config, 71)
                    self.assertEqual(first.to_dict(), again.to_dict())
                    self.assertEqual(first.escaped_defects, 0)
                    self.assertGreaterEqual(first.rejected_prs, len(truth.defective_prs))
                    self.assertEqual(first.merged_prs + first.rejected_prs, len(prs))
        self.assertEqual(observed, [truth] * (2 * len(ALL_STRATEGIES)))

    def test_conflicts_are_reverified_in_isolation_and_do_not_reject_clean_prs(self):
        prs = [make_pr(number, path="src/app/shared.py") for number in range(1, 9)]
        config = replace(CONFIG, semantic_conflict_prob=1.0)
        for strategy in ALL_STRATEGIES:
            with self.subTest(strategy=strategy):
                result = simulate_queue(GRAPH, prs, strategy, config, 0)
                self.assertEqual(result.merged_prs, len(prs))
                self.assertEqual(result.rejected_prs, 0)
                self.assertEqual(result.escaped_defects, 0)
                if strategy == "StaticBatchBisect":
                    self.assertEqual(result.total_ci_runs, 2 * len(prs) - 1)
                if strategy == "SpeculativeLinear":
                    self.assertGreater(result.invalidation_rate, 0)

    def test_graph_lanes_keep_cones_disjoint_and_protect_high_risk_prs(self):
        prs = [
            make_pr(1, path="src/app/shared.py"),
            make_pr(2, path="src/app/shared.py"),
            make_pr(3),
            make_pr(4, risk=0.9),
            make_pr(5, risk=0.8),
            make_pr(6),
        ]
        started = []
        start = simulator._Simulation._start

        def capture(sim, job):
            started.append(job.members)
            return start(sim, job)

        with patch.object(simulator._Simulation, "_start", capture):
            simulate_queue(GRAPH, prs, "SSMQ", replace(CONFIG, workers=1), 0)
        self.assertEqual(started, [(1, 3, 6), (2, 4), (5,)])
        for members in started:
            for i, left in enumerate(members):
                for right in members[i + 1:]:
                    self.assertEqual(GRAPH.overlap_score(prs[left - 1], prs[right - 1]), 0)

        started.clear()
        with patch.object(simulator._Simulation, "_start", capture):
            simulate_queue(GRAPH, prs, "SSMQ-NoGraph", CONFIG, 0)
        self.assertEqual(started, [(1, 2, 3, 4, 5, 6)])

    def test_adaptive_flake_rerun_avoids_batch_teardown(self):
        prs = [make_pr(number) for number in range(1, 9)]
        config = replace(CONFIG, flaky_rate=0.30)
        full = simulate_queue(GRAPH, prs, "SSMQ", config, 0)
        disabled = simulate_queue(GRAPH, prs, "SSMQ-NoFlaky", config, 0)
        baseline = simulate_queue(GRAPH, prs, "FlakeAwareBatch", config, 0)
        self.assertEqual(full.merged_prs, len(prs))
        self.assertEqual(full.total_ci_runs, 2)
        self.assertEqual(full.wasted_ci_runs, 1)
        self.assertEqual(baseline.total_ci_runs, 2)
        self.assertLess(full.ci_minutes_per_merged_pr, disabled.ci_minutes_per_merged_pr)

    def test_causal_blame_reduces_depth_and_bisects_after_a_miss(self):
        prs = [make_pr(number) for number in range(1, 9)]
        perfect = replace(CONFIG, base_defect_rate=0.25, blame_top1_acc=1, blame_top2_acc=1)
        second_only = replace(perfect, blame_top1_acc=0)
        truth = GroundTruth(frozenset({8}), frozenset())
        with patch.object(simulator, "build_ground_truth", return_value=truth):
            full = simulate_queue(GRAPH, prs, "SSMQ", perfect, 0)
            disabled = simulate_queue(GRAPH, prs, "SSMQ-NoBlame", perfect, 0)
            missed = simulate_queue(GRAPH, prs, "SSMQ", second_only, 0)
        self.assertEqual(full.mean_blame_rounds, 1)
        self.assertEqual(full.total_ci_runs, 3)
        self.assertEqual(disabled.mean_blame_rounds, 3)
        self.assertGreater(missed.mean_blame_rounds, full.mean_blame_rounds)
        for run in (full, disabled, missed):
            self.assertEqual((run.merged_prs, run.rejected_prs, run.escaped_defects), (7, 1, 0))

    def test_linear_abort_charges_partial_runner_time(self):
        prs = [make_pr(number, arrival=number - 1) for number in range(1, 5)]
        truth = GroundTruth(frozenset({1}), frozenset())
        with patch.object(simulator, "build_ground_truth", return_value=truth):
            result = simulate_queue(GRAPH, prs, "SpeculativeLinear", CONFIG, 0)
        self.assertEqual((result.merged_prs, result.rejected_prs, result.total_ci_runs), (3, 1, 7))
        self.assertEqual(result.wasted_ci_runs, 3)
        self.assertAlmostEqual(result.invalidation_rate, 3 / 7)
        self.assertAlmostEqual(result.ci_minutes_per_merged_pr, (10 + 9 + 8 + 7 + 30) / 3)
        self.assertAlmostEqual(result.sim_duration_hr, 20 / 60)

    def test_uber_tree_reuses_a_speculative_bisection_branch(self):
        prs = [make_pr(number) for number in range(1, 5)]
        config = replace(CONFIG, base_defect_rate=0.04, workers=3)
        with patch.object(
            simulator, "build_ground_truth", return_value=GroundTruth(frozenset({4}), frozenset())
        ):
            result = simulate_queue(GRAPH, prs, "UberSubmitQueue", config, 0)
        self.assertEqual((result.merged_prs, result.rejected_prs, result.total_ci_runs), (3, 1, 5))
        self.assertEqual(result.wasted_ci_runs, 0)
        self.assertEqual(result.escaped_defects, 0)

    def test_empty_stream_and_velocity_scaling(self):
        empty = simulate_queue(GRAPH, [], "SerialQueue", CONFIG, 0)
        self.assertEqual((empty.total_ci_runs, empty.sim_duration_hr), (0, 0.0))
        prs = [make_pr(1, arrival=0), make_pr(2, arrival=60)]
        baseline = simulate_queue(GRAPH, prs, "SerialQueue", CONFIG, 0)
        fast = simulate_queue(GRAPH, prs, "SerialQueue", replace(CONFIG, velocity_multiplier=5), 0)
        self.assertAlmostEqual(baseline.sim_duration_hr * 60, 70)
        self.assertAlmostEqual(fast.sim_duration_hr * 60, 22)
        self.assertEqual(fast.p95_latency_min, 10)

    def test_kubernetes_trace_smoke_all_strategies(self):
        path = Path(__file__).resolve().parents[1] / "data/traces/kubernetes_kubernetes.json"
        graph, prs = load_trace(path, warmup_fraction=SimConfig.warmup_fraction)
        self.assertEqual(len(prs), 150)
        self.assertAlmostEqual((prs[-1].arrival_min - prs[0].arrival_min) / (len(prs) - 1), 10)
        for strategy in ALL_STRATEGIES:
            with self.subTest(strategy=strategy):
                metrics = simulate_queue(graph, prs, strategy, SimConfig(), 42)
                self.assertEqual(metrics.strategy, strategy)
                self.assertEqual(metrics.escaped_defects, 0)
                self.assertEqual(metrics.merged_prs + metrics.rejected_prs, len(prs))
                self.assertGreater(metrics.throughput_prs_per_hr, 0)
                self.assertGreaterEqual(metrics.p95_latency_min, metrics.p50_latency_min)
                self.assertLessEqual(metrics.wasted_ci_runs, metrics.total_ci_runs)


if __name__ == "__main__":
    unittest.main()
