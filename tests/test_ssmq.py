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
KUBERNETES_TRACE = Path(__file__).resolve().parents[1] / "data/traces/kubernetes_kubernetes.json"


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
        self.assertGreater(graph.warmup_mean_risk, 0.0)

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
        self.assertTrue(all(a < b for a, b in truth.conflicting_pairs))
        self.assertTrue(all(a % 3 == b % 3 for a, b in truth.on_graph_pairs))
        self.assertTrue(all(a % 3 != b % 3 for a, b in truth.off_graph_pairs))
        self.assertTrue(truth.persistent_pairs <= truth.conflicting_pairs)
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
                    self.assertEqual(
                        first.rejected_defective + first.rejected_persistent_conflict + first.rejected_false_flaky,
                        first.rejected_prs,
                    )
        self.assertEqual(observed, [truth] * (2 * len(ALL_STRATEGIES)))

    def test_fixed_density_graph_recall_preserves_total_conflict_count(self):
        graph, prs = load_trace(KUBERNETES_TRACE)
        config = SimConfig()
        full = build_ground_truth(graph, prs, replace(config, graph_recall=1.0), 42)
        base_total = len(full.conflicting_pairs)
        self.assertTrue(full.conflicting_pairs)
        self.assertEqual(full.off_graph_pairs, frozenset())
        for recall in (0.8, 0.6, 0.4, 0.2, 0.0):
            with self.subTest(recall=recall):
                truth = build_ground_truth(graph, prs, replace(config, graph_recall=recall), 42)
                # v5 EXACT-COUNT fixed-density invariant: zero variance across recall!
                self.assertEqual(len(truth.conflicting_pairs), base_total)
                expected_vis = 0 if recall == 0.0 else int(round(recall * base_total))
                self.assertEqual(len(truth.on_graph_pairs), expected_vis)
                realized = len(truth.on_graph_pairs) / len(truth.conflicting_pairs)
                metrics = simulate_queue(graph, prs, "SSMQ", replace(config, graph_recall=recall), 42)
                self.assertEqual(metrics.off_graph_conflicts_total, len(truth.off_graph_pairs))
                self.assertEqual(metrics.on_graph_conflicts_total, len(truth.on_graph_pairs))
                self.assertAlmostEqual(metrics.realized_graph_recall, realized, places=6)
        empty = build_ground_truth(graph, prs, replace(config, semantic_conflict_prob=0), 42)
        self.assertEqual(empty.conflicting_pairs, frozenset())

    def test_decoupled_risk_correlation_zero_gives_uniform_defects(self):
        prs = [make_pr(i, risk=risk) for i, risk in enumerate([0.05, 0.2, 0.5, 0.95])]
        probs = simulator._defect_probabilities(prs, 0.10, risk_correlation=0.0)
        for p in probs.values():
            self.assertAlmostEqual(p, 0.10, places=12)

    def test_warmup_priors_have_zero_eval_leakage(self):
        warmup = [
            PRRecord(1, ("src/a/x.py",), additions=10),
            PRRecord(2, ("src/b/y.py",), additions=40),
        ]
        graph = DependencyGraph(warmup)
        pr_target = make_pr(10, path="src/a/x.py", risk=0.4)
        p_a = simulator._warmup_scheduler_priors(graph, [pr_target, make_pr(11, risk=0.1)], 0.08)
        p_b = simulator._warmup_scheduler_priors(graph, [pr_target, make_pr(11, risk=0.99)], 0.08)
        self.assertEqual(p_a[10], p_b[10])

    def test_dep_aware_tree_bridge_pr_enforces_disjoint_lane_invariant(self):
        # Exact Codex v4 counterexample: lane {a}, lane {b}, then bridge PR {a, b}!
        pr1 = PRRecord(1, ("mod_a/f/a.py",), risk_score=0.1, arrival_min=0.0)
        pr2 = PRRecord(2, ("mod_b/f/b.py",), risk_score=0.1, arrival_min=0.0)
        pr3 = PRRecord(3, ("mod_a/f/a.py", "mod_b/f/b.py"), risk_score=0.1, arrival_min=0.0)
        sim = simulator._Simulation(GRAPH, [pr1, pr2, pr3], "DepAwareOutcomeTree", CONFIG, 0)
        orig_dispatch = sim._dispatch_dep_trees
        checked_ticks = 0

        def checked_dispatch():
            nonlocal checked_ticks
            orig_dispatch()
            checked_ticks += 1
            # Active lanes MUST remain strictly pairwise cone-disjoint at every tick!
            for i in range(len(sim.dep_lanes)):
                for j in range(i + 1, len(sim.dep_lanes)):
                    self.assertTrue(
                        sim.dep_lanes[i].cone.isdisjoint(sim.dep_lanes[j].cone),
                        msg=f"Active lanes overlapped: {sim.dep_lanes[i].cone} vs {sim.dep_lanes[j].cone}",
                    )
            # If both pr1 and pr2 are active in separate lanes, bridge pr3 MUST NOT be in any active lane!
            active_members = {m for lane in sim.dep_lanes for m in lane.queue}
            if 1 in active_members and 2 in active_members:
                self.assertNotIn(3, active_members)

        sim._dispatch_dep_trees = checked_dispatch
        metrics = sim.run()
        self.assertGreaterEqual(checked_ticks, 2)
        self.assertEqual((metrics.merged_prs, metrics.rejected_prs, metrics.escaped_defects), (3, 0, 0))

    def test_conflicts_are_reverified_in_isolation_and_do_not_reject_clean_prs(self):
        prs = [make_pr(number, path="src/app/shared.py") for number in range(1, 9)]
        config = replace(CONFIG, semantic_conflict_prob=1.0, graph_recall=1.0, persistent_conflict_share=0)
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

    def test_off_graph_conflict_fails_a_graph_disjoint_batch(self):
        prs = [make_pr(1), make_pr(2)]
        pair = frozenset({(1, 2)})
        truth = GroundTruth(frozenset(), pair, off_graph_pairs=pair)
        self.assertEqual(GRAPH.overlap_score(*prs), 0)
        with patch.object(simulator, "build_ground_truth", return_value=truth):
            result = simulate_queue(GRAPH, prs, "SSMQ-NoFlaky", CONFIG, 0)
        self.assertEqual((result.merged_prs, result.rejected_prs, result.escaped_defects), (2, 0, 0))
        self.assertEqual(result.total_ci_runs, 3)

    def test_persistent_conflict_is_rejected_against_cumulative_mainline(self):
        prs = [make_pr(number, arrival=(number - 1) * 20) for number in range(1, 4)]
        pair = frozenset({(1, 3)})
        truth = GroundTruth(frozenset(), pair, persistent_pairs=pair, off_graph_pairs=pair)
        for strategy in ALL_STRATEGIES:
            with self.subTest(strategy=strategy):
                with patch.object(simulator, "build_ground_truth", return_value=truth):
                    result = simulate_queue(GRAPH, prs, strategy, CONFIG, 0)
                self.assertEqual((result.merged_prs, result.rejected_prs, result.escaped_defects), (2, 1, 0))
                self.assertEqual((result.clean_prs_total, result.clean_prs_merged), (3, 2))
                self.assertEqual(result.rejected_persistent_conflict, 1)
                self.assertEqual(result.false_rejection_rate, 0)

    def test_concurrent_passing_job_reverifies_after_a_persistent_conflict(self):
        prs = [make_pr(1), make_pr(2)]
        pair = frozenset({(1, 2)})
        truth = GroundTruth(frozenset(), pair, persistent_pairs=pair, off_graph_pairs=pair)
        config = replace(CONFIG, workers=2, max_batch_size=1)
        with patch.object(simulator, "build_ground_truth", return_value=truth):
            result = simulate_queue(GRAPH, prs, "SSMQ-NoFlaky", config, 0)
        self.assertEqual((result.merged_prs, result.rejected_prs, result.escaped_defects), (1, 1, 0))
        self.assertEqual(result.total_ci_runs, 3)
        self.assertEqual(result.wasted_ci_runs, 1)
        self.assertEqual(result.ci_minutes_per_merged_pr, 30)
        self.assertEqual(result.runner_utilization, 0.75)

    def test_deconfounded_cone_and_risk_ablations(self):
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

        # SSMQ-NoRisk packs disjoint PRs regardless of risk score!
        started.clear()
        with patch.object(simulator._Simulation, "_start", capture):
            simulate_queue(GRAPH, prs, "SSMQ-NoRisk", replace(CONFIG, workers=1), 0)
        self.assertEqual(started, [(1, 3, 4, 5, 6), (2,)])

        # SSMQ-NoCone groups low-risk PRs even when cones overlap, isolating only high-risk PRs!
        started.clear()
        with patch.object(simulator._Simulation, "_start", capture):
            simulate_queue(GRAPH, prs, "SSMQ-NoCone", replace(CONFIG, workers=1), 0)
        self.assertEqual(started, [(1, 2, 3, 6), (4, 5)])

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

    def test_causal_blame_reduces_depth_and_instruments_hits(self):
        prs = [make_pr(number, risk=0.5) for number in range(1, 9)]
        perfect = replace(CONFIG, base_defect_rate=0.25, blame_top1_acc=1, blame_top2_acc=1)
        second_only = replace(perfect, blame_top1_acc=0)
        truth = GroundTruth(frozenset({8}), frozenset())
        with patch.object(simulator, "build_ground_truth", return_value=truth):
            full = simulate_queue(GRAPH, prs, "SSMQ", perfect, 0)
            disabled = simulate_queue(GRAPH, prs, "SSMQ-NoBlame", perfect, 0)
            missed = simulate_queue(GRAPH, prs, "SSMQ", second_only, 0)
        self.assertEqual(full.mean_blame_rounds, 1)
        self.assertEqual(full.blame_attempts, 1)
        self.assertEqual(full.blame_one_step_hits, 1)
        self.assertEqual(full.failed_root_batches, 1)
        self.assertEqual(full.failed_root_single_culprit, 1)
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

    def test_fifo_and_dep_aware_outcome_trees(self):
        graph, prs = load_trace(KUBERNETES_TRACE)
        config = SimConfig(velocity_multiplier=5)
        for seed in (42, 1001):
            with self.subTest(seed=seed):
                linear = simulate_queue(graph, prs, "SpeculativeLinear", config, seed)
                fifo_tree = simulate_queue(graph, prs, "FIFOOutcomeTree", config, seed)
                dep_tree = simulate_queue(graph, prs, "DepAwareOutcomeTree", config, seed)
                self.assertGreater(fifo_tree.throughput_prs_per_hr, linear.throughput_prs_per_hr)
                self.assertLess(dep_tree.p95_latency_min, fifo_tree.p95_latency_min)
                self.assertEqual(fifo_tree.escaped_defects, 0)
                self.assertEqual(dep_tree.escaped_defects, 0)

    def test_adaptive_reruns_lower_false_rejections_at_ten_percent_flakiness(self):
        graph, prs = load_trace(KUBERNETES_TRACE)
        config = SimConfig(flaky_rate=0.10)
        for seed in (42, 1001, 1002):
            with self.subTest(seed=seed):
                full = simulate_queue(graph, prs, "SSMQ", config, seed)
                disabled = simulate_queue(graph, prs, "SSMQ-NoFlaky", config, seed)
                self.assertEqual(full.clean_prs_total, disabled.clean_prs_total)
                self.assertLess(full.false_rejection_rate, disabled.false_rejection_rate)

    def test_empty_stream_and_velocity_scaling(self):
        empty = simulate_queue(GRAPH, [], "SerialQueue", CONFIG, 0)
        self.assertEqual((empty.total_ci_runs, empty.sim_duration_hr), (0, 0.0))
        self.assertEqual((empty.mean_batch_size, empty.runner_utilization, empty.false_rejection_rate), (0, 0, 0))
        prs = [make_pr(1, arrival=0), make_pr(2, arrival=60)]
        baseline = simulate_queue(GRAPH, prs, "SerialQueue", CONFIG, 0)
        fast = simulate_queue(GRAPH, prs, "SerialQueue", replace(CONFIG, velocity_multiplier=5), 0)
        self.assertAlmostEqual(baseline.sim_duration_hr * 60, 70)
        self.assertAlmostEqual(fast.sim_duration_hr * 60, 22)
        self.assertEqual(fast.p95_latency_min, 10)

    def test_kubernetes_trace_smoke_all_strategies(self):
        graph, prs = load_trace(KUBERNETES_TRACE, warmup_fraction=SimConfig.warmup_fraction)
        self.assertEqual(len(prs), 150)
        self.assertAlmostEqual((prs[-1].arrival_min - prs[0].arrival_min) / (len(prs) - 1), 10)
        for strategy in ALL_STRATEGIES:
            with self.subTest(strategy=strategy):
                metrics = simulate_queue(graph, prs, strategy, SimConfig(), 42)
                self.assertEqual(metrics.strategy, strategy)
                self.assertEqual(metrics.escaped_defects, 0)
                self.assertEqual(metrics.merged_prs + metrics.rejected_prs, len(prs))
                self.assertEqual(
                    metrics.rejected_defective + metrics.rejected_persistent_conflict + metrics.rejected_false_flaky,
                    metrics.rejected_prs,
                )
                self.assertGreater(metrics.throughput_prs_per_hr, 0)
                self.assertGreaterEqual(metrics.p95_latency_min, metrics.p50_latency_min)
                self.assertLessEqual(metrics.wasted_ci_runs, metrics.total_ci_runs)
                self.assertEqual(metrics.merged_prs, metrics.clean_prs_merged)
                self.assertLessEqual(metrics.clean_prs_merged, metrics.clean_prs_total)
                self.assertGreaterEqual(metrics.mean_batch_size, 1)
                self.assertGreater(metrics.runner_utilization, 0)
                self.assertLessEqual(metrics.runner_utilization, 1 + 1e-12)


if __name__ == "__main__":
    unittest.main()
