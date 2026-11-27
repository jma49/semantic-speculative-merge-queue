"""Aggregate the paired Git experiment without discarding final tests or ranker splits."""
from collections import defaultdict
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    rows = json.loads((ROOT / "runs/v6_exp10_real_git_replay/metrics.json").read_text())
    groups = defaultdict(list)
    for row in rows:
        groups[(row["packing"], row["recovery"])].append(row)
    fields = ("candidate_unittest_runs", "total_unittest_runs", "total_decompositions",
              "total_git_merges", "git_merge_conflicts", "merged_prs", "real_escaped_defects")
    summary = {}
    table = [
        r"\begin{table}[t]\centering\small",
        r"\caption{Git factorial totals over paired streams. Tests include a final check. Splits include both bisections and ranker decisions. Attempts include textual conflicts.}\label{tab:gitfactorial}",
        r"\begin{tabular}{llrrr}\toprule Packing & Recovery & Tests & Splits & Attempts \\ \midrule",
    ]
    for (packing, recovery), arm in groups.items():
        totals = {field: sum(r[field] for r in arm) for field in fields}
        summary[packing + "+" + recovery] = totals
        label = {"ASTStack": "AST", "FileTrivia": "File", "Bisect": "Bisect"}[recovery]
        table.append(f"{packing} & {label} & {totals['total_unittest_runs']} & "
                     f"{totals['total_decompositions']} & {totals['total_git_merges']} " + r"\\")
    table.append(r"\bottomrule\end{tabular}\end{table}")
    (ROOT / "paper/generated/tables/git_v6.tex").write_text("\n".join(table) + "\n")
    (ROOT / "results/git_v6.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
