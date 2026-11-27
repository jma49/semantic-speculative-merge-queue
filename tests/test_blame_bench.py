#!/usr/bin/env python3
"""Unit tests for the real executable AST/stack-trace causal blame micro-benchmark (v4)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ssmq.blame_bench import (
    CLEAN_PRS,
    COFILE_COMMENT_PRS,
    DEFECT_PRS,
    SEMANTIC_CONFLICT_PAIRS,
    SIBLING_FUNCTION_PAIRINGS,
    execute_batch,
    run_blame_benchmark,
)


class BlameBenchTests(unittest.TestCase):
    def test_base_clean_comments_and_siblings_pass_real_unittest(self) -> None:
        ok, out = execute_batch([])
        self.assertTrue(ok, msg=out)
        ok_clean, out_clean = execute_batch(CLEAN_PRS[:3])
        self.assertTrue(ok_clean, msg=out_clean)
        for mod, pr in COFILE_COMMENT_PRS.items():
            ok_d, out_d = execute_batch([pr])
            self.assertTrue(ok_d, msg=f"Co-file comment PR for {mod} failed:\n{out_d}")
        for defect, sib in SIBLING_FUNCTION_PAIRINGS:
            ok_s, out_s = execute_batch([sib])
            self.assertTrue(ok_s, msg=f"Functional sibling PR {sib.pr_id} failed alone:\n{out_s}")
            ok_both, _ = execute_batch([sib, defect])
            self.assertFalse(ok_both, msg=f"Defect {defect.pr_id} + sibling {sib.pr_id} must fail")

    def test_semantic_conflict_pairs_pass_individually_and_fail_combined(self) -> None:
        for pr_a, pr_b in SEMANTIC_CONFLICT_PAIRS:
            ok_a, out_a = execute_batch([pr_a])
            ok_b, out_b = execute_batch([pr_b])
            ok_ab, _ = execute_batch([pr_a, pr_b])
            self.assertTrue(ok_a, msg=f"{pr_a.pr_id} failed alone:\n{out_a}")
            self.assertTrue(ok_b, msg=f"{pr_b.pr_id} failed alone:\n{out_b}")
            self.assertFalse(ok_ab, msg=f"{pr_a.pr_id} + {pr_b.pr_id} should fail combined")

    def test_ast_stack_beats_trivia_filtered_file_baseline_on_functional_siblings(self) -> None:
        summary, records = run_blame_benchmark(seed=2026)
        self.assertEqual(summary.total_failing_batches, 80)
        self.assertEqual(summary.single_defect_batches, 24)
        self.assertEqual(summary.cofile_comment_batches, 16)
        self.assertEqual(summary.cofile_sibling_batches, 16)
        self.assertEqual(summary.semantic_conflict_batches, 24)
        # AST function-symbol ranking must strictly beat trivia-filtered file overlap
        # specifically on REAL FUNCTIONAL sibling-function co-file distractors!
        self.assertGreater(summary.ast_stack_sibling_top1_acc, summary.file_trivia_sibling_top1_acc)
        self.assertGreater(summary.ast_stack_top1_acc, summary.file_trivia_top1_acc)
        self.assertGreater(summary.ast_stack_loo_recovery_rate, summary.file_trivia_loo_recovery_rate)
        self.assertEqual(len(records), summary.total_failing_batches)


if __name__ == "__main__":
    unittest.main()
