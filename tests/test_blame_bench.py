#!/usr/bin/env python3
"""Unit tests for the real executable AST/stack-trace causal blame micro-benchmark (v3)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ssmq.blame_bench import (
    CLEAN_PRS,
    COFILE_DISTRACTOR_PRS,
    DEFECT_PRS,
    SEMANTIC_CONFLICT_PAIRS,
    execute_batch,
    run_blame_benchmark,
)


class BlameBenchTests(unittest.TestCase):
    def test_base_clean_and_cofile_distractors_pass_real_unittest(self) -> None:
        ok, out = execute_batch([])
        self.assertTrue(ok, msg=out)
        ok_clean, out_clean = execute_batch(CLEAN_PRS[:3])
        self.assertTrue(ok_clean, msg=out_clean)
        for mod, pr in COFILE_DISTRACTOR_PRS.items():
            ok_d, out_d = execute_batch([pr])
            self.assertTrue(ok_d, msg=f"Co-file distractor for {mod} failed:\n{out_d}")

    def test_single_defect_fails_with_cofile_distractor(self) -> None:
        for defect in DEFECT_PRS:
            mod = next(iter(defect.files))
            ok, _ = execute_batch([COFILE_DISTRACTOR_PRS[mod], defect])
            self.assertFalse(ok, msg=f"{defect.pr_id} should still fail alongside co-file distractor")

    def test_semantic_conflict_pairs_pass_individually_and_fail_combined(self) -> None:
        for pr_a, pr_b in SEMANTIC_CONFLICT_PAIRS:
            ok_a, out_a = execute_batch([pr_a])
            ok_b, out_b = execute_batch([pr_b])
            ok_ab, _ = execute_batch([pr_a, pr_b])
            self.assertTrue(ok_a, msg=f"{pr_a.pr_id} failed alone:\n{out_a}")
            self.assertTrue(ok_b, msg=f"{pr_b.pr_id} failed alone:\n{out_b}")
            self.assertFalse(ok_ab, msg=f"{pr_a.pr_id} + {pr_b.pr_id} should fail combined")

    def test_ast_stack_ranker_beats_all_baselines_including_file_overlap(self) -> None:
        summary, records = run_blame_benchmark(seed=2026)
        self.assertEqual(summary.total_failing_batches, 64)
        self.assertEqual(summary.single_defect_batches, 24)
        self.assertEqual(summary.cofile_distractor_batches, 16)
        self.assertEqual(summary.semantic_conflict_batches, 24)
        self.assertGreater(summary.ast_stack_top1_acc, summary.file_overlap_top1_acc)
        self.assertGreater(summary.ast_stack_top1_acc, summary.random_top1_acc)
        self.assertGreater(summary.ast_stack_top1_acc, summary.churn_risk_top1_acc)
        self.assertGreater(summary.ast_stack_cofile_top1_acc, summary.file_overlap_cofile_top1_acc)
        self.assertGreaterEqual(summary.ast_stack_top1_acc, 0.80)
        self.assertEqual(len(records), summary.total_failing_batches)


if __name__ == "__main__":
    unittest.main()
