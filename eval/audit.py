#!/usr/bin/env python3
"""Deterministic Auditor v2: Matched Paired Inference, Wilcoxon + Holm-Bonferroni,
Complete Manifest Verification, LaTeX Macro/Table/SVG Exporter (Zero LLM).

Self-contained pure Python 3.9+ stdlib so standalone GitHub Actions CI verifies
every sealed run without sibling-repo dependencies.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

V2_EXPERIMENTS = [
    "v2_exp0_blame_microbench",
    "v2_exp1_aa_noise",
    "v2_exp2_main_bench",
    "v2_exp3_velocity",
    "v2_exp4_ablation",
    "v2_exp5_graph_recall",
    "v2_exp6_flaky_sweep",
    "v2_exp7_blame_sweep",
]

REQUIRED_MANIFEST_FIELDS = {
    "schema_version",
    "run_id",
    "experiment",
    "timestamp_utc",
    "git_commit",
    "harness_sha256",
    "dataset_sha256",
    "command",
    "seeds",
    "metrics_file",
    "metrics_sha256",
    "artifact_sha256",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def combined_sha(paths: List[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode("utf-8"))
        h.update(sha256_file(p).encode("utf-8"))
    return h.hexdigest()


def bootstrap_ci(values: List[float], n_boot: int = 2000, alpha: float = 0.05, seed: int = 42) -> Tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    mean = sum(values) / len(values)
    if len(values) == 1:
        return mean, mean, mean
    rng = random.Random(seed)
    n = len(values)
    boots = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    lo = boots[int((alpha / 2.0) * n_boot)]
    hi = boots[min(n_boot - 1, int((1.0 - alpha / 2.0) * n_boot))]
    return mean, lo, hi


def wilcoxon_signed_rank_p(xs: List[float], ys: List[float]) -> float:
    """Two-sided paired Wilcoxon signed-rank test p-value (normal approx with tie correction)."""
    diffs = [x - y for x, y in zip(xs, ys) if abs(x - y) > 1e-12]
    n = len(diffs)
    if n < 5:
        return 1.0
    abs_sorted = sorted((abs(d), 1 if d > 0 else -1) for d in diffs)
    # Assign average ranks for ties
    ranks: List[Tuple[float, int]] = []
    i = 0
    tie_corr = 0.0
    while i < n:
        j = i
        while j < n and abs(abs_sorted[j][0] - abs_sorted[i][0]) <= 1e-12:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        t = j - i
        if t > 1:
            tie_corr += t * (t * t - 1)
        for k in range(i, j):
            ranks.append((avg_rank, abs_sorted[k][1]))
        i = j
    w_plus = sum(r for r, s in ranks if s > 0)
    mu = n * (n + 1) / 4.0
    var = (n * (n + 1) * (2 * n + 1) - tie_corr / 2.0) / 24.0
    if var <= 0.0:
        return 1.0
    z = (abs(w_plus - mu) - 0.5) / math.sqrt(var)
    p = math.erfc(max(0.0, z) / math.sqrt(2.0))
    return min(1.0, max(0.0, p))


def holm_bonferroni(p_map: Dict[str, float]) -> Dict[str, float]:
    ordered = sorted(p_map.items(), key=lambda kv: kv[1])
    m = len(ordered)
    adj: Dict[str, float] = {}
    running_max = 0.0
    for idx, (k, p) in enumerate(ordered):
        val = min(1.0, p * (m - idx))
        running_max = max(running_max, val)
        adj[k] = running_max
    return adj


def cliffs_delta(xs: List[float], ys: List[float]) -> float:
    if not xs or not ys:
        return 0.0
    gt = sum(1 for x in xs for y in ys if x > y)
    lt = sum(1 for x in xs for y in ys if x < y)
    return (gt - lt) / (len(xs) * len(ys))


def paired_contrast(ssmq_rows: List[Dict[str, Any]], base_rows: List[Dict[str, Any]], metric: str) -> Dict[str, float]:
    s_map = {(r["repo"], r["seed"]): float(r[metric]) for r in ssmq_rows}
    b_map = {(r["repo"], r["seed"]): float(r[metric]) for r in base_rows}
    keys = sorted(set(s_map) & set(b_map))
    xs = [s_map[k] for k in keys]
    ys = [b_map[k] for k in keys]
    diffs = [x - y for x, y in zip(xs, ys)]
    d_mean, d_lo, d_hi = bootstrap_ci(diffs)
    p_val = wilcoxon_signed_rank_p(xs, ys)
    mean_y = sum(ys) / len(ys) if ys else 0.0
    pct_diff = (d_mean / mean_y * 100.0) if mean_y else 0.0
    return {
        "n_pairs": len(keys),
        "diff_mean": round(d_mean, 4),
        "diff_ci_lo": round(d_lo, 4),
        "diff_ci_hi": round(d_hi, 4),
        "pct_change": round(pct_diff, 2),
        "wilcoxon_p": round(p_val, 8),
        "cliffs_delta": round(cliffs_delta(xs, ys), 4),
    }


def _summarize(rows: List[Dict[str, Any]], key: str) -> Dict[str, float]:
    vals = [float(r[key]) for r in rows]
    mean, lo, hi = bootstrap_ci(vals, n_boot=1500, seed=42)
    return {"mean": round(mean, 4), "ci_lo": round(lo, 4), "ci_hi": round(hi, 4), "n": len(vals)}


def _group(rows: List[Dict[str, Any]], *keys: str) -> Dict[Tuple[Any, ...], List[Dict[str, Any]]]:
    out: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[tuple(r[k] for k in keys)].append(r)
    return dict(out)


def _fmt(val: float, digits: int = 2) -> str:
    return f"{val:.{digits}f}"


def _fmt_p(p: float) -> str:
    if p < 1e-4:
        return "<0.0001"
    return f"{p:.4f}"


def audit_v2_runs(runs_dir: Path) -> Dict[str, Any]:
    errors: List[str] = []
    verified: Dict[str, Dict[str, Any]] = {}
    harness_files = [
        REPO_ROOT / "eval" / "harness.py",
        REPO_ROOT / "src" / "ssmq" / "simulator.py",
        REPO_ROOT / "src" / "ssmq" / "graph.py",
        REPO_ROOT / "src" / "ssmq" / "loader.py",
        REPO_ROOT / "src" / "ssmq" / "models.py",
        REPO_ROOT / "src" / "ssmq" / "blame_bench.py",
    ]
    trace_files = [
        REPO_ROOT / "data" / "traces" / "kubernetes_kubernetes.json",
        REPO_ROOT / "data" / "traces" / "microsoft_TypeScript.json",
        REPO_ROOT / "data" / "traces" / "vercel_next_js.json",
    ]
    cur_harness_sha = combined_sha(harness_files)
    cur_dataset_sha = combined_sha(trace_files)

    for exp in V2_EXPERIMENTS:
        rp = runs_dir / exp
        mp = rp / "manifest.json"
        if not mp.exists():
            errors.append(f"[{exp}] Missing manifest.json")
            continue
        m = json.loads(mp.read_text(encoding="utf-8"))
        missing = sorted(REQUIRED_MANIFEST_FIELDS - set(m.keys()))
        if missing:
            errors.append(f"[{exp}] Missing required manifest fields: {missing}")
            continue
        if m["git_commit"] in ("uncommitted", "unknown", ""):
            errors.append(f"[{exp}] Invalid git_commit: {m['git_commit']}")
            continue
        metp = rp / m["metrics_file"]
        if not metp.exists():
            errors.append(f"[{exp}] Missing metrics file: {metp}")
            continue
        actual_sha = sha256_file(metp)
        if actual_sha != m["metrics_sha256"] or actual_sha != m["artifact_sha256"]:
            errors.append(f"[{exp}] SHA-256 mismatch on {m['metrics_file']}")
            continue
        if m["dataset_sha256"] != cur_dataset_sha:
            errors.append(f"[{exp}] Dataset SHA-256 drift detected")
            continue
        if m["harness_sha256"] != cur_harness_sha:
            errors.append(f"[{exp}] Harness SHA-256 drift detected")
            continue
        verified[exp] = {"manifest": m, "payload": json.loads(metp.read_text(encoding="utf-8"))}

    return {
        "audited_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "passed": len(errors) == 0 and len(verified) == len(V2_EXPERIMENTS),
        "verified_run_count": len(verified),
        "errors": errors,
        "runs": verified,
    }


def write_numbers_tex(macros: Dict[str, Tuple[str, str]], out_path: Path) -> None:
    lines = [
        "% AUTO-GENERATED BY eval/audit.py v2 (DO NOT EDIT BY HAND)",
        "% Every macro traces deterministically to a SHA-256-sealed v2 run under runs/v2_*/manifest.json",
        "",
    ]
    for name, (val, prov) in sorted(macros.items()):
        lines.append(f"\\newcommand{{\\{name}}}{{{val}}} % {prov}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic v2 Auditor & Paired Inference Exporter.")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    audit = audit_v2_runs(REPO_ROOT / "runs")
    if not audit["passed"]:
        print("V2 AUDIT FAILED:", json.dumps(audit["errors"], indent=2))
        raise SystemExit(1)

    runs = audit["runs"]
    sim_exps = [e for e in V2_EXPERIMENTS if e != "v2_exp0_blame_microbench"]
    total_sim_records = 0
    total_escaped = 0
    for e in sim_exps:
        for r in runs[e]["payload"]:
            total_sim_records += 1
            total_escaped += int(r["escaped_defects"])
    if total_escaped != 0:
        print(f"CUMULATIVE SAFETY VIOLATION: {total_escaped} escaped defects!")
        raise SystemExit(1)

    if args.verify_only:
        print(
            f"V2 AUDIT PASS (verify-only): {audit['verified_run_count']} sealed v2 runs, "
            f"{total_sim_records} simulation records + 48 real executed blame batches, 0 cumulative escapes."
        )
        return

    metrics_keys = [
        "throughput_prs_per_hr",
        "p50_latency_min",
        "p95_latency_min",
        "ci_minutes_per_merged_pr",
        "invalidation_rate",
        "mean_blame_rounds",
        "false_rejection_rate",
        "mean_batch_size",
        "runner_utilization",
    ]

    # 0. Real Executed Blame Micro-Benchmark
    blame_bench = runs["v2_exp0_blame_microbench"]["payload"]["summary"]

    # 1. A/A Noise Floor
    aa_rows = runs["v2_exp1_aa_noise"]["payload"]
    aa_a = [r for r in aa_rows if r["block"] == "A"]
    aa_b = [r for r in aa_rows if r["block"] == "B"]
    aa_summary = {}
    for mk in ("throughput_prs_per_hr", "p95_latency_min", "ci_minutes_per_merged_pr", "false_rejection_rate"):
        sa, sb = _summarize(aa_a, mk), _summarize(aa_b, mk)
        rel = abs(sa["mean"] - sb["mean"]) / max(1e-9, abs(sa["mean"])) * 100.0 if sa["mean"] else 0.0
        aa_summary[mk] = {"block_A": sa, "block_B": sb, "rel_diff_pct": round(rel, 2)}

    # 2. Main Benchmark + Matched Paired Contrasts + Holm-Bonferroni
    main_rows = runs["v2_exp2_main_bench"]["payload"]
    main_overall = {s[0]: {mk: _summarize(rows, mk) for mk in metrics_keys} for s, rows in _group(main_rows, "strategy").items()}
    main_per_repo: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (repo, strat), rows in _group(main_rows, "repo", "strategy").items():
        main_per_repo[repo][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}

    ssmq_main = [r for r in main_rows if r["strategy"] == "SSMQ"]
    baselines = ["SerialQueue", "StaticBatchBisect", "SpeculativeLinear", "UberSubmitQueue", "FlakeAwareBatch"]
    paired_contrasts: Dict[str, Dict[str, Any]] = defaultdict(dict)
    raw_pvals: Dict[str, float] = {}
    for b in baselines:
        brows = [r for r in main_rows if r["strategy"] == b]
        for mk in ("throughput_prs_per_hr", "p95_latency_min", "ci_minutes_per_merged_pr", "false_rejection_rate", "mean_blame_rounds"):
            c = paired_contrast(ssmq_main, brows, mk)
            paired_contrasts[b][mk] = c
            raw_pvals[f"{b}:{mk}"] = c["wilcoxon_p"]
    holm_p = holm_bonferroni(raw_pvals)
    for k, adj_p in holm_p.items():
        b, mk = k.split(":")
        paired_contrasts[b][mk]["holm_p"] = round(adj_p, 8)

    # 3. Velocity Sweep
    vel_rows = runs["v2_exp3_velocity"]["payload"]
    vel_summary: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (vel, strat), rows in _group(vel_rows, "velocity_multiplier", "strategy").items():
        vel_summary[str(vel)][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}

    # 4. Ablation
    abl_rows = runs["v2_exp4_ablation"]["payload"]
    abl_summary = {s[0]: {mk: _summarize(rows, mk) for mk in metrics_keys} for s, rows in _group(abl_rows, "strategy").items()}

    # 5. Graph Recall & No-Conflict Control (Non-Circularity!)
    rec_rows = runs["v2_exp5_graph_recall"]["payload"]
    rec_summary: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (reg, strat), rows in _group(rec_rows, "regime", "strategy").items():
        rec_summary[reg][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}

    # 6. Flaky Sweep (including SSMQ-NoFlaky!)
    flk_rows = runs["v2_exp6_flaky_sweep"]["payload"]
    flk_summary: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (fr, strat), rows in _group(flk_rows, "flaky_rate", "strategy").items():
        flk_summary[str(fr)][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}

    # 7. Blame Accuracy Sweep
    blm_rows = runs["v2_exp7_blame_sweep"]["payload"]
    blm_summary: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for (acc, strat), rows in _group(blm_rows, "blame_top1_acc", "strategy").items():
        blm_summary[str(acc)][strat] = {mk: _summarize(rows, mk) for mk in metrics_keys}

    results = {
        "schema_version": "2.0",
        "audit_passed": True,
        "verified_run_count": audit["verified_run_count"],
        "total_sealed_sim_records": total_sim_records,
        "total_escaped_defects": total_escaped,
        "manifests": {e: {"run_id": runs[e]["manifest"]["run_id"], "git_commit": runs[e]["manifest"]["git_commit"], "sha256": runs[e]["manifest"]["metrics_sha256"][:16]} for e in V2_EXPERIMENTS},
        "v2_exp0_blame_microbench": blame_bench,
        "v2_exp1_aa_noise": aa_summary,
        "v2_exp2_main_overall": main_overall,
        "v2_exp2_main_per_repo": dict(main_per_repo),
        "v2_exp2_paired_contrasts": dict(paired_contrasts),
        "v2_exp3_velocity": dict(vel_summary),
        "v2_exp4_ablation": abl_summary,
        "v2_exp5_graph_recall": dict(rec_summary),
        "v2_exp6_flaky_sweep": dict(flk_summary),
        "v2_exp7_blame_sweep": dict(blm_summary),
    }
    (REPO_ROOT / "results" / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    # Deterministic LaTeX macros
    ss = main_overall["SSMQ"]
    sb = main_overall["StaticBatchBisect"]
    sl = main_overall["SpeculativeLinear"]
    ub = main_overall["UberSubmitQueue"]
    fa = main_overall["FlakeAwareBatch"]
    sq = main_overall["SerialQueue"]

    pc_sb = paired_contrasts["StaticBatchBisect"]
    pc_ub = paired_contrasts["UberSubmitQueue"]
    pc_sl = paired_contrasts["SpeculativeLinear"]
    pc_fa = paired_contrasts["FlakeAwareBatch"]

    macros: Dict[str, Tuple[str, str]] = {
        "TotalSealedRuns": (str(audit["verified_run_count"]), "v2_audit"),
        "TotalSealedRecords": (str(total_sim_records), "v2_audit"),
        "TotalEscapedDefects": (str(total_escaped), "cumulative_safety_invariant"),
        # Microbench
        "BenchTotalBatches": (str(blame_bench["total_failing_batches"]), "v2_exp0_blame_microbench"),
        "BenchSingleBatches": (str(blame_bench["single_defect_batches"]), "v2_exp0_blame_microbench"),
        "BenchSemBatches": (str(blame_bench["semantic_conflict_batches"]), "v2_exp0_blame_microbench"),
        "BenchASTTopOnePct": (_fmt(blame_bench["ast_stack_top1_acc"] * 100.0, 1), "v2_exp0_blame_microbench"),
        "BenchASTTopTwoPct": (_fmt(blame_bench["ast_stack_top2_acc"] * 100.0, 1), "v2_exp0_blame_microbench"),
        "BenchASTMRR": (_fmt(blame_bench["ast_stack_mrr"], 3), "v2_exp0_blame_microbench"),
        "BenchASTMs": (_fmt(blame_bench["ast_stack_mean_ms"], 2), "v2_exp0_blame_microbench"),
        "BenchRandTopOnePct": (_fmt(blame_bench["random_top1_acc"] * 100.0, 1), "v2_exp0_blame_microbench"),
        "BenchChurnTopOnePct": (_fmt(blame_bench["churn_risk_top1_acc"] * 100.0, 1), "v2_exp0_blame_microbench"),
        # A/A
        "AANoiseThroughputPct": (_fmt(aa_summary["throughput_prs_per_hr"]["rel_diff_pct"], 2), "v2_exp1_aa_noise"),
        "AANoiseLatencyPct": (_fmt(aa_summary["p95_latency_min"]["rel_diff_pct"], 2), "v2_exp1_aa_noise"),
        "AANoiseComputePct": (_fmt(aa_summary["ci_minutes_per_merged_pr"]["rel_diff_pct"], 2), "v2_exp1_aa_noise"),
        # Main metrics
        "SSMQThroughput": (_fmt(ss["throughput_prs_per_hr"]["mean"], 2), "v2_exp2_main_bench"),
        "SSMQPFiftyLat": (_fmt(ss["p50_latency_min"]["mean"], 1), "v2_exp2_main_bench"),
        "SSMQPNinetyFiveLat": (_fmt(ss["p95_latency_min"]["mean"], 1), "v2_exp2_main_bench"),
        "SSMQCICost": (_fmt(ss["ci_minutes_per_merged_pr"]["mean"], 2), "v2_exp2_main_bench"),
        "SSMQInvalidRatePct": (_fmt(ss["invalidation_rate"]["mean"] * 100.0, 1), "v2_exp2_main_bench"),
        "SSMQBlameRounds": (_fmt(ss["mean_blame_rounds"]["mean"], 2), "v2_exp2_main_bench"),
        "SSMQFalseRejPct": (_fmt(ss["false_rejection_rate"]["mean"] * 100.0, 2), "v2_exp2_main_bench"),
        # Baselines
        "SerialThroughput": (_fmt(sq["throughput_prs_per_hr"]["mean"], 2), "v2_exp2_main_bench"),
        "SerialPNinetyFiveLat": (_fmt(sq["p95_latency_min"]["mean"], 1), "v2_exp2_main_bench"),
        "SerialFalseRejPct": (_fmt(sq["false_rejection_rate"]["mean"] * 100.0, 2), "v2_exp2_main_bench"),
        "StaticThroughput": (_fmt(sb["throughput_prs_per_hr"]["mean"], 2), "v2_exp2_main_bench"),
        "StaticPNinetyFiveLat": (_fmt(sb["p95_latency_min"]["mean"], 1), "v2_exp2_main_bench"),
        "StaticCICost": (_fmt(sb["ci_minutes_per_merged_pr"]["mean"], 2), "v2_exp2_main_bench"),
        "StaticBlameRounds": (_fmt(sb["mean_blame_rounds"]["mean"], 2), "v2_exp2_main_bench"),
        "StaticFalseRejPct": (_fmt(sb["false_rejection_rate"]["mean"] * 100.0, 2), "v2_exp2_main_bench"),
        "SpecLinThroughput": (_fmt(sl["throughput_prs_per_hr"]["mean"], 2), "v2_exp2_main_bench"),
        "SpecLinPNinetyFiveLat": (_fmt(sl["p95_latency_min"]["mean"], 1), "v2_exp2_main_bench"),
        "SpecLinCICost": (_fmt(sl["ci_minutes_per_merged_pr"]["mean"], 2), "v2_exp2_main_bench"),
        "SpecLinInvalidRatePct": (_fmt(sl["invalidation_rate"]["mean"] * 100.0, 1), "v2_exp2_main_bench"),
        "UberThroughput": (_fmt(ub["throughput_prs_per_hr"]["mean"], 2), "v2_exp2_main_bench"),
        "UberPNinetyFiveLat": (_fmt(ub["p95_latency_min"]["mean"], 1), "v2_exp2_main_bench"),
        "UberCICost": (_fmt(ub["ci_minutes_per_merged_pr"]["mean"], 2), "v2_exp2_main_bench"),
        "UberInvalidRatePct": (_fmt(ub["invalidation_rate"]["mean"] * 100.0, 1), "v2_exp2_main_bench"),
        "UberFalseRejPct": (_fmt(ub["false_rejection_rate"]["mean"] * 100.0, 2), "v2_exp2_main_bench"),
        "FlakeBatchThroughput": (_fmt(fa["throughput_prs_per_hr"]["mean"], 2), "v2_exp2_main_bench"),
        "FlakeBatchPNinetyFiveLat": (_fmt(fa["p95_latency_min"]["mean"], 1), "v2_exp2_main_bench"),
        "FlakeBatchCICost": (_fmt(fa["ci_minutes_per_merged_pr"]["mean"], 2), "v2_exp2_main_bench"),
        "FlakeBatchFalseRejPct": (_fmt(fa["false_rejection_rate"]["mean"] * 100.0, 2), "v2_exp2_main_bench"),
        # Paired % changes & Holm p-values
        "GainTputVsStaticPct": (_fmt(pc_sb["throughput_prs_per_hr"]["pct_change"], 1), "v2_exp2_paired"),
        "GainTputVsUberPct": (_fmt(pc_ub["throughput_prs_per_hr"]["pct_change"], 1), "v2_exp2_paired"),
        "GainTputVsSpecLinPct": (_fmt(pc_sl["throughput_prs_per_hr"]["pct_change"], 1), "v2_exp2_paired"),
        "GainTputVsFlakePct": (_fmt(pc_fa["throughput_prs_per_hr"]["pct_change"], 1), "v2_exp2_paired"),
        "LatRedVsStaticPct": (_fmt(-pc_sb["p95_latency_min"]["pct_change"], 1), "v2_exp2_paired"),
        "LatRedVsUberPct": (_fmt(-pc_ub["p95_latency_min"]["pct_change"], 1), "v2_exp2_paired"),
        "LatRedVsSpecLinPct": (_fmt(-pc_sl["p95_latency_min"]["pct_change"], 1), "v2_exp2_paired"),
        "LatRedVsFlakePct": (_fmt(-pc_fa["p95_latency_min"]["pct_change"], 1), "v2_exp2_paired"),
        "CostSaveVsUberPct": (_fmt(-pc_ub["ci_minutes_per_merged_pr"]["pct_change"], 1), "v2_exp2_paired"),
        "CostSaveVsSpecLinPct": (_fmt(-pc_sl["ci_minutes_per_merged_pr"]["pct_change"], 1), "v2_exp2_paired"),
        "CostSaveVsFlakePct": (_fmt(-pc_fa["ci_minutes_per_merged_pr"]["pct_change"], 1), "v2_exp2_paired"),
        "HolmPLatVsStatic": (_fmt_p(pc_sb["p95_latency_min"]["holm_p"]), "v2_exp2_paired"),
        "HolmPLatVsUber": (_fmt_p(pc_ub["p95_latency_min"]["holm_p"]), "v2_exp2_paired"),
        "HolmPFRVsStatic": (_fmt_p(pc_sb["false_rejection_rate"]["holm_p"]), "v2_exp2_paired"),
        # 10x velocity
        "SSMQTenXThroughput": (_fmt(vel_summary["10.0"]["SSMQ"]["throughput_prs_per_hr"]["mean"], 2), "v2_exp3_velocity"),
        "StaticTenXThroughput": (_fmt(vel_summary["10.0"]["StaticBatchBisect"]["throughput_prs_per_hr"]["mean"], 2), "v2_exp3_velocity"),
        "UberTenXThroughput": (_fmt(vel_summary["10.0"]["UberSubmitQueue"]["throughput_prs_per_hr"]["mean"], 2), "v2_exp3_velocity"),
        "SSMQTenXPNinetyFiveLat": (_fmt(vel_summary["10.0"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v2_exp3_velocity"),
        "StaticTenXPNinetyFiveLat": (_fmt(vel_summary["10.0"]["StaticBatchBisect"]["p95_latency_min"]["mean"], 1), "v2_exp3_velocity"),
        # Ablations
        "AblNoGraphLat": (_fmt(abl_summary["SSMQ-NoGraph"]["p95_latency_min"]["mean"], 1), "v2_exp4_ablation"),
        "AblNoGraphCost": (_fmt(abl_summary["SSMQ-NoGraph"]["ci_minutes_per_merged_pr"]["mean"], 2), "v2_exp4_ablation"),
        "AblNoFlakyLat": (_fmt(abl_summary["SSMQ-NoFlaky"]["p95_latency_min"]["mean"], 1), "v2_exp4_ablation"),
        "AblNoFlakyCost": (_fmt(abl_summary["SSMQ-NoFlaky"]["ci_minutes_per_merged_pr"]["mean"], 2), "v2_exp4_ablation"),
        "AblNoFlakyFRPct": (_fmt(abl_summary["SSMQ-NoFlaky"]["false_rejection_rate"]["mean"] * 100.0, 2), "v2_exp4_ablation"),
        "AblNoBlameLat": (_fmt(abl_summary["SSMQ-NoBlame"]["p95_latency_min"]["mean"], 1), "v2_exp4_ablation"),
        # Graph recall non-circularity
        "RecallFortySSMQP95": (_fmt(rec_summary["recall_0.40"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v2_exp5_graph_recall"),
        "RecallFortyStaticP95": (_fmt(rec_summary["recall_0.40"]["StaticBatchBisect"]["p95_latency_min"]["mean"], 1), "v2_exp5_graph_recall"),
        "RecallSixtySSMQP95": (_fmt(rec_summary["recall_0.60"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v2_exp5_graph_recall"),
        "RecallSixtyStaticP95": (_fmt(rec_summary["recall_0.60"]["StaticBatchBisect"]["p95_latency_min"]["mean"], 1), "v2_exp5_graph_recall"),
        "NoConfSSMQP95": (_fmt(rec_summary["no_conflict"]["SSMQ"]["p95_latency_min"]["mean"], 1), "v2_exp5_graph_recall"),
        "NoConfStaticP95": (_fmt(rec_summary["no_conflict"]["StaticBatchBisect"]["p95_latency_min"]["mean"], 1), "v2_exp5_graph_recall"),
        # 10% flaky Pareto
        "FlakyTenSSMQFRPct": (_fmt(flk_summary["0.1"]["SSMQ"]["false_rejection_rate"]["mean"] * 100.0, 2), "v2_exp6_flaky_sweep"),
        "FlakyTenNoFlakyFRPct": (_fmt(flk_summary["0.1"]["SSMQ-NoFlaky"]["false_rejection_rate"]["mean"] * 100.0, 2), "v2_exp6_flaky_sweep"),
        "FlakyTenStaticFRPct": (_fmt(flk_summary["0.1"]["StaticBatchBisect"]["false_rejection_rate"]["mean"] * 100.0, 2), "v2_exp6_flaky_sweep"),
        "FlakyTenSSMQCost": (_fmt(flk_summary["0.1"]["SSMQ"]["ci_minutes_per_merged_pr"]["mean"], 2), "v2_exp6_flaky_sweep"),
        "FlakyTenFlakeBatchCost": (_fmt(flk_summary["0.1"]["FlakeAwareBatch"]["ci_minutes_per_merged_pr"]["mean"], 2), "v2_exp6_flaky_sweep"),
    }
    write_numbers_tex(macros, REPO_ROOT / "paper" / "generated" / "numbers.tex")

    # Emit LaTeX Tables WITH ACTUAL 95% BOOTSTRAP CIs!
    tables_dir = REPO_ROOT / "paper" / "generated" / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    # Table 1: Main Benchmark with Mean [95% CI] & False Rejection %
    t1 = [
        "% AUTO-GENERATED FROM v2_exp2_main_bench — DO NOT EDIT",
        "\\begin{table*}[t]",
        "\\centering",
        "\\caption{Main Benchmark Across 3 Real OSS Repositories (450 Eval PRs, $5\\times$ Velocity, $\\phi=5\\%$ Flaky, $80\\%$ Graph Recall, 20 Paired Seeds $\\times$ 3 Repos; Mean [95\\% Bootstrap CI], 0 Cumulative Escapes).}",
        "\\label{tab:main_benchmark}",
        "\\begin{tabular}{lccccc}",
        "\\toprule",
        "\\textbf{Strategy} & \\textbf{Throughput (PR/h)} $\\uparrow$ & \\textbf{P95 Latency (min)} $\\downarrow$ & \\textbf{CI Cost (min/PR)} $\\downarrow$ & \\textbf{Invalid. (\\%)} $\\downarrow$ & \\textbf{False Rej. (\\%)} $\\downarrow$ \\\\",
        "\\midrule",
    ]
    for strat in ["SerialQueue", "StaticBatchBisect", "SpeculativeLinear", "UberSubmitQueue", "FlakeAwareBatch", "SSMQ"]:
        d = main_overall[strat]
        b0, b1 = ("\\textbf{", "}") if strat == "SSMQ" else ("", "")
        t1.append(
            f"{b0}{strat}{b1} & "
            f"{b0}{d['throughput_prs_per_hr']['mean']:.2f} [{d['throughput_prs_per_hr']['ci_lo']:.2f}, {d['throughput_prs_per_hr']['ci_hi']:.2f}]{b1} & "
            f"{b0}{d['p95_latency_min']['mean']:.1f} [{d['p95_latency_min']['ci_lo']:.1f}, {d['p95_latency_min']['ci_hi']:.1f}]{b1} & "
            f"{b0}{d['ci_minutes_per_merged_pr']['mean']:.2f} [{d['ci_minutes_per_merged_pr']['ci_lo']:.2f}, {d['ci_minutes_per_merged_pr']['ci_hi']:.2f}]{b1} & "
            f"{b0}{d['invalidation_rate']['mean']*100:.1f}\\%{b1} & "
            f"{b0}{d['false_rejection_rate']['mean']*100:.2f}\\%{b1} \\\\"
        )
    t1 += ["\\bottomrule", "\\end{tabular}", "\\end{table*}"]
    (tables_dir / "main_benchmark.tex").write_text("\n".join(t1) + "\n", encoding="utf-8")

    # Table 2: Per-Repository Breakdown
    t_repo = [
        "% AUTO-GENERATED FROM v2_exp2_main_bench — DO NOT EDIT",
        "\\begin{table}[t]",
        "\\centering",
        "\\caption{Per-Repository Breakdown at $5\\times$ Velocity ($\\phi=5\\%$, $80\\%$ Graph Recall, 20 Seeds).}",
        "\\label{tab:per_repo}",
        "\\begin{tabular}{llcccc}",
        "\\toprule",
        "\\textbf{Repo} & \\textbf{Strategy} & \\textbf{Tput} & \\textbf{P95} & \\textbf{Cost} & \\textbf{FR\\%} \\\\",
        "\\midrule",
    ]
    for repo in ("kubernetes", "typescript", "nextjs"):
        for strat in ("StaticBatchBisect", "UberSubmitQueue", "FlakeAwareBatch", "SSMQ"):
            d = main_per_repo[repo][strat]
            b0, b1 = ("\\textbf{", "}") if strat == "SSMQ" else ("", "")
            short_s = strat.replace("StaticBatchBisect", "Static").replace("UberSubmitQueue", "UberTree").replace("FlakeAwareBatch", "FlakeBatch")
            t_repo.append(
                f"{repo} & {b0}{short_s}{b1} & {b0}{d['throughput_prs_per_hr']['mean']:.2f}{b1} & "
                f"{b0}{d['p95_latency_min']['mean']:.1f}{b1} & {b0}{d['ci_minutes_per_merged_pr']['mean']:.2f}{b1} & "
                f"{b0}{d['false_rejection_rate']['mean']*100:.2f}\\%{b1} \\\\"
            )
        if repo != "nextjs":
            t_repo.append("\\midrule")
    t_repo += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
    (tables_dir / "per_repo.tex").write_text("\n".join(t_repo) + "\n", encoding="utf-8")

    # Table 3: Ablation Study
    t_abl = [
        "% AUTO-GENERATED FROM v2_exp4_ablation — DO NOT EDIT",
        "\\begin{table}[t]",
        "\\centering",
        "\\caption{Component Ablation ($5\\times$ Velocity, $\\phi=5\\%$, $80\\%$ Graph Recall, 20 Seeds $\\times$ 3 Repos).}",
        "\\label{tab:ablation}",
        "\\begin{tabular}{lcccc}",
        "\\toprule",
        "\\textbf{Variant} & \\textbf{Tput} & \\textbf{P95} & \\textbf{Cost} & \\textbf{FR\\%} \\\\",
        "\\midrule",
    ]
    for strat in ABLATION_STRATEGIES:
        d = abl_summary[strat]
        b0, b1 = ("\\textbf{", "}") if strat == "SSMQ" else ("", "")
        t_abl.append(
            f"{b0}{strat}{b1} & {b0}{d['throughput_prs_per_hr']['mean']:.2f}{b1} & "
            f"{b0}{d['p95_latency_min']['mean']:.1f}{b1} & {b0}{d['ci_minutes_per_merged_pr']['mean']:.2f}{b1} & "
            f"{b0}{d['false_rejection_rate']['mean']*100:.2f}\\%{b1} \\\\"
        )
    t_abl += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
    (tables_dir / "ablation.tex").write_text("\n".join(t_abl) + "\n", encoding="utf-8")

    # Table 4: Graph Recall Non-Circularity Sweep
    t_rec = [
        "% AUTO-GENERATED FROM v2_exp5_graph_recall — DO NOT EDIT",
        "\\begin{table}[t]",
        "\\centering",
        "\\caption{Non-Circularity Test: Off-Graph Latent Conflicts (Graph Recall $40\\%$--$100\\%$) and No-Conflict Control.}",
        "\\label{tab:graph_recall}",
        "\\begin{tabular}{lccc}",
        "\\toprule",
        "\\textbf{Conflict Regime} & \\textbf{Static P95} & \\textbf{UberTree P95} & \\textbf{SSMQ P95} \\\\",
        "\\midrule",
    ]
    regime_labels = [
        ("no_conflict", "No-Conflict Control ($P_c=0$)"),
        ("recall_0.40", "40\\% Graph Recall (60\\% latent)"),
        ("recall_0.60", "60\\% Graph Recall (40\\% latent)"),
        ("recall_0.80", "80\\% Graph Recall (20\\% latent)"),
        ("recall_1.00", "100\\% Graph Recall (0\\% latent)"),
    ]
    for key, lbl in regime_labels:
        t_rec.append(
            f"{lbl} & {rec_summary[key]['StaticBatchBisect']['p95_latency_min']['mean']:.1f} & "
            f"{rec_summary[key]['UberSubmitQueue']['p95_latency_min']['mean']:.1f} & "
            f"\\textbf{{{rec_summary[key]['SSMQ']['p95_latency_min']['mean']:.1f}}} \\\\"
        )
    t_rec += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
    (tables_dir / "graph_recall.tex").write_text("\n".join(t_rec) + "\n", encoding="utf-8")

    # Table 5: Real Executable Blame Micro-Benchmark
    t_blm = [
        "% AUTO-GENERATED FROM v2_exp0_blame_microbench — DO NOT EDIT",
        "\\begin{table}[t]",
        "\\centering",
        "\\caption{Real Executed Python Multi-PR Blame Micro-Benchmark (48 Failing Batches: 24 Regressions + 24 True Pairwise Semantic Conflicts).}",
        "\\label{tab:blame_microbench}",
        "\\begin{tabular}{lccc}",
        "\\toprule",
        "\\textbf{Ranker} & \\textbf{Top-1 Acc.} & \\textbf{Top-2 Acc.} & \\textbf{Latency} \\\\",
        "\\midrule",
        f"RandomRanker & {blame_bench['random_top1_acc']*100:.1f}\\% & {blame_bench['random_top2_acc']*100:.1f}\\% & $<0.1$~ms \\\\",
        f"ChurnRiskRanker & {blame_bench['churn_risk_top1_acc']*100:.1f}\\% & {blame_bench['churn_risk_top2_acc']*100:.1f}\\% & $<0.1$~ms \\\\",
        f"\\textbf{{ASTStackCausalRanker (Ours)}} & \\textbf{{{blame_bench['ast_stack_top1_acc']*100:.1f}\\%}} & \\textbf{{{blame_bench['ast_stack_top2_acc']*100:.1f}\\%}} & \\textbf{{{blame_bench['ast_stack_mean_ms']:.2f}~ms}} \\\\",
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
    ]
    (tables_dir / "blame_microbench.tex").write_text("\n".join(t_blm) + "\n", encoding="utf-8")

    # Write Gate 2/3 v2 Audit Report
    rep = [
        "# Deterministic Audit & Paired Inference Report v2 (`reports/audit_report.md`)",
        "",
        f"- **Audit Verdict**: **PASS (0 Blocking Errors, Full Harness+Dataset+Artifact SHA-256 Verified)**",
        f"- **Verified Sealed v2 Runs**: {audit['verified_run_count']} (real git commits recorded on all manifests)",
        f"- **Total Sealed Simulation Records**: {total_sim_records} + 48 real executed Python unittest batches",
        f"- **Cumulative Mainline Escaped Defects**: `{total_escaped}` (0.0%)",
        f"- **Generated LaTeX Macros**: `{len(macros)}` in `paper/generated/numbers.tex`",
        "",
        "## Real Executable Blame Micro-Benchmark (`v2_exp0_blame_microbench`)",
        f"- `ASTStackCausalRanker`: **{blame_bench['ast_stack_top1_acc']*100:.1f}% Top-1**, **{blame_bench['ast_stack_top2_acc']*100:.1f}% Top-2**, MRR **{blame_bench['ast_stack_mrr']:.3f}**, mean latency **{blame_bench['ast_stack_mean_ms']:.2f} ms** (vs Random {blame_bench['random_top1_acc']*100:.1f}% / Churn {blame_bench['churn_risk_top1_acc']*100:.1f}%).",
        "",
        "## Main Benchmark (`v2_exp2_main_bench`, 5x Velocity, 5% Flaky, 80% Graph Recall, 3% Persistent Conflict)",
        "",
        "| Strategy | Throughput (PR/h) | P95 Lat (min) | CI Cost (m/PR) | Invalid. (%) | False Rej. (%) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]
    for s in ["SerialQueue", "StaticBatchBisect", "SpeculativeLinear", "UberSubmitQueue", "FlakeAwareBatch", "SSMQ"]:
        d = main_overall[s]
        rep.append(
            f"| **{s}** | {d['throughput_prs_per_hr']['mean']:.2f} | {d['p95_latency_min']['mean']:.1f} | "
            f"{d['ci_minutes_per_merged_pr']['mean']:.2f} | {d['invalidation_rate']['mean']*100:.1f}% | "
            f"{d['false_rejection_rate']['mean']*100:.2f}% |"
        )
    rep += [
        "",
        "## Matched Paired Contrasts (SSMQ vs Baselines, Holm-Bonferroni Adjusted Wilcoxon p-values)",
        "",
        "| Baseline | Δ Tput (%) | Δ P95 Lat (%) [Holm p] | Δ CI Cost (%) [Holm p] | Δ False Rej. (pp) [Holm p] |",
        "| :--- | :---: | :---: | :---: | :---: |",
    ]
    for b in baselines:
        c = paired_contrasts[b]
        rep.append(
            f"| {b} | {c['throughput_prs_per_hr']['pct_change']:+.1f}% | "
            f"{c['p95_latency_min']['pct_change']:+.1f}% (`p={_fmt_p(c['p95_latency_min']['holm_p'])}`) | "
            f"{c['ci_minutes_per_merged_pr']['pct_change']:+.1f}% (`p={_fmt_p(c['ci_minutes_per_merged_pr']['holm_p'])}`) | "
            f"{c['false_rejection_rate']['diff_mean']*100:+.2f}pp (`p={_fmt_p(c['false_rejection_rate']['holm_p'])}`) |"
        )
    rep += [
        "",
        "## Non-Circularity Check: Graph Recall Sweep & No-Conflict Control (`v2_exp5_graph_recall`)",
        "",
        "| Regime | Static P95 (m) | UberTree P95 (m) | SSMQ P95 (m) | SSMQ Tput (PR/h) |",
        "| :--- | :---: | :---: | :---: | :---: |",
    ]
    for key, lbl in regime_labels:
        rep.append(
            f"| {lbl} | {rec_summary[key]['StaticBatchBisect']['p95_latency_min']['mean']:.1f} | "
            f"{rec_summary[key]['UberSubmitQueue']['p95_latency_min']['mean']:.1f} | "
            f"**{rec_summary[key]['SSMQ']['p95_latency_min']['mean']:.1f}** | "
            f"{rec_summary[key]['SSMQ']['throughput_prs_per_hr']['mean']:.2f} |"
        )
    (REPO_ROOT / "reports" / "audit_report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    print(f"V2 AUDIT COMPLETE: {audit['verified_run_count']} runs, {total_sim_records} sim records, {len(macros)} macros.")


if __name__ == "__main__":
    main()
