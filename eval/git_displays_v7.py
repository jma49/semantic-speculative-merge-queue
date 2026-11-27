"""Aggregate v7 counterbalanced Git factorial, authored blame benchmark, and QuixBugs tables."""
from collections import defaultdict
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    git_rows = json.loads((ROOT / "runs/v7_exp10_real_git_replay/metrics.json").read_text())
    groups = defaultdict(list)
    for row in git_rows:
        groups[(row["packing"], row["recovery"])].append(row)
    fields = (
        "candidate_unittest_runs",
        "total_unittest_runs",
        "total_decompositions",
        "blame_invocations",
        "blame_one_step_hits",
        "total_git_merges",
        "git_merge_conflicts",
        "merged_prs",
        "real_escaped_defects",
    )
    summary = {}
    table = [
        r"\begin{table}[t]\centering\scriptsize",
        r"\caption{Counterbalanced Git factorial totals across 20 paired streams (10 seeds $\times$ 2 neutral PR-ID polarities) with matched $1.5\times$ direct-callee weighting. Tests include the final branch check; Splits count both bisections and ranker steps.}\label{tab:gitfactorial}",
        r"\begin{tabular}{llrrrr}\toprule",
        r"Packing & Recovery & Tests & Splits & Hits & Git attempts \\ \midrule",
    ]
    order = (
        ("FIFO", "Bisect"),
        ("FIFO", "FileTrivia"),
        ("FIFO", "ASTStack"),
        ("Disjoint", "Bisect"),
        ("Disjoint", "FileTrivia"),
        ("Disjoint", "ASTStack"),
    )
    for packing, recovery in order:
        arm = groups[(packing, recovery)]
        totals = {field: sum(r[field] for r in arm) for field in fields}
        by_pol = {
            pol: {
                field: sum(r[field] for r in arm if r["polarity"] == pol)
                for field in ("total_unittest_runs", "total_decompositions", "blame_one_step_hits", "blame_invocations")
            }
            for pol in ("asc", "desc")
        }
        summary[packing + "+" + recovery] = {**totals, "by_polarity": by_pol}
        label = {"ASTStack": "AST", "FileTrivia": "File", "Bisect": "Bisect"}[recovery]
        hits_str = "---" if recovery == "Bisect" else f"{totals['blame_one_step_hits']}/{totals['blame_invocations']}"
        table.append(
            f"{packing} & {label} & {totals['total_unittest_runs']} & "
            f"{totals['total_decompositions']} & {hits_str} & {totals['total_git_merges']} " + r"\\"
        )
    table.append(r"\bottomrule\end{tabular}\end{table}")

    # Authored 86-batch blame benchmark + independent 36-case QuixBugs attribution table
    blame_payload = json.loads((ROOT / "runs/v7_exp0_blame_microbench/metrics.json").read_text())
    bs = blame_payload["summary"]
    qb_cases = json.loads((ROOT / "runs/v7_exp12_quixbugs/metrics.json").read_text())["records"]
    qb_summary = {}
    for kind in ("sibling", "same_function", "two_faults"):
        sub = [c for c in qb_cases if c["kind"] == kind]
        qb_summary[kind] = {
            "n": len(sub),
            "ast_loo": sum(c["outcomes"]["full_ast"]["complement_pass"] for c in sub),
            "file_loo": sum(c["outcomes"]["full_file"]["complement_pass"] for c in sub),
            "blind_loo": sum(c["outcomes"]["absent_ast"]["complement_pass"] for c in sub),
        }

    table.extend([
        r"\begin{table}[t]\centering\scriptsize",
        r"\caption{Attribution leave-one-out recovery and Top-1 accuracy with matched $1.5\times$ direct-callee weighting: authored 86-batch Python fixture (top) and 6 independent upstream QuixBugs faults with paired ID reversals (bottom).}\label{tab:v7blame}",
        r"\setlength{\tabcolsep}{2pt}",
        r"\begin{tabular}{lrrr}\toprule",
        r"Fixture / Stratum ($N$) & AST & File (matched) & Notes \\ \midrule",
        f"Authored: disjoint defects ({bs['single_defect_batches']}) & {bs['ast_stack_disjoint_top1_acc']*100:.1f}\\% & {bs['file_trivia_disjoint_top1_acc']*100:.1f}\\% & Top-1 \\\\",
        f"Authored: co-file comments ({bs['cofile_comment_batches']}) & {bs['ast_stack_comment_top1_acc']*100:.1f}\\% & {bs['file_trivia_comment_top1_acc']*100:.1f}\\% & Top-1 \\\\",
        f"Authored: co-file siblings ({bs['cofile_sibling_batches']}) & {bs['ast_stack_sibling_top1_acc']*100:.1f}\\% & {bs['file_trivia_sibling_top1_acc']*100:.1f}\\% & Top-1 \\\\",
        f"Authored: semantic pairs ({bs['semantic_conflict_batches']}) & {bs['ast_stack_sem_top1_acc']*100:.1f}\\% & {bs['file_trivia_sem_top1_acc']*100:.1f}\\% & Top-1 \\\\",
        f"Authored: pooled LOO ({bs['total_failing_batches']}) & {bs['ast_stack_loo_recovery_rate']*100:.1f}\\% & {bs['file_trivia_loo_recovery_rate']*100:.1f}\\% & 1-step pass \\\\ \\midrule",
        f"QuixBugs: sibling distractor ({qb_summary['sibling']['n']}) & {qb_summary['sibling']['ast_loo']}/{qb_summary['sibling']['n']} & {qb_summary['sibling']['file_loo']}/{qb_summary['sibling']['n']} & Blind: {qb_summary['sibling']['blind_loo']}/{qb_summary['sibling']['n']} \\\\",
        f"QuixBugs: same-function ({qb_summary['same_function']['n']}) & {qb_summary['same_function']['ast_loo']}/{qb_summary['same_function']['n']} & {qb_summary['same_function']['file_loo']}/{qb_summary['same_function']['n']} & Tie \\\\",
        f"QuixBugs: two faults ({qb_summary['two_faults']['n']}) & {qb_summary['two_faults']['ast_loo']}/{qb_summary['two_faults']['n']} & {qb_summary['two_faults']['file_loo']}/{qb_summary['two_faults']['n']} & Multi-fault \\\\",
        r"\bottomrule\end{tabular}\end{table}",
    ])

    (ROOT / "paper/generated/tables/git_v7.tex").write_text("\n".join(table) + "\n")
    (ROOT / "results/git_v7.json").write_text(
        json.dumps({"git_factorial": summary, "blame_microbench": bs, "quixbugs": qb_summary}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
