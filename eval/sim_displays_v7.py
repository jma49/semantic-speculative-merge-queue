"""Deterministic paired v7 simulation estimates and manuscript tables from sealed exact-tree data."""
from collections import defaultdict
from hashlib import sha256
import json
from math import comb
from pathlib import Path
from random import Random
from statistics import fmean

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = {
    "ParallelSingletonRetry": "Parallel singleton + retry",
    "StaticBatchBisect": "FIFO batch + bisect",
    "FIFOBatchRetry": "FIFO batch + retry",
    "SSMQ-NoGraph": "FIFO + retry + blame",
    "SSMQ-NoCone": "Risk + retry + blame (NoCone)",
    "DisjointBatchRetry": "Disjoint batch + retry",
    "SSMQ-NoRisk": "SSMQ without risk lanes",
    "SSMQ-NoBlame": "SSMQ without blame",
    "SSMQ": "SSMQ (full)",
    "FIFOOutcomeTree": "FIFO outcome tree (stacked)",
}
FIELDS = (
    "p95_latency_min",
    "throughput_prs_per_hr",
    "merged_prs",
    "ci_minutes_per_merged_pr",
    "false_rejection_rate",
    "mean_root_batch_size",
    "invalidation_rate",
    "stale_rechecks",
    "realized_graph_recall",
    "realized_graph_precision",
    "observed_graph_edge_density",
    "canceled_ci_minutes",
    "escaped_defects",
)


def load(suite):
    directory = ROOT / "runs" / f"v7_sim_{suite}"
    manifest = json.loads((directory / "manifest.json").read_text())
    data = (directory / "metrics.json").read_bytes()
    if sha256(data).hexdigest() != manifest["artifact_sha256"]:
        raise ValueError(f"Invalid sealed simulation suite {suite}")
    rows = json.loads(data)
    if len(rows) != manifest["record_count"]:
        raise ValueError("Incorrect record count")
    return rows


def means(rows):
    return {
        field: fmean(values) if (values := [r[field] for r in rows if r[field] is not None]) else None
        for field in FIELDS
    }


def contrast(rows, baseline, metric):
    groups = defaultdict(dict)
    for row in rows:
        key = (row["repo"], row["seed"])
        if key in groups[row["strategy"]]:
            raise ValueError("Duplicate strategy within paired block")
        groups[row["strategy"]][key] = row[metric]
    left, right = groups["SSMQ"], groups[baseline]
    if not left or left.keys() != right.keys():
        raise ValueError("Incomplete paired contrasts")
    repos = sorted({repo for repo, _ in left})
    seeds = sorted({seed for _, seed in left})
    by_repo = {repo: [left[repo, seed] - right[repo, seed] for seed in seeds] for repo in repos}
    blocks = [fmean(pair) for pair in zip(*by_repo.values())]
    rng = Random(11629)
    bootstrap = sorted(fmean(rng.choices(blocks, k=len(blocks))) for _ in range(10000))
    n = sum(x != 0 for x in blocks)
    tail = min(sum(x > 0 for x in blocks), sum(x < 0 for x in blocks))
    sign_p = min(1, 2 * sum(comb(n, i) for i in range(tail + 1)) / 2**n)
    return dict(
        difference=fmean(blocks),
        ci95=[bootstrap[249], bootstrap[9749]],
        sign_test_p=sign_p,
        by_repo={repo: fmean(values) for repo, values in by_repo.items()},
    )


def main():
    out = {}
    for suite in ("main", "recall", "proxy"):
        all_rows = load(suite)
        out[suite] = {}
        for condition in sorted({r["condition"] for r in all_rows}):
            rows = [r for r in all_rows if r["condition"] == condition]
            strategies = sorted({r["strategy"] for r in rows})
            repos = sorted({r["repo"] for r in rows})
            by_strategy = {s: means([r for r in rows if r["strategy"] == s]) for s in strategies}
            by_repo_strategy = {
                repo: {s: means([r for r in rows if r["repo"] == repo and r["strategy"] == s]) for s in strategies}
                for repo in repos
            }
            contrasts = {s: contrast(rows, s, "p95_latency_min") for s in strategies if s != "SSMQ"}
            out[suite][condition] = dict(
                by_strategy=by_strategy,
                by_repo_strategy=by_repo_strategy,
                p95_difference=contrasts,
            )
    (ROOT / "results/sim_v7.json").write_text(json.dumps(out, indent=2, allow_nan=False) + "\n")

    # Table II: Main exact-tree comparison across nominal (5x) and saturated (15x) loads
    tex = [
        r"\begin{table*}[t]\centering\scriptsize",
        r"\caption{Paired exact-tree simulation ($W{=}8$, decoupled scheduler priors) across nominal ($5\times$, 30~PRs/hr) and saturated ($15\times$, 90~PRs/hr) arrival loads. Every policy enforces identical exact-tree commit verification (zero escapes). $\Delta$ is SSMQ minus row policy (positive means SSMQ is slower), with 95\% paired-seed bootstrap intervals across all three repositories. Width is mean root batch size; FRR is clean false-rejection rate; Acc.\ is mean accepted PRs; CI/m is runner-minutes per merge; Inv.\ is invalidated run share.}\label{tab:v7sim}",
        r"\begin{tabular}{lrrrrrrr}\toprule",
        r"Policy & P95 (min) & $\Delta$ [95\% CI] & Width & FRR (\%) & Acc. & CI/m & Inv. (\%) \\ \midrule",
    ]
    for idx, (cond, header) in enumerate((
        ("nominal", r"\multicolumn{8}{l}{\textit{Nominal load ($5\times$, 30 PRs/hr, 20 seeds $\times$ 3 repos)}} \\"),
        ("saturated", r"\multicolumn{8}{l}{\textit{Saturated load ($15\times$, 90 PRs/hr, 20 seeds $\times$ 3 repos)}} \\"),
    )):
        if idx > 0:
            tex.append(r"\midrule")
        tex.append(header)
        res = out["main"][cond]
        for key, label in STRATEGIES.items():
            stats = res["by_strategy"][key]
            effect = res["p95_difference"].get(key)
            delta = "---"
            if effect:
                lo, hi = effect["ci95"]
                delta = f"{effect['difference']:+.1f} [{lo:+.1f}, {hi:+.1f}]"
            tex.append(
                f"{label} & {stats['p95_latency_min']:.1f} & {delta} & "
                f"{stats['mean_root_batch_size']:.2f} & {stats['false_rejection_rate']*100:.2f} & "
                f"{stats['merged_prs']:.1f} & {stats['ci_minutes_per_merged_pr']:.1f} & "
                f"{stats['invalidation_rate']*100:.1f}" + r" \\"
            )
    tex.append(r"\bottomrule\end{tabular}\end{table*}")

    # Table III: Per-repository contrasts (disclosing heterogeneity hidden by pooled averages)
    tex.extend([
        r"\begin{table}[t]\centering\scriptsize",
        r"\caption{Per-repository P95 contrasts (SSMQ minus comparator, minutes) and warm-up cone edge density ($D$) / SSMQ root batch width ($w$). Positive values mean SSMQ is slower.}\label{tab:v7perrepo}",
        r"\setlength{\tabcolsep}{3.5pt}",
        r"\begin{tabular}{lrrr}\toprule",
        r"Regime / Comparator & \texttt{k8s} & \texttt{TS} & \texttt{next.js} \\ \midrule",
    ])
    nom_r = out["main"]["nominal"]["by_repo_strategy"]
    tex.append(
        f"Warm-up cone edge density $D$ (\\%) & "
        f"{nom_r['kubernetes']['SSMQ']['observed_graph_edge_density']*100:.1f} & "
        f"{nom_r['typescript']['SSMQ']['observed_graph_edge_density']*100:.1f} & "
        f"{nom_r['nextjs']['SSMQ']['observed_graph_edge_density']*100:.1f}" + r" \\"
    )
    tex.append(
        f"SSMQ nominal root width $w$ & "
        f"{nom_r['kubernetes']['SSMQ']['mean_root_batch_size']:.2f} & "
        f"{nom_r['typescript']['SSMQ']['mean_root_batch_size']:.2f} & "
        f"{nom_r['nextjs']['SSMQ']['mean_root_batch_size']:.2f}" + r" \\ \midrule"
    )
    per_repo_rows = (
        ("main", "nominal", "ParallelSingletonRetry", "Nom.: vs.\\ Parallel singleton"),
        ("main", "nominal", "StaticBatchBisect", "Nom.: vs.\\ FIFO + bisect"),
        ("main", "nominal", "FIFOBatchRetry", "Nom.: vs.\\ FIFO + retry"),
        ("main", "nominal", "SSMQ-NoCone", "Nom.: vs.\\ NoCone"),
        ("main", "nominal", "DisjointBatchRetry", "Nom.: vs.\\ Disjoint + retry"),
        ("main", "nominal", "FIFOOutcomeTree", "Nom.: vs.\\ Outcome tree"),
        ("main", "saturated", "FIFOBatchRetry", "Sat.: vs.\\ FIFO + retry"),
        ("main", "saturated", "SSMQ-NoCone", "Sat.: vs.\\ NoCone"),
        ("main", "saturated", "FIFOOutcomeTree", "Sat.: vs.\\ Outcome tree"),
        ("proxy", "directory_proxy", "SSMQ-NoCone", "Dir.\\ proxy: vs.\\ NoCone"),
        ("proxy", "directory_proxy", "SSMQ-NoGraph", "Dir.\\ proxy: vs.\\ NoGraph"),
    )
    for suite, cond, comp, label in per_repo_rows:
        by_r = out[suite][cond]["p95_difference"][comp]["by_repo"]
        tex.append(
            f"{label} & {by_r['kubernetes']:+.1f} & {by_r['typescript']:+.1f} & {by_r['nextjs']:+.1f}" + r" \\"
        )
    tex.append(r"\bottomrule\end{tabular}\end{table}")

    # Table IV: Cone sensitivity under exact-tree semantics (with root width and FRR)
    tex.extend([
        r"\begin{table}[t]\centering\scriptsize",
        r"\caption{Cone exclusion sensitivity under exact-tree commit. $\Delta$ is SSMQ minus SSMQ-NoCone (same risk, retry, and blame rules; positive means cone exclusion is slower). Width and FRR show SSMQ / NoCone.}\label{tab:v7control}",
        r"\setlength{\tabcolsep}{0.8pt}",
        r"\begin{tabular}{lrrrrr}\toprule",
        r"Condition & Rec. & Prec. & $\Delta$ P95 [95\% CI] & Width & FRR (\%) \\ \midrule",
    ])
    labels = (
        ("recall", "recall_0.0", r"Budget 0\%"),
        ("recall", "recall_0.4", r"Budget 40\%"),
        ("recall", "recall_0.6", r"Budget 60\%"),
        ("recall", "recall_0.8", r"Budget 80\%"),
        ("recall", "recall_1.0", r"Budget 100\%"),
        ("proxy", "directory_proxy", "Directory (nom.)"),
        ("proxy", "saturated_directory", "Directory (sat.)"),
        ("proxy", "directory_no_flakes", "Dir., no flakes"),
        ("proxy", "directory_no_persistence", "Dir., transient"),
        ("proxy", "uniform_pairs", "Uniform pairs"),
    )
    for suite, cond, label in labels:
        entry = out[suite][cond]
        s_stats = entry["by_strategy"]["SSMQ"]
        nc_stats = entry["by_strategy"]["SSMQ-NoCone"]
        e = entry["p95_difference"]["SSMQ-NoCone"]
        lo, hi = e["ci95"]
        tex.append(
            f"{label} & {s_stats['realized_graph_recall']:.2f} & {s_stats['realized_graph_precision']:.2f} & "
            f"{e['difference']:+.1f} [{lo:+.1f}, {hi:+.1f}] & "
            f"{s_stats['mean_root_batch_size']:.2f}/{nc_stats['mean_root_batch_size']:.2f} & "
            f"{s_stats['false_rejection_rate']*100:.1f}/{nc_stats['false_rejection_rate']*100:.1f}" + r" \\"
        )
    tex.append(r"\bottomrule\end{tabular}\end{table}")
    (ROOT / "paper/generated/sim_v7.tex").write_text("\n".join(tex) + "\n")


if __name__ == "__main__":
    main()
