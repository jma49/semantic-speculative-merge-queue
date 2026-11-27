"""Unit tests for v9 sensitivity invariants, GroundTruth monotonicity, decoupling, and parity."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from eval.baselines_v8 import engine as v8_engine  # noqa: E402
from eval.baselines_v8 import harness_v8_baselines as v8_harness  # noqa: E402
from eval.sensitivity_v9 import harness_v9_sensitivity as v9_harness  # noqa: E402
from ssmq import build_ground_truth  # noqa: E402

TRACES_DIR = ROOT / "data" / "traces"


class SensitivityInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph, cls.fgraph, cls.prs = v8_engine.load_trace_with_warmup(
            TRACES_DIR / "kubernetes_kubernetes.json"
        )

    def test_default_configs_match_v8_baselines_exactly(self) -> None:
        self.assertEqual(v9_harness.BASE, v8_harness.BASE)
        self.assertEqual(v9_harness.CONDITIONS["nominal_default"], v8_harness.CONDITIONS["nominal"])
        self.assertEqual(v9_harness.CONDITIONS["saturated_default"], v8_harness.CONDITIONS["saturated"])

    def test_ci_duration_sweep_preserves_ground_truth_and_priors(self) -> None:
        gt12 = build_ground_truth(self.graph, self.prs, v9_harness.CONDITIONS["nominal_default"], 1001)
        gt16 = build_ground_truth(self.graph, self.prs, v9_harness.CONDITIONS["nominal_ci16"], 1001)
        gt20 = build_ground_truth(self.graph, self.prs, v9_harness.CONDITIONS["nominal_ci20"], 1001)
        self.assertEqual(gt12, gt16)
        self.assertEqual(gt12, gt20)

    def test_persistent_share_sweep_is_strictly_monotone_and_keeps_other_features_fixed(self) -> None:
        gt03 = build_ground_truth(self.graph, self.prs, v9_harness.CONDITIONS["nominal_default"], 1001)
        gt10 = build_ground_truth(self.graph, self.prs, v9_harness.CONDITIONS["nominal_persist10"], 1001)
        gt25 = build_ground_truth(self.graph, self.prs, v9_harness.CONDITIONS["nominal_persist25"], 1001)

        # Unary defects, total conflict pairs, and on/off-graph partition stay identical.
        self.assertEqual(gt03.defective_prs, gt10.defective_prs)
        self.assertEqual(gt03.defective_prs, gt25.defective_prs)
        self.assertEqual(gt03.conflicting_pairs, gt10.conflicting_pairs)
        self.assertEqual(gt03.conflicting_pairs, gt25.conflicting_pairs)
        self.assertEqual(gt03.off_graph_pairs, gt10.off_graph_pairs)
        self.assertEqual(gt03.off_graph_pairs, gt25.off_graph_pairs)

        # Persistent conflict pairs form a strict monotone superset.
        self.assertTrue(gt03.persistent_pairs.issubset(gt10.persistent_pairs))
        self.assertTrue(gt10.persistent_pairs.issubset(gt25.persistent_pairs))
        self.assertLess(len(gt03.persistent_pairs), len(gt10.persistent_pairs))
        self.assertLess(len(gt10.persistent_pairs), len(gt25.persistent_pairs))

        # Decoupled scheduler priors remain completely invariant.
        sim03 = v8_engine._V8Simulation(
            self.graph, self.prs, "SSMQ-FilteredCone", v9_harness.CONDITIONS["nominal_default"], 1001, self.fgraph
        )
        sim25 = v8_engine._V8Simulation(
            self.graph, self.prs, "SSMQ-FilteredCone", v9_harness.CONDITIONS["nominal_persist25"], 1001, self.fgraph
        )
        self.assertEqual(sim03.defect_risk, sim25.defect_risk)
        self.assertEqual(sim03.cones, sim25.cones)
        self.assertEqual(sim03.config.scheduler_persistent_prior, sim25.config.scheduler_persistent_prior)
        self.assertEqual(sim03.config.scheduler_conflict_prior, sim25.config.scheduler_conflict_prior)
        self.assertEqual(sim03.config.scheduler_defect_prior, sim25.config.scheduler_defect_prior)

    def test_reproducibility_order_invariance_and_exact_tree_across_sweeps(self) -> None:
        for cond_name in ("nominal_ci16", "nominal_ci20", "nominal_persist10", "nominal_persist25"):
            cfg = v9_harness.CONDITIONS[cond_name]
            for strat in v9_harness.STRATEGIES:
                with self.subTest(condition=cond_name, strategy=strat):
                    m1 = v8_engine.simulate_v8_queue(self.graph, self.prs, strat, cfg, 1001, filtered_graph=self.fgraph)
                    m2 = v8_engine.simulate_v8_queue(
                        self.graph, list(reversed(self.prs)), strat, cfg, 1001, filtered_graph=self.fgraph
                    )
                    self.assertEqual(m1.to_dict(), m2.to_dict())
                    self.assertEqual(m1.escaped_defects, 0)
                    self.assertEqual(m1.merged_prs + m1.rejected_prs, len(self.prs))
                    self.assertEqual(
                        m1.rejected_defective + m1.rejected_persistent_conflict + m1.rejected_false_flaky,
                        m1.rejected_prs,
                    )

    def test_ci_duration_increases_mean_ci_minutes_per_run_without_changing_cone_density(self) -> None:
        m12 = v8_engine.simulate_v8_queue(
            self.graph, self.prs, "SSMQ-FilteredCone", v9_harness.CONDITIONS["nominal_default"], 1001, filtered_graph=self.fgraph
        )
        m20 = v8_engine.simulate_v8_queue(
            self.graph, self.prs, "SSMQ-FilteredCone", v9_harness.CONDITIONS["nominal_ci20"], 1001, filtered_graph=self.fgraph
        )
        self.assertEqual(m12.observed_graph_edge_density, m20.observed_graph_edge_density)
        self.assertGreater(m20.ci_minutes_per_merged_pr, m12.ci_minutes_per_merged_pr)


if __name__ == "__main__":
    unittest.main()
