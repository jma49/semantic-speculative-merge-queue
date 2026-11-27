"""Unit tests asserting faithful failure semantics, exact-tree authority, and parity (v8)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from eval.baselines_v8 import engine  # noqa: E402
from eval.baselines_v8.engine import (  # noqa: E402
    ALL_V8_STRATEGIES,
    NEW_V8_STRATEGIES,
    V7_CONTROL_STRATEGIES,
    FilteredConeGraph,
    load_trace_with_warmup,
    simulate_v8_queue,
)
from ssmq import DependencyGraph, GroundTruth, PRRecord, SimConfig, build_ground_truth, simulate_queue  # noqa: E402

CONFIG = SimConfig(
    workers=8,
    ci_duration_min=10.0,
    ci_duration_std=0.0,
    velocity_multiplier=1.0,
    base_defect_rate=0.0,
    semantic_conflict_prob=0.0,
    flaky_rate=0.0,
)
GRAPH = DependencyGraph()
TRACES_DIR = ROOT / "data" / "traces"


def make_pr(number: int, *, files=None, risk: float = 0.1, arrival: float = 0.0) -> PRRecord:
    if files is None:
        files = (f"mod{number}/sub/file{number}.py",)
    return PRRecord(
        number=number,
        files=tuple(files),
        risk_score=risk,
        arrival_min=arrival,
        changed_files_count=len(files),
    )


class FailureSemanticsTests(unittest.TestCase):
    def test_beheshtian_batchstop4_bisects_above_4_and_stops_at_4_to_singletons(self) -> None:
        # 8 PRs with PR 7 defective: batch(8) -> fails -> bisects to (4, 4) ->
        # failing sub-batch (5,6,7,8) MUST stop bisecting and emit 4 singletons directly (never pairs of 2!).
        prs = [make_pr(i) for i in range(1, 9)]
        truth = GroundTruth(frozenset({7}), frozenset())
        started = []
        orig_start = engine._V8Simulation._start

        def capture(sim, job, **kwargs):
            started.append(job.members)
            return orig_start(sim, job, **kwargs)

        cfg = replace(CONFIG, workers=1)  # workers=1 isolates pure BatchStop4 split sequence
        with patch.object(engine, "build_ground_truth", return_value=truth):
            with patch.object(engine._V8Simulation, "_start", capture):
                metrics = simulate_v8_queue(GRAPH, prs, "Beheshtian-BatchStop4", cfg, 0)

        self.assertEqual((metrics.merged_prs, metrics.rejected_prs, metrics.escaped_defects), (7, 1, 0))
        self.assertEqual(started[0], (1, 2, 3, 4, 5, 6, 7, 8))
        self.assertEqual(started[1], (1, 2, 3, 4))
        self.assertEqual(started[2], (5, 6, 7, 8))
        # Critically: NO size-2 sub-batches (5,6) or (7,8); jumps straight to singletons!
        self.assertEqual(started[3:], [(5,), (6,), (7,), (8,)])
        self.assertTrue(all(len(m) != 2 for m in started))

    def test_beheshtian_batchstop4_size_4_fails_directly_to_singletons_and_enforces_exact_tree(self) -> None:
        prs = [make_pr(i) for i in range(1, 5)]
        truth = GroundTruth(frozenset({3}), frozenset())
        started = []
        orig_start = engine._V8Simulation._start

        def capture(sim, job, **kwargs):
            started.append(job.members)
            return orig_start(sim, job, **kwargs)

        with patch.object(engine, "build_ground_truth", return_value=truth):
            with patch.object(engine._V8Simulation, "_start", capture):
                metrics = simulate_v8_queue(GRAPH, prs, "Beheshtian-BatchStop4", CONFIG, 0)

        self.assertEqual((metrics.merged_prs, metrics.rejected_prs, metrics.escaped_defects), (3, 1, 0))
        # First run is (1,2,3,4); on failure, 4 singletons launch concurrently on W=8, causing stale rechecks!
        self.assertEqual(started[0], (1, 2, 3, 4))
        self.assertEqual(started[1:5], [(1,), (2,), (3,), (4,)])
        self.assertGreater(metrics.stale_rechecks, 0)
        self.assertTrue(all(len(m) != 2 for m in started))

    def test_bors_batch_bisect_single_staging_branch_recursive_bisection_zero_stale(self) -> None:
        # Even with W=8 workers available, historical bors-ng runs 1 staging batch at a time
        # and recursively bisects (8 -> 4+4 -> 2+2 -> 1+1) with ZERO stale rechecks.
        prs = [make_pr(i) for i in range(1, 9)]
        truth = GroundTruth(frozenset({7}), frozenset())
        started = []
        max_concurrent = 0
        orig_start = engine._V8Simulation._start

        def capture(sim, job, **kwargs):
            nonlocal max_concurrent
            started.append(job.members)
            run = orig_start(sim, job, **kwargs)
            max_concurrent = max(max_concurrent, sim.running)
            return run

        with patch.object(engine, "build_ground_truth", return_value=truth):
            with patch.object(engine._V8Simulation, "_start", capture):
                metrics = simulate_v8_queue(GRAPH, prs, "Bors-BatchBisect", CONFIG, 0)

        self.assertEqual(max_concurrent, 1)
        self.assertEqual(metrics.stale_rechecks, 0)
        self.assertEqual((metrics.merged_prs, metrics.rejected_prs, metrics.escaped_defects), (7, 1, 0))
        self.assertEqual(
            started,
            [
                (1, 2, 3, 4, 5, 6, 7, 8),
                (1, 2, 3, 4),
                (5, 6, 7, 8),
                (5, 6),
                (7, 8),
                (7,),
                (8,),
            ],
        )

    def test_github_mq_evicts_head_failure_cancels_and_rebuilds_without_bisection(self) -> None:
        # PR 1 is defective, PRs 2..4 arrive staggered by 1 min so all 4 are staged concurrently.
        # GitHub MQ must evict PR 1 immediately, cancel stages 2..4 (charging partial CI), and rebuild 2..4.
        prs = [make_pr(i, arrival=float(i - 1)) for i in range(1, 5)]
        truth = GroundTruth(frozenset({1}), frozenset())
        started = []
        orig_start = engine._V8Simulation._start

        def capture(sim, job, **kwargs):
            started.append((job.prefix, job.members))
            return orig_start(sim, job, **kwargs)

        with patch.object(engine, "build_ground_truth", return_value=truth):
            with patch.object(engine._V8Simulation, "_start", capture):
                metrics = simulate_v8_queue(GRAPH, prs, "GitHubMQ-StagedGroup", CONFIG, 0)

        self.assertEqual((metrics.merged_prs, metrics.rejected_prs, metrics.escaped_defects), (3, 1, 0))
        self.assertEqual(metrics.stale_rechecks, 0)
        self.assertEqual(metrics.wasted_ci_runs, 3)
        self.assertAlmostEqual(metrics.canceled_ci_minutes, 9.0 + 8.0 + 7.0)
        # Verify exact prefix progression: initial build on defective PR 1, then clean rebuild of 2,3,4
        self.assertEqual(
            started,
            [
                ((), (1,)),
                ((1,), (2,)),
                ((1, 2), (3,)),
                ((1, 2, 3), (4,)),
                ((), (2,)),
                ((2,), (3,)),
                ((2, 3), (4,)),
            ],
        )

    def test_github_mq_evicts_mid_queue_failure_immediately_without_retest(self) -> None:
        # PR 1 clean, PR 2 defective, PR 3 clean, PR 4 clean (all arrive at t=0).
        # PR 1 passes & merges; PR 2's staged run (prefix=(1,), members=(2,)) fails ->
        # PR 2 is immediately evicted (NO retest of PR 2 alone!), stages 3 & 4 are discarded & rebuilt atop {1}.
        prs = [make_pr(i, arrival=float(i - 1)) for i in range(1, 5)]
        truth = GroundTruth(frozenset({2}), frozenset())
        started = []
        orig_start = engine._V8Simulation._start

        def capture(sim, job, **kwargs):
            started.append((job.prefix, job.members))
            return orig_start(sim, job, **kwargs)

        with patch.object(engine, "build_ground_truth", return_value=truth):
            with patch.object(engine._V8Simulation, "_start", capture):
                metrics = simulate_v8_queue(GRAPH, prs, "GitHubMQ-StagedGroup", CONFIG, 0)

        self.assertEqual((metrics.merged_prs, metrics.rejected_prs, metrics.escaped_defects), (3, 1, 0))
        self.assertEqual(metrics.stale_rechecks, 0)
        # PR 2 ran ONLY ONCE (never retried or bisected!)
        pr2_runs = [m for _, m in started if m == (2,)]
        self.assertEqual(len(pr2_runs), 1)
        # Stages 3 and 4 were canceled and rebuilt cleanly without PR 2
        self.assertEqual(started[-2:], [((), (3,)), ((3,), (4,))])

    def test_github_mq_rejects_persistent_conflict_and_propagates_prefix_fault(self) -> None:
        prs = [make_pr(1, arrival=0.0), make_pr(2, arrival=0.0)]
        pair = frozenset({(1, 2)})
        truth = GroundTruth(frozenset(), pair, persistent_pairs=pair, off_graph_pairs=pair)
        with patch.object(engine, "build_ground_truth", return_value=truth):
            metrics = simulate_v8_queue(GRAPH, prs, "GitHubMQ-StagedGroup", CONFIG, 0)
        self.assertEqual((metrics.merged_prs, metrics.rejected_prs, metrics.escaped_defects), (1, 1, 0))
        self.assertEqual(metrics.rejected_persistent_conflict, 1)
        self.assertEqual(metrics.false_rejection_rate, 0.0)

        # Prefix fault propagation: if PR 1 is defective and finishes AFTER PR 2 (due to duration),
        # PR 2's staged run must still fail (cannot falsely pass atop defective PR 1), and once PR 1
        # is evicted, PR 2 rebuilds cleanly on main and merges!
        prs2 = [make_pr(1, arrival=0.0), make_pr(2, arrival=0.0)]
        truth2 = GroundTruth(frozenset({1}), frozenset())
        cfg_var = replace(CONFIG, ci_duration_min=12.0, ci_duration_std=2.0)
        with patch.object(engine, "build_ground_truth", return_value=truth2):
            for seed in range(10):
                m = simulate_v8_queue(GRAPH, prs2, "GitHubMQ-StagedGroup", cfg_var, seed)
                self.assertEqual((m.merged_prs, m.rejected_prs, m.escaped_defects), (1, 1, 0))

    def test_predibatch_groups_low_risk_isolates_high_risk_and_bisects_on_failure(self) -> None:
        # Low-risk PRs (1, 2, 3, 5) grouped together; high-risk PR 4 isolated as singleton!
        prs = [
            make_pr(1, risk=0.10),
            make_pr(2, risk=0.15),
            make_pr(3, risk=0.20),
            make_pr(4, risk=0.85),
            make_pr(5, risk=0.12),
        ]
        # Defect in PR 2 (inside low-risk batch) and PR 4 (isolated high-risk singleton).
        truth = GroundTruth(frozenset({2, 4}), frozenset())
        started = []
        orig_start = engine._V8Simulation._start

        def capture(sim, job, **kwargs):
            started.append((job.prefix, job.members))
            return orig_start(sim, job, **kwargs)

        with patch.object(engine, "build_ground_truth", return_value=truth):
            with patch.object(engine._V8Simulation, "_start", capture):
                metrics = simulate_v8_queue(GRAPH, prs, "PRediBatch-RiskGroup", CONFIG, 0)

        self.assertEqual((metrics.merged_prs, metrics.rejected_prs, metrics.escaped_defects), (3, 2, 0))
        self.assertEqual(metrics.stale_rechecks, 0)
        # First stage groups all 4 low-risk PRs (head=1, then sorted ascending risk: 5, 2, 3);
        # second speculative stage isolates high-risk PR 4 as singleton atop the low-risk batch!
        self.assertEqual(started[0], ((), (1, 5, 2, 3)))
        self.assertEqual(started[1], ((1, 5, 2, 3), (4,)))
        # When (1,5,2,3) fails, stage 2 is canceled and (1,5,2,3) bisects into (1,5) and (2,3)!
        self.assertEqual(started[2], ((), (1, 5)))
        self.assertEqual(started[3], ((1, 5), (2, 3)))

    def test_predibatch_priors_are_decoupled_from_ground_truth_rates(self) -> None:
        warmup = [
            PRRecord(1, ("src/a/x.py",), additions=10),
            PRRecord(2, ("src/b/y.py",), additions=80),
        ]
        graph = DependencyGraph(warmup)
        prs = [make_pr(10, risk=0.2), make_pr(11, risk=0.8)]
        cfg_lo = replace(CONFIG, base_defect_rate=0.01, semantic_conflict_prob=0.01, flaky_rate=0.01)
        cfg_hi = replace(CONFIG, base_defect_rate=0.60, semantic_conflict_prob=0.80, flaky_rate=0.40)
        sim_lo = engine._V8Simulation(graph, prs, "PRediBatch-RiskGroup", cfg_lo, 0)
        sim_hi = engine._V8Simulation(graph, prs, "PRediBatch-RiskGroup", cfg_hi, 0)
        self.assertEqual(sim_lo.defect_risk, sim_hi.defect_risk)

    def test_aviator_dir_affected_lanes_bridge_barrier_and_exact_tree_rechecks(self) -> None:
        # PR 1 touches dir_a, PR 2 touches dir_b (disjoint direct dirs -> parallel lanes!),
        # PR 3 touches both dir_a & dir_b (bridge PR -> MUST wait until lanes drain!).
        # Under equal exact-tree authority, when Lane 1 merges PR 1, Lane 2's concurrent pass
        # on stale main MUST trigger a stale_recheck (zero escapes!).
        pr1 = make_pr(1, files=("pkg/dir_a/x.py",), arrival=0.0)
        pr2 = make_pr(2, files=("pkg/dir_b/y.py",), arrival=0.0)
        pr3 = make_pr(3, files=("pkg/dir_a/x.py", "pkg/dir_b/y.py"), arrival=0.0)

        sim = engine._V8Simulation(GRAPH, [pr1, pr2, pr3], "Aviator-DirAffected", CONFIG, 0)
        orig_dispatch = sim._dispatch_aviator
        checked_ticks = 0

        def checked_dispatch() -> None:
            nonlocal checked_ticks
            orig_dispatch()
            checked_ticks += 1
            for i in range(len(sim.aviator_lanes)):
                for j in range(i + 1, len(sim.aviator_lanes)):
                    self.assertTrue(
                        sim.aviator_lanes[i].targets.isdisjoint(sim.aviator_lanes[j].targets),
                        msg="Active Aviator lanes overlapped on direct directory targets",
                    )
            active = {m for lane in sim.aviator_lanes for m in lane.queue}
            if 1 in active and 2 in active:
                self.assertNotIn(3, active)

        sim._dispatch_aviator = checked_dispatch  # type: ignore[method-assign]
        metrics = sim.run()
        self.assertGreaterEqual(checked_ticks, 2)
        self.assertEqual((metrics.merged_prs, metrics.rejected_prs, metrics.escaped_defects), (3, 0, 0))
        self.assertGreaterEqual(metrics.stale_rechecks, 1)

        # Cross-directory persistent conflict MUST be caught by exact-tree recheck (zero escapes!).
        pair = frozenset({(1, 2)})
        truth_p = GroundTruth(frozenset(), pair, persistent_pairs=pair, off_graph_pairs=pair)
        with patch.object(engine, "build_ground_truth", return_value=truth_p):
            m_p = simulate_v8_queue(GRAPH, [pr1, pr2], "Aviator-DirAffected", CONFIG, 0)
        self.assertEqual((m_p.merged_prs, m_p.rejected_prs, m_p.escaped_defects), (1, 1, 0))
        self.assertEqual(m_p.rejected_persistent_conflict, 1)
        self.assertEqual(m_p.stale_rechecks, 1)

    def test_filtered_cone_drops_megaprs_and_directory_cliques_without_leakage(self) -> None:
        mega_files = tuple(f"pkg/sub{i}/f{i}.py" for i in range(25))
        warmup = [
            PRRecord(1, mega_files),
            PRRecord(2, ("src/app/a.py", "lib/core/b.py")),
            PRRecord(3, ("src/app/c.py",)),
        ]
        fg = FilteredConeGraph(warmup, max_files=20)
        self.assertEqual((fg.warmup_total, fg.warmup_kept), (3, 2))
        # Co-change from small PR 2 is retained; mega-PR 1 and dir sibling c.py are excluded!
        self.assertEqual(fg.impact_cone(("src/app/a.py",)), frozenset({"src/app/a.py", "lib/core/b.py"}))
        self.assertEqual(fg.impact_cone(("pkg/sub0/f0.py",)), frozenset({"pkg/sub0/f0.py"}))

        # Verify dramatic cone density reduction on all three frozen v7 traces.
        for stem in ("kubernetes_kubernetes", "microsoft_TypeScript", "vercel_next_js"):
            graph, fgraph, prs = load_trace_with_warmup(TRACES_DIR / f"{stem}.json")
            m_unfilt = simulate_v8_queue(graph, prs, "SSMQ", SimConfig(), 42)
            m_filt = simulate_v8_queue(graph, prs, "SSMQ-FilteredCone", SimConfig(), 42, filtered_graph=fgraph)
            self.assertLess(m_filt.observed_graph_edge_density, 0.25)
            self.assertLess(m_filt.observed_graph_edge_density, m_unfilt.observed_graph_edge_density * 0.4)
            self.assertEqual(m_filt.escaped_defects, 0)


class CrossStrategyInvariantTests(unittest.TestCase):
    def test_all_v8_strategies_deterministic_paired_and_exact_tree_on_k8s_trace(self) -> None:
        graph, fgraph, prs = load_trace_with_warmup(TRACES_DIR / "kubernetes_kubernetes.json")
        cfg = SimConfig(
            semantic_conflict_prob=0.18,
            persistent_conflict_share=0.03,
            blame_overhead_min=0.00004,
            scheduler_defect_prior=0.10,
            scheduler_conflict_prior=0.10,
            scheduler_persistent_prior=0.02,
        )
        truth = build_ground_truth(graph, prs, cfg, 1001)
        for strategy in ALL_V8_STRATEGIES:
            with self.subTest(strategy=strategy):
                first = simulate_v8_queue(graph, prs, strategy, cfg, 1001, filtered_graph=fgraph)
                again = simulate_v8_queue(graph, list(reversed(prs)), strategy, cfg, 1001, filtered_graph=fgraph)
                self.assertEqual(first.to_dict(), again.to_dict())
                self.assertEqual(first.escaped_defects, 0)
                self.assertEqual(first.merged_prs + first.rejected_prs, len(prs))
                self.assertEqual(
                    first.rejected_defective + first.rejected_persistent_conflict + first.rejected_false_flaky,
                    first.rejected_prs,
                )
                self.assertEqual(first.clean_prs_total, len(prs) - len(truth.defective_prs))
                self.assertGreater(first.throughput_prs_per_hr, 0.0)
                self.assertLessEqual(first.runner_utilization, 1.0 + 1e-12)
                if strategy in V7_CONTROL_STRATEGIES:
                    direct = simulate_queue(graph, prs, strategy, cfg, 1001)
                    self.assertEqual(first.to_dict(), direct.to_dict())
                if strategy in ("Bors-BatchBisect", "GitHubMQ-StagedGroup", "PRediBatch-RiskGroup"):
                    self.assertEqual(first.stale_rechecks, 0)


if __name__ == "__main__":
    unittest.main()
