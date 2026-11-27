"""Rebuild the v7 manuscript's summary numbers and tables from the sealed observations."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    for script in ("analyze_traces", "git_displays_v7", "sim_displays_v7"):
        subprocess.run([sys.executable, "-B", str(ROOT / "eval" / (script + ".py"))], check=True)
    traces = json.loads((ROOT / "results/trace_stats.json").read_text())
    sim = json.loads((ROOT / "results/sim_v7.json").read_text())
    git_summary = json.loads((ROOT / "results/git_v7.json").read_text())
    stale_rows = json.loads((ROOT / "runs/v7_exp11_concurrent_git_safety/metrics.json").read_text())

    nom = sim["main"]["nominal"]
    sat = sim["main"]["saturated"]
    dprox = sim["proxy"]["directory_proxy"]
    gf = git_summary["git_factorial"]
    bs = git_summary["blame_microbench"]
    qb = git_summary["quixbugs"]

    def fmt_ci(entry):
        return round(entry["difference"], 1), round(entry["ci95"][0], 1), round(entry["ci95"][1], 1)

    nom_vs_nocone, nom_vs_nocone_lo, nom_vs_nocone_hi = fmt_ci(nom["p95_difference"]["SSMQ-NoCone"])
    nom_vs_fifo, nom_vs_fifo_lo, nom_vs_fifo_hi = fmt_ci(nom["p95_difference"]["StaticBatchBisect"])
    nom_vs_fifor, nom_vs_fifor_lo, nom_vs_fifor_hi = fmt_ci(nom["p95_difference"]["FIFOBatchRetry"])
    nom_vs_sing, nom_vs_sing_lo, nom_vs_sing_hi = fmt_ci(nom["p95_difference"]["ParallelSingletonRetry"])
    nom_vs_disj, nom_vs_disj_lo, nom_vs_disj_hi = fmt_ci(nom["p95_difference"]["DisjointBatchRetry"])
    nom_vs_tree, nom_vs_tree_lo, nom_vs_tree_hi = fmt_ci(nom["p95_difference"]["FIFOOutcomeTree"])
    sat_vs_nocone, sat_vs_nocone_lo, sat_vs_nocone_hi = fmt_ci(sat["p95_difference"]["SSMQ-NoCone"])
    sat_vs_fifor, sat_vs_fifor_lo, sat_vs_fifor_hi = fmt_ci(sat["p95_difference"]["FIFOBatchRetry"])
    sat_vs_tree, sat_vs_tree_lo, sat_vs_tree_hi = fmt_ci(sat["p95_difference"]["FIFOOutcomeTree"])
    dir_vs_nocone, dir_vs_nocone_lo, dir_vs_nocone_hi = fmt_ci(dprox["p95_difference"]["SSMQ-NoCone"])
    dir_vs_nograph, dir_vs_nograph_lo, dir_vs_nograph_hi = fmt_ci(dprox["p95_difference"]["SSMQ-NoGraph"])

    values = {
        "TraceCount": traces["total_prs"],
        # Nominal exact-tree P95 & deltas
        "NomSsmqP": f"{nom['by_strategy']['SSMQ']['p95_latency_min']:.1f}",
        "NomNoConeP": f"{nom['by_strategy']['SSMQ-NoCone']['p95_latency_min']:.1f}",
        "NomFifoP": f"{nom['by_strategy']['StaticBatchBisect']['p95_latency_min']:.1f}",
        "NomFifoRetryP": f"{nom['by_strategy']['FIFOBatchRetry']['p95_latency_min']:.1f}",
        "NomSingP": f"{nom['by_strategy']['ParallelSingletonRetry']['p95_latency_min']:.1f}",
        "NomTreeP": f"{nom['by_strategy']['FIFOOutcomeTree']['p95_latency_min']:.1f}",
        "NomSsmqWidth": f"{nom['by_strategy']['SSMQ']['mean_root_batch_size']:.2f}",
        "NomNoConeWidth": f"{nom['by_strategy']['SSMQ-NoCone']['mean_root_batch_size']:.2f}",
        "NomFifoRetryWidth": f"{nom['by_strategy']['FIFOBatchRetry']['mean_root_batch_size']:.2f}",
        "NomSsmqFrr": f"{nom['by_strategy']['SSMQ']['false_rejection_rate']*100:.2f}",
        "NomNoConeFrr": f"{nom['by_strategy']['SSMQ-NoCone']['false_rejection_rate']*100:.2f}",
        "NomFifoFrr": f"{nom['by_strategy']['StaticBatchBisect']['false_rejection_rate']*100:.2f}",
        "NomTreeFrr": f"{nom['by_strategy']['FIFOOutcomeTree']['false_rejection_rate']*100:.2f}",
        "NomSsmqCi": f"{nom['by_strategy']['SSMQ']['ci_minutes_per_merged_pr']:.1f}",
        "NomTreeCi": f"{nom['by_strategy']['FIFOOutcomeTree']['ci_minutes_per_merged_pr']:.1f}",
        "NomSsmqInv": f"{nom['by_strategy']['SSMQ']['invalidation_rate']*100:.1f}",
        "NomVsNoCone": f"{nom_vs_nocone:.1f}",
        "NomVsNoConeLo": f"{nom_vs_nocone_lo:.1f}",
        "NomVsNoConeHi": f"{nom_vs_nocone_hi:.1f}",
        "NomVsFifo": f"{nom_vs_fifo:.1f}",
        "NomVsFifoLo": f"{nom_vs_fifo_lo:.1f}",
        "NomVsFifoHi": f"{nom_vs_fifo_hi:.1f}",
        "NomVsFifoR": f"{nom_vs_fifor:.1f}",
        "NomVsFifoRLo": f"{nom_vs_fifor_lo:.1f}",
        "NomVsFifoRHi": f"{nom_vs_fifor_hi:.1f}",
        "NomVsSingGain": f"{-nom_vs_sing:.1f}",
        "NomVsSingLo": f"{-nom_vs_sing_hi:.1f}",
        "NomVsSingHi": f"{-nom_vs_sing_lo:.1f}",
        "NomVsDisj": f"{nom_vs_disj:+.1f}",
        "NomVsDisjLo": f"{nom_vs_disj_lo:+.1f}",
        "NomVsDisjHi": f"{nom_vs_disj_hi:+.1f}",
        "NomVsTree": f"{nom_vs_tree:.1f}",
        "NomVsTreeLo": f"{nom_vs_tree_lo:.1f}",
        "NomVsTreeHi": f"{nom_vs_tree_hi:.1f}",
        # Saturated exact-tree P95 & deltas
        "SatSsmqP": f"{sat['by_strategy']['SSMQ']['p95_latency_min']:.1f}",
        "SatNoConeP": f"{sat['by_strategy']['SSMQ-NoCone']['p95_latency_min']:.1f}",
        "SatFifoRetryP": f"{sat['by_strategy']['FIFOBatchRetry']['p95_latency_min']:.1f}",
        "SatSingP": f"{sat['by_strategy']['ParallelSingletonRetry']['p95_latency_min']:.1f}",
        "SatTreeP": f"{sat['by_strategy']['FIFOOutcomeTree']['p95_latency_min']:.1f}",
        "SatVsNoCone": f"{sat_vs_nocone:.1f}",
        "SatVsNoConeLo": f"{sat_vs_nocone_lo:.1f}",
        "SatVsNoConeHi": f"{sat_vs_nocone_hi:.1f}",
        "SatVsFifoR": f"{sat_vs_fifor:.1f}",
        "SatVsFifoRLo": f"{sat_vs_fifor_lo:.1f}",
        "SatVsFifoRHi": f"{sat_vs_fifor_hi:.1f}",
        "SatVsTree": f"{sat_vs_tree:.1f}",
        "SatVsTreeLo": f"{sat_vs_tree_lo:.1f}",
        "SatVsTreeHi": f"{sat_vs_tree_hi:.1f}",
        # Directory proxy
        "DirVsNoCone": f"{dir_vs_nocone:.1f}",
        "DirVsNoConeLo": f"{dir_vs_nocone_lo:.1f}",
        "DirVsNoConeHi": f"{dir_vs_nocone_hi:.1f}",
        "DirVsNoGraph": f"{dir_vs_nograph:.1f}",
        "DirVsNoGraphLo": f"{dir_vs_nograph_lo:.1f}",
        "DirVsNoGraphHi": f"{dir_vs_nograph_hi:.1f}",
        # Counterbalanced Git factorial (20 paired streams per arm)
        "GitDisjAstTests": gf["Disjoint+ASTStack"]["total_unittest_runs"],
        "GitDisjFileTests": gf["Disjoint+FileTrivia"]["total_unittest_runs"],
        "GitDisjBisectTests": gf["Disjoint+Bisect"]["total_unittest_runs"],
        "GitFifoAstTests": gf["FIFO+ASTStack"]["total_unittest_runs"],
        "GitFifoFileTests": gf["FIFO+FileTrivia"]["total_unittest_runs"],
        "GitFifoBisectTests": gf["FIFO+Bisect"]["total_unittest_runs"],
        # Concurrent stale-snapshot safety
        "StalePairsN": len(stale_rows),
        "StaleUnsafeEscapes": sum(int(r["unsafe_stale_commit_escapes"]) for r in stale_rows),
        "StaleRecheckCatches": sum(int(r["ssmq_stale_recheck_catches"]) for r in stale_rows),
        # Authored 86-batch blame micro-benchmark
        "BlameTotalN": bs["total_failing_batches"],
        "BlameAstLoo": f"{bs['ast_stack_loo_recovery_rate']*100:.1f}",
        "BlameFileLoo": f"{bs['file_trivia_loo_recovery_rate']*100:.1f}",
        "BlameAstSib": f"{bs['ast_stack_sibling_top1_acc']*100:.1f}",
        "BlameFileSib": f"{bs['file_trivia_sibling_top1_acc']*100:.1f}",
        # Independent QuixBugs
        "QuixSiblingN": qb["sibling"]["n"],
        "QuixSiblingAst": qb["sibling"]["ast_loo"],
        "QuixSiblingFile": qb["sibling"]["file_loo"],
        "QuixSiblingBlind": qb["sibling"]["blind_loo"],
        "QuixSameN": qb["same_function"]["n"],
        "QuixSameAst": qb["same_function"]["ast_loo"],
        "QuixSameFile": qb["same_function"]["file_loo"],
        "QuixDoubleN": qb["two_faults"]["n"],
        "QuixDoubleAst": qb["two_faults"]["ast_loo"],
    }

    (ROOT / "results/v7_paper.json").write_text(json.dumps(values, indent=2) + "\n")
    (ROOT / "paper/generated/v7.tex").write_text(
        "".join("\\newcommand{\\%s}{%s}\n" % (name, value) for name, value in values.items())
    )


if __name__ == "__main__":
    main()
