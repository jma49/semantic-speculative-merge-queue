#!/usr/bin/env python3
"""Unit tests for the real executable AST/stack-trace blame benchmark & real-Git replay (v5)."""
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
    PatchPR,
    _apply_single_patch_strict,
    execute_batch,
    run_blame_benchmark,
)
from ssmq.git_replay import run_git_queue_replay


class BlameBenchTests(unittest.TestCase):
    def test_unique_patch_match_enforcement(self) -> None:
        with self.assertRaises(ValueError):
            _apply_single_patch_strict("x = 1\nx = 1\n", "x = 1", "x = 2", "p", "a.py")
        with self.assertRaises(ValueError):
            _apply_single_patch_strict("x = 1\n", "missing", "x = 2", "p", "a.py")
        with self.assertRaises(ValueError):
            _apply_single_patch_strict("x = 1\n", "x = 1", "x = 1", "p", "a.py")

    def test_base_clean_comments_and_siblings_pass_real_unittest(self) -> None:
        ok, out = execute_batch([])
        self.assertTrue(ok, msg=out)
        ok_clean, out_clean = execute_batch(CLEAN_PRS[:3])
        self.assertTrue(ok_clean, msg=out_clean)
        for mod, pr in COFILE_COMMENT_PRS.items():
            ok_d, out_d = execute_batch([pr])
            self.assertTrue(ok_d, msg=f"Co-file comment PR for {mod} failed:\n{out_d}")
        for defect, sib in SIBLING_FUNCTION_PAIRINGS:
            self.assertEqual(defect.added_lines, sib.added_lines)
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

    def test_ast_stack_beats_strict_nested_file_baseline_on_symmetric_siblings(self) -> None:
        summary, records = run_blame_benchmark(seed=2026)
        self.assertEqual(summary.total_failing_batches, 86)
        self.assertEqual(summary.single_defect_batches, 24)
        self.assertEqual(summary.cofile_comment_batches, 16)
        self.assertEqual(summary.cofile_sibling_batches, 22)
        self.assertEqual(summary.semantic_conflict_batches, 24)
        # With exact 50/50 ID symmetry and identical weights/tie-break,
        # AST function-symbol ranking must strictly beat the nested file ablation!
        self.assertGreater(summary.ast_stack_sibling_top1_acc, summary.file_trivia_sibling_top1_acc)
        self.assertGreater(summary.ast_stack_top1_acc, summary.file_trivia_top1_acc)
        self.assertGreater(summary.ast_stack_loo_recovery_rate, summary.file_trivia_loo_recovery_rate)
        self.assertEqual(len(records), summary.total_failing_batches)

    def test_real_git_tree_replay_zero_escapes_and_neutral_counterbalanced_ids(self) -> None:
        from ssmq.git_replay import build_git_replay_stream

        fwd, _, _ = build_git_replay_stream(0, reverse_ids=False)
        rev, _, _ = build_git_replay_stream(0, reverse_ids=True)
        self.assertEqual([p.pr_id for p in fwd], [f"pr_{i:02d}" for i in range(len(fwd))])
        self.assertEqual([p.pr_id for p in rev], [f"pr_{len(rev) - 1 - i:02d}" for i in range(len(rev))])
        for strategy in ("StaticBatchBisect", "SSMQ"):
            m = run_git_queue_replay(strategy, seed=0, max_batch=4)
            self.assertTrue(m.final_main_tests_pass, msg=f"{strategy} broke real git main!")
            self.assertEqual(m.real_escaped_defects, 0)
            self.assertEqual(m.merged_prs + m.rejected_prs, m.total_prs)
            self.assertGreater(m.total_unittest_runs, 0)
            self.assertGreater(m.total_git_merges, 0)
            if strategy == "SSMQ":
                self.assertGreaterEqual(m.blame_invocations, 1)


if __name__ == "__main__":
    unittest.main()
