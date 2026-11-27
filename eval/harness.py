#!/usr/bin/env python3
"""Frozen Sealed Experiment Harness for SSMQ (Gate 1 -> Gate 2).

Executes five sealed, reproducible experimental suites over the 600 real merged
GitHub PR traces (kubernetes/kubernetes, microsoft/TypeScript, vercel/next.js):
  1. exp0_aa_noise      : A/A noise floor check (two disjoint 20-seed blocks on identical config)
  2. exp1_main_bench    : Main benchmark (6 strategies x 3 real OSS repos x 20 seeds at 5x velocity)
  3. exp2_velocity      : PR velocity surge sweep (1x, 3x, 5x, 10x AI-agent arrival rates)
  4. exp3_ablation      : Component ablation (SSMQ vs NoGraph, NoFlaky, NoBlame)
  5. exp4_sensitivity   : Flaky rate sweep (0%, 2%, 5%, 10%) & blame Top-1 accuracy sweep (0.50..0.95)

Each experiment writes an immutable directory under `runs/<run_id>/` containing:
  - `metrics.json` (raw per-seed RunMetrics records)
  - `manifest.json` (SHA-256 of metrics, harness, traces, git commit, CLI command, seeds)
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
FACTORY_ROOT = REPO_ROOT.parent / "research-paper-factory"
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(FACTORY_ROOT))

from factory.auditor import sha256_file
from factory.scaffold import append_ai_usage
from ssmq import ALL_STRATEGIES, SimConfig, load_trace, simulate_queue

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
SEEDS_AA_B = list(range(2001, 2021))  # Disjoint 20-seed block for A/A noise floor


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
        return "uncommitted"


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
    records: List[Dict[str, Any]],
    extra_meta: Dict[str, Any],
) -> Path:
    run_dir = REPO_ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = run_dir / "metrics.json"
    metrics_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    harness_files = [
        REPO_ROOT / "eval" / "harness.py",
        REPO_ROOT / "src" / "ssmq" / "simulator.py",
        REPO_ROOT / "src" / "ssmq" / "graph.py",
        REPO_ROOT / "src" / "ssmq" / "loader.py",
        REPO_ROOT / "src" / "ssmq" / "models.py",
    ]
    manifest = {
        "run_id": run_id,
        "experiment": experiment,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "harness_sha256": _combined_sha(harness_files),
        "dataset_sha256": _combined_sha(list(TRACES.values())),
        "command": "python3 eval/harness.py",
        "seeds": seeds,
        "record_count": len(records),
        "metrics_file": "metrics.json",
        "metrics_sha256": sha256_file(metrics_path),
        "meta": extra_meta,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"SEALED {run_id}: {len(records)} records, sha256={manifest['metrics_sha256'][:12]}")
    return run_dir


def main() -> None:
    loaded = {name: load_trace(path) for name, path in TRACES.items()}
    base_cfg = SimConfig(
        workers=8,
        ci_duration_min=12.0,
        ci_duration_std=2.0,
        max_batch_size=8,
        max_spec_depth=4,
        velocity_multiplier=5.0,
        base_defect_rate=0.08,
        semantic_conflict_prob=0.22,
        flaky_rate=0.05,
        blame_top1_acc=0.85,
        blame_top2_acc=0.95,
        blame_overhead_min=0.5,
        warmup_fraction=0.25,
    )

    # EXP-0: A/A Noise Floor Check (Block A vs Block B on identical SSMQ config)
    aa_rows: List[Dict[str, Any]] = []
    for block_name, seeds in (("A", SEEDS_MAIN), ("B", SEEDS_AA_B)):
        for repo_name, (g, prs) in loaded.items():
            for s in seeds:
                m = simulate_queue(g, prs, "SSMQ", base_cfg, s).to_dict()
                m["repo"] = repo_name
                m["block"] = block_name
                aa_rows.append(m)
    seal_run(
        "exp0_aa_noise_v1",
        "exp0_aa_noise",
        SEEDS_MAIN + SEEDS_AA_B,
        aa_rows,
        {"velocity_multiplier": 5.0, "flaky_rate": 0.05},
    )

    # EXP-1: Main Benchmark (6 strategies x 3 repos x 20 seeds at 5x AI-agent velocity)
    main_rows: List[Dict[str, Any]] = []
    for repo_name, (g, prs) in loaded.items():
        for strat in MAIN_STRATEGIES:
            for s in SEEDS_MAIN:
                m = simulate_queue(g, prs, strat, base_cfg, s).to_dict()
                m["repo"] = repo_name
                main_rows.append(m)
    seal_run(
        "exp1_main_bench_v1",
        "exp1_main_bench",
        SEEDS_MAIN,
        main_rows,
        {"strategies": MAIN_STRATEGIES, "repos": list(TRACES.keys()), "velocity_multiplier": 5.0, "flaky_rate": 0.05},
    )

    # EXP-2: Velocity Surge Sweep (1x, 3x, 5x, 10x across 3 repos x 20 seeds)
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
        "exp2_velocity_sweep_v1",
        "exp2_velocity_sweep",
        SEEDS_MAIN,
        vel_rows,
        {"velocities": velocities, "strategies": vel_strats},
    )

    # EXP-3: Ablation Study (SSMQ vs NoGraph, NoFlaky, NoBlame)
    abl_rows: List[Dict[str, Any]] = []
    for repo_name, (g, prs) in loaded.items():
        for strat in ABLATION_STRATEGIES:
            for s in SEEDS_MAIN:
                m = simulate_queue(g, prs, strat, base_cfg, s).to_dict()
                m["repo"] = repo_name
                abl_rows.append(m)
    seal_run(
        "exp3_ablation_v1",
        "exp3_ablation",
        SEEDS_MAIN,
        abl_rows,
        {"strategies": ABLATION_STRATEGIES, "velocity_multiplier": 5.0, "flaky_rate": 0.05},
    )

    # EXP-4A: Flaky Rate Sensitivity Sweep (0%, 2%, 5%, 10%)
    flaky_rows: List[Dict[str, Any]] = []
    flaky_rates = [0.0, 0.02, 0.05, 0.10]
    flaky_strats = ["StaticBatchBisect", "SpeculativeLinear", "UberSubmitQueue", "FlakeAwareBatch", "SSMQ"]
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
        "exp4a_flaky_sweep_v1",
        "exp4a_flaky_sweep",
        SEEDS_MAIN,
        flaky_rows,
        {"flaky_rates": flaky_rates, "strategies": flaky_strats},
    )

    # EXP-4B: Causal Blame Top-1 Accuracy Sensitivity Sweep (0.50 .. 0.95)
    blame_rows: List[Dict[str, Any]] = []
    blame_accs = [0.50, 0.65, 0.75, 0.85, 0.95]
    for acc in blame_accs:
        top2 = min(0.99, acc + 0.10)
        cfg = SimConfig(**{**base_cfg.__dict__, "blame_top1_acc": acc, "blame_top2_acc": top2})
        for repo_name, (g, prs) in loaded.items():
            for s in SEEDS_MAIN:
                m = simulate_queue(g, prs, "SSMQ", cfg, s).to_dict()
                m["repo"] = repo_name
                m["blame_top1_acc"] = acc
                blame_rows.append(m)
    seal_run(
        "exp4b_blame_accuracy_v1",
        "exp4b_blame_accuracy",
        SEEDS_MAIN,
        blame_rows,
        {"blame_top1_accs": blame_accs},
    )

    append_ai_usage(
        REPO_ROOT,
        provider="codex",
        model="gpt-6.2-sol",
        role="Architect",
        purpose="Implemented pure-Python SSMQ discrete-event simulator, 9 strategies, and 12 unit tests",
        files="src/ssmq/*.py, tests/test_ssmq.py",
    )
    append_ai_usage(
        REPO_ROOT,
        provider="antigravity",
        model="gemini-4-neon",
        role="Runner",
        purpose="Executed 6 sealed multi-seed experiment suites (20 seeds x 3 real OSS repos) with SHA-256 manifests",
        files="eval/harness.py, runs/*/manifest.json, runs/*/metrics.json",
    )


if __name__ == "__main__":
    main()
