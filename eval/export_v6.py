"""Rebuild the manuscript's summary numbers and tables from the distributed observations."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    for script in ("analyze_traces", "git_displays_v6", "sim_displays_v6"):
        subprocess.run([sys.executable, "-B", str(ROOT / "eval" / (script + ".py"))], check=True)
    traces = json.loads((ROOT / "results/trace_stats.json").read_text())
    sim = json.loads((ROOT / "results/sim_v6.json").read_text())
    git = json.loads((ROOT / "runs/v6_exp10_real_git_replay/metrics.json").read_text())
    contrast = sim["main"]["default"]["p95_difference"]["DisjointBatchRetry"]
    control = sim["proxy"]["directory_proxy"]["p95_difference"]["SSMQ-NoCone"]
    totals = {policy: sum(row["total_unittest_runs"] for row in git if row["strategy"] == policy)
              for policy in ("Disjoint+ASTStack", "Disjoint+FileTrivia")}
    values = {
        "TraceCount": traces["total_prs"],
        "DisjointAdvantage": round(-contrast["difference"], 1),
        "DisjointLow": round(-contrast["ci95"][1], 1),
        "DisjointHigh": round(-contrast["ci95"][0], 1),
        "DirectoryPenalty": round(control["difference"], 1),
        "DirectoryLow": round(control["ci95"][0], 1),
        "DirectoryHigh": round(control["ci95"][1], 1),
        "GitAstTests": totals["Disjoint+ASTStack"],
        "GitFileTests": totals["Disjoint+FileTrivia"],
    }
    cases = json.loads((ROOT / "runs/v6_exp12_quixbugs/metrics.json").read_text())["records"]
    for kind, macro in (("sibling", "Sibling"), ("same_function", "Same"), ("two_faults", "Double")):
        subset = [c for c in cases if c["kind"] == kind]
        values["Quix" + macro + "N"] = len(subset)
        for signal, metric in (("full_ast", "Ast"), ("full_file", "File"), ("absent_ast", "Blind")):
            values["Quix" + macro + metric] = sum(c["outcomes"][signal]["complement_pass"] for c in subset)
    (ROOT / "results/v6_paper.json").write_text(json.dumps(values, indent=2) + "\n")
    (ROOT / "paper/generated/v6.tex").write_text("".join(
        "\\newcommand{\\%s}{%s}\n" % (name, value) for name, value in values.items()))


if __name__ == "__main__":
    main()
