"""Deterministic paired simulation estimates and manuscript tables from sealed data."""
from collections import defaultdict
from hashlib import sha256
import json
from math import comb
from pathlib import Path
from random import Random
from statistics import fmean

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = {
    "StaticBatchBisect": "FIFO batch + bisect",
    "DepAwareOutcomeTree": "Lane outcome tree",
    "DepTreeRetry": "Lane tree + retry",
    "PerPRDAGRetry": "Per-PR DAG + retry",
    "DisjointBatchRetry": "Disjoint batch + retry",
    "SSMQ": "SSMQ",
}
FIELDS = ("p95_latency_min", "throughput_prs_per_hr", "merged_prs", "ci_minutes_per_merged_pr",
          "realized_graph_recall", "realized_graph_precision", "observed_graph_edge_density",
          "bridge_wait_min_total", "canceled_ci_minutes", "false_rejection_rate", "escaped_defects")


def load(suite):
    directory = ROOT / "runs" / f"v6_sim_{suite}"
    manifest = json.loads((directory / "manifest.json").read_text())
    data = (directory / "metrics.json").read_bytes()
    if sha256(data).hexdigest() != manifest["artifact_sha256"]:
        raise ValueError(f"Invalid sealed simulation suite {suite}")
    rows = json.loads(data)
    if len(rows) != manifest["record_count"]:
        raise ValueError("Incorrect record count")
    return rows


def means(rows):
    return {field: fmean(values) if (values := [r[field] for r in rows if r[field] is not None]) else None
            for field in FIELDS}


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
    # All three traces retained at equal weight. Resample whole paired seed blocks.
    rng = Random(11629)
    bootstrap = sorted(fmean(rng.choices(blocks, k=len(blocks))) for _ in range(10000))
    n = sum(x != 0 for x in blocks)
    tail = min(sum(x > 0 for x in blocks), sum(x < 0 for x in blocks))
    sign_p = min(1, 2 * sum(comb(n, i) for i in range(tail + 1)) / 2**n)
    return dict(difference=fmean(blocks), ci95=[bootstrap[249], bootstrap[9749]],
                sign_test_p=sign_p, by_repo={repo: fmean(values) for repo, values in by_repo.items()})


def main():
    out = {}
    for suite in ("main", "recall", "proxy"):
        all_rows = load(suite)
        out[suite] = {}
        for condition in sorted({r["condition"] for r in all_rows}):
            rows = [r for r in all_rows if r["condition"] == condition]
            by_strategy = {s: means([r for r in rows if r["strategy"] == s]) for s in sorted({r["strategy"] for r in rows})}
            contrasts = {s: contrast(rows, s, "p95_latency_min") for s in by_strategy if s != "SSMQ"}
            out[suite][condition] = dict(by_strategy=by_strategy, p95_difference=contrasts)
    (ROOT / "results/sim_v6.json").write_text(json.dumps(out, indent=2, allow_nan=False) + "\n")
    main_results = out["main"]["default"]
    tex = [r"\begin{table*}[t]\centering\small",
           r"\caption{Paired simulation on the exhaustive merge windows. P95 is the arithmetic mean of per-run survivor P95 latency. $\Delta$ is SSMQ minus the row policy (minutes), with percentile intervals resampling paired seeds while retaining all repositories. Accepted is the mean count per stream.}\label{tab:v6sim}",
           r"\begin{tabular}{lrrrrr}\toprule Policy & P95 (min) & $\Delta$ [95\% CI] & Accepted & Merges/hr & Runner-min/merge \\ \midrule"]
    for key, label in STRATEGIES.items():
        stats = main_results["by_strategy"][key]
        effect = main_results["p95_difference"].get(key)
        delta = "---"
        if effect:
            lo, hi = effect["ci95"]
            delta = f"{effect['difference']:+.1f} [{lo:+.1f}, {hi:+.1f}]"
        tex.append(f"{label} & {stats['p95_latency_min']:.1f} & {delta} & {stats['merged_prs']:.1f} & {stats['throughput_prs_per_hr']:.1f} & {stats['ci_minutes_per_merged_pr']:.1f}" + r" \\")
    tex.extend([r"\bottomrule\end{tabular}\end{table*}",
                r"\begin{table}[t]\centering\scriptsize",
                r"\caption{Cone sensitivity. $\Delta$ compares SSMQ with cone exclusion disabled, using the same risk and retry rules. Precision and recall refer to injected pair labels.}\label{tab:v6control}",
                r"\begin{tabular}{lrrr}\toprule Label condition & Rec. & Prec. & $\Delta$ min [95\% CI] \\ \midrule"])
    labels = (("recall", "recall_0.0", r"Budget 0\%"), ("recall", "recall_0.4", r"Budget 40\%"),
              ("recall", "recall_0.6", r"Budget 60\%"), ("recall", "recall_0.8", r"Budget 80\%"),
              ("recall", "recall_1.0", r"Budget 100\%"), ("proxy", "directory_proxy", "Directory"),
              ("proxy", "directory_no_flakes", "Directory, no flakes"), ("proxy", "directory_no_persistence", "Directory, transient"),
              ("proxy", "uniform_pairs", "Uniform pairs"))
    for suite, condition, label in labels:
        entry = out[suite][condition]
        stats = entry["by_strategy"]["SSMQ"]
        e = entry["p95_difference"]["SSMQ-NoCone"]
        lo, hi = e["ci95"]
        tex.append(f"{label} & {stats['realized_graph_recall']:.2f} & {stats['realized_graph_precision']:.2f} & {e['difference']:+.1f} [{lo:+.1f}, {hi:+.1f}]" + r" \\")
    tex.append(r"\bottomrule\end{tabular}\end{table}")
    (ROOT / "paper/generated/sim_v6.tex").write_text("\n".join(tex) + "\n")


if __name__ == "__main__":
    main()
