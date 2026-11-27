#!/usr/bin/env python3
"""Frozen Sealed Experiment Harness v3 for SSMQ (ICSE / FSE / EuroSys Camera-Ready Polish).

Preserves prior v1/v2 sealed runs untouched and writes 9 immutable v3 suites
under `runs/v3_*/`:
  - v3_exp0_blame_microbench : 64 real executed Python unittest batches
                               (24 disjoint regressions + 16 co-file distractors
                               + 24 true pairwise semantic conflicts) evaluating
                               Random, ChurnRisk, FileTracebackOverlap, and ASTStackCausal
  - v3_exp1_aa_noise         : Disjoint 20-seed Block A vs B noise calibration
  - v3_exp2_main_bench       : Main benchmark (6 strategies x 3 repos x 20 seeds, 80% graph recall)
  - v3_exp3_velocity         : Velocity surge sweep (1x, 3x, 5x, 10x)
  - v3_exp4_ablation         : Full component ablation (including false-rejection rate)
  - v3_exp5_graph_recall     : Non-circularity proof (graph_recall 0.40..1.00 + no-conflict control)
  - v3_exp6_flaky_sweep      : Flaky Pareto sweep (0%, 2%, 5%, 10%, 15%) incl. SSMQ-NoFlaky
  - v3_exp7_blame_sweep      : Blame Top-1 accuracy sweep (0.50..measured_top1) vs SSMQ-NoBlame
  - v3_exp8_blame_regime     : Failure-density & large-batch regime sweep characterizing when
                               1-step causal blame yields substantial vs marginal end-to-end gains
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ssmq import SimConfig, load_trace, simulate_queue
from ssmq.blame_bench import run_blame_benchmark

TRACES = {
    "kubernetes": REPO_ROOT / "data" / "traces" / "kubernetes_kubernetes.json",
    "typescript": REPO_ROOT / "data" / "traces" / "microsoft_TypeScript.json",
    "nextjs": REPO_ROOT / "data" / "traces" / "vercel_next_js.json",
}

MAIN_STRATEGIES = [
    "SerialQueue",
    "StaticBatchBisect",
    "SpeculativeLinear",
    "UberSubmitQueue",
    "FlakeAwareBatch",
    "SSMQ",
]

ABLATION_STRATEGIES = [
    "SSMQ",
    "SSMQ-NoGraph",
    "SSMQ-NoFlaky",
    "SSMQ-NoBlame",
    "StaticBatchBisect",
]

SEEDS_MAIN = list(range(1001, 1021))  # 20 deterministic seeds
SEEDS_AA_B = list(range(2001, 2021))  # Disjoint 20-seed block for A/A check


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def _combined_sha(paths: List[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode("utf-8"))
        h.update(sha256_file(p).encode("utf-8"))
    return h.hexdigest()


def seal_run(
    run_id: str,
    experiment: str,
    seeds: List[int],
    payload: Any,
    extra_meta: Dict[str, Any],
) -> Path:
    run_dir = REPO_ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = run_dir / "metrics.json"
    metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    m_sha = sha256_file(metrics_path)

    harness_files = [
        REPO_ROOT / "eval" / "harness.py",
        REPO_ROOT / "src" / "ssmq" / "simulator.py",
        REPO_ROOT / "src" / "ssmq" / "graph.py",
        REPO_ROOT / "src" / "ssmq" / "loader.py",
        REPO_ROOT / "src" / "ssmq" / "models.py",
        REPO_ROOT / "src" / "ssmq" / "blame_bench.py",
    ]
    rec_count = len(payload) if isinstance(payload, list) else len(payload.get("records", []))
    manifest = {
        "schema_version": "3.0",
        "run_id": run_id,
        "experiment": experiment,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "harness_sha256": _combined_sha(harness_files),
        "dataset_sha256": _combined_sha(list(TRACES.values())),
        "command": "python3 eval/harness.py",
        "seeds": seeds,
        "record_count": rec_count,
        "metrics_file": "metrics.json",
        "metrics_sha256": m_sha,
        "artifact_sha256": m_sha,
        "meta": extra_meta,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"SEALED {run_id}: {rec_count} records, commit={manifest['git_commit']}, sha256={m_sha[:12]}")
    return run_dir


def main() -> None:
    # EXP-0: 64-batch real executed AST/stack-trace causal blame micro-benchmark
    blame_summary, blame_records = run_blame_benchmark(seed=2026)
    seal_run(
        "v3_exp0_blame_microbench",
        "v3_exp0_blame_microbench",
        [2026],
        {"summary": blame_summary.to_dict(), "records": blame_records},
        {"type": "executable_python_unittest_microbench", "batches": blame_summary.total_failing_batches},
    )

    loaded = {name: load_trace(path) for name, path in TRACES.items()}
    measured_top1 = blame_summary.ast_stack_top1_acc
    measured_top2 = min(0.99, blame_summary.ast_stack_top2_acc)
    measured_overhead_min = round(blame_summary.ast_stack_mean_ms / 60000.0, 6)

    base_cfg = SimConfig(
        workers=8,
        ci_duration_min=12.0,
        ci_duration_std=2.0,
        max_batch_size=8,
        max_spec_depth=4,
        velocity_multiplier=5.0,
        base_defect_rate=0.08,
        semantic_conflict_prob=0.18,
        flaky_rate=0.05,
        graph_recall=0.80,
        persistent_conflict_share=0.03,
        blame_top1_acc=measured_top1,
        blame_top2_acc=measured_top2,
        blame_overhead_min=measured_overhead_min,
        warmup_fraction=0.25,
    )

    # EXP-1: A/A Noise Floor Check
    aa_rows: List[Dict[str, Any]] = []
    for block_name, seeds in (("A", SEEDS_MAIN), ("B", SEEDS_AA_B)):
        for repo_name, (g, prs) in loaded.items():
            for s in seeds:
                m = simulate_queue(g, prs, "SSMQ", base_cfg, s).to_dict()
                m["repo"] = repo_name
                m["block"] = block_name
                aa_rows.append(m)
    seal_run(
        "v3_exp1_aa_noise",
        "v3_exp1_aa_noise",
        SEEDS_MAIN + SEEDS_AA_B,
        aa_rows,
        {"graph_recall": 0.80, "flaky_rate": 0.05, "persistent_conflict_share": 0.03},
    )

    # EXP-2: Main Benchmark (6 strategies x 3 repos x 20 paired seeds)
    main_rows: List[Dict[str, Any]] = []
    for repo_name, (g, prs) in loaded.items():
        for strat in MAIN_STRATEGIES:
            for s in SEEDS_MAIN:
                m = simulate_queue(g, prs, strat, base_cfg, s).to_dict()
                m["repo"] = repo_name
                main_rows.append(m)
    seal_run(
        "v3_exp2_main_bench",
        "v3_exp2_main_bench",
        SEEDS_MAIN,
        main_rows,
        {
            "strategies": MAIN_STRATEGIES,
            "repos": list(TRACES.keys()),
            "graph_recall": 0.80,
            "persistent_conflict_share": 0.03,
            "blame_top1_acc": measured_top1,
        },
    )

    # EXP-3: Velocity Surge Sweep (1x, 3x, 5x, 10x)
    vel_rows: List[Dict[str, Any]] = []
    velocities = [1.0, 3.0, 5.0, 10.0]
    vel_strats = ["StaticBatchBisect", "SpeculativeLinear", "UberSubmitQueue", "FlakeAwareBatch", "SSMQ"]
    for vel in velocities:
        cfg = SimConfig(**{**base_cfg.__dict__, "velocity_multiplier": vel})
        for repo_name, (g, prs) in loaded.items():
            for strat in vel_strats:
                for s in SEEDS_MAIN:
                    m = simulate_queue(g, prs, strat, cfg, s).to_dict()
                    m["repo"] = repo_name
                    m["velocity_multiplier"] = vel
                    vel_rows.append(m)
    seal_run(
        "v3_exp3_velocity",
        "v3_exp3_velocity",
        SEEDS_MAIN,
        vel_rows,
        {"velocities": velocities, "strategies": vel_strats},
    )

    # EXP-4: Component Ablation
    abl_rows: List[Dict[str, Any]] = []
    for repo_name, (g, prs) in loaded.items():
        for strat in ABLATION_STRATEGIES:
            for s in SEEDS_MAIN:
                m = simulate_queue(g, prs, strat, base_cfg, s).to_dict()
                m["repo"] = repo_name
                abl_rows.append(m)
    seal_run(
        "v3_exp4_ablation",
        "v3_exp4_ablation",
        SEEDS_MAIN,
        abl_rows,
        {"strategies": ABLATION_STRATEGIES},
    )

    # EXP-5: Non-Circularity Proof — Graph Recall Sweep (0.40..1.00) + No-Conflict Control
    rec_rows: List[Dict[str, Any]] = []
    rec_settings = [
        ("no_conflict", 0.0, 1.0),
        ("recall_0.40", 0.18, 0.40),
        ("recall_0.60", 0.18, 0.60),
        ("recall_0.80", 0.18, 0.80),
        ("recall_1.00", 0.18, 1.00),
    ]
    rec_strats = ["StaticBatchBisect", "UberSubmitQueue", "SSMQ"]
    for label, cprob, grecall in rec_settings:
        cfg = SimConfig(**{**base_cfg.__dict__, "semantic_conflict_prob": cprob, "graph_recall": grecall})
        for repo_name, (g, prs) in loaded.items():
            for strat in rec_strats:
                for s in SEEDS_MAIN:
                    m = simulate_queue(g, prs, strat, cfg, s).to_dict()
                    m["repo"] = repo_name
                    m["regime"] = label
                    m["graph_recall"] = grecall
                    m["semantic_conflict_prob"] = cprob
                    rec_rows.append(m)
    seal_run(
        "v3_exp5_graph_recall",
        "v3_exp5_graph_recall",
        SEEDS_MAIN,
        rec_rows,
        {"regimes": [x[0] for x in rec_settings], "strategies": rec_strats},
    )

    # EXP-6: Flaky Pareto Sweep (0%, 2%, 5%, 10%, 15%) incl. SSMQ-NoFlaky
    flaky_rows: List[Dict[str, Any]] = []
    flaky_rates = [0.0, 0.02, 0.05, 0.10, 0.15]
    flaky_strats = ["StaticBatchBisect", "SpeculativeLinear", "UberSubmitQueue", "FlakeAwareBatch", "SSMQ-NoFlaky", "SSMQ"]
    for fr in flaky_rates:
        cfg = SimConfig(**{**base_cfg.__dict__, "flaky_rate": fr})
        for repo_name, (g, prs) in loaded.items():
            for strat in flaky_strats:
                for s in SEEDS_MAIN:
                    m = simulate_queue(g, prs, strat, cfg, s).to_dict()
                    m["repo"] = repo_name
                    m["flaky_rate"] = fr
                    flaky_rows.append(m)
    seal_run(
        "v3_exp6_flaky_sweep",
        "v3_exp6_flaky_sweep",
        SEEDS_MAIN,
        flaky_rows,
        {"flaky_rates": flaky_rates, "strategies": flaky_strats},
    )

    # EXP-7: Blame Accuracy Sweep matched directly against SSMQ-NoBlame
    blame_rows: List[Dict[str, Any]] = []
    blame_accs = [0.50, 0.65, 0.75, 0.85, measured_top1]
    for acc in blame_accs:
        top2 = min(0.99, acc + 0.06)
        cfg = SimConfig(**{**base_cfg.__dict__, "blame_top1_acc": acc, "blame_top2_acc": top2})
        for repo_name, (g, prs) in loaded.items():
            for strat in ("SSMQ", "SSMQ-NoBlame"):
                for s in SEEDS_MAIN:
                    m = simulate_queue(g, prs, strat, cfg, s).to_dict()
                    m["repo"] = repo_name
                    m["blame_top1_acc"] = round(acc, 4)
                    blame_rows.append(m)
    seal_run(
        "v3_exp7_blame_sweep",
        "v3_exp7_blame_sweep",
        SEEDS_MAIN,
        blame_rows,
        {"blame_top1_accs": [round(a, 4) for a in blame_accs]},
    )

    # EXP-8: Failure-Density & Large-Batch Regime Sweep (characterizing where 1-step blame dominates)
    regime_rows: List[Dict[str, Any]] = []
    regimes = [
        ("nominal_8pct_k8", 0.08, 8, 0.80),
        ("elevated_15pct_k8", 0.15, 8, 0.80),
        ("highdef_22pct_k12", 0.22, 12, 0.60),
        ("lowrec_15pct_k12", 0.15, 12, 0.40),
    ]
    regime_strats = ["StaticBatchBisect", "SSMQ-NoBlame", "SSMQ"]
    for label, drate, kmax, grecall in regimes:
        cfg = SimConfig(**{
            **base_cfg.__dict__,
            "base_defect_rate": drate,
            "max_batch_size": kmax,
            "graph_recall": grecall,
        })
        for repo_name, (g, prs) in loaded.items():
            for strat in regime_strats:
                for s in SEEDS_MAIN:
                    m = simulate_queue(g, prs, strat, cfg, s).to_dict()
                    m["repo"] = repo_name
                    m["regime"] = label
                    m["base_defect_rate"] = drate
                    m["max_batch_size"] = kmax
                    m["graph_recall"] = grecall
                    regime_rows.append(m)
    seal_run(
        "v3_exp8_blame_regime",
        "v3_exp8_blame_regime",
        SEEDS_MAIN,
        regime_rows,
        {"regimes": [r[0] for r in regimes], "strategies": regime_strats},
    )


if __name__ == "__main__":
    main()
