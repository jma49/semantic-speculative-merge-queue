#!/usr/bin/env python3
"""Frozen Sealed Experiment Harness v4 for SSMQ (Post-Codex-v3 Gate 3 Approval).

Preserves prior v1/v2/v3 runs untouched and writes 9 immutable v4 suites under
`runs/v4_*/` (refusing to overwrite existing sealed runs unless `--force` is set):
  - v4_exp0_blame_microbench : 80 real executed Python unittest batches across
                               4 strata (24 disjoint + 16 comment co-file +
                               16 FUNCTIONAL sibling co-file + 24 semantic
                               conflicts) + executed leave-one-out recovery +
                               all 5 rankers timed live.
  - v4_exp1_aa_noise         : Disjoint 20-seed Block A vs B noise calibration.
  - v4_exp2_main_bench       : 7 primary strategies (incl. FIFOOutcomeTree &
                               DepAwareOutcomeTree) x 3 repos x 20 paired seeds.
  - v4_exp3_velocity         : Velocity sweep (1x, 3x, 5x, 10x) x 6 strategies.
  - v4_exp4_ablation         : Deconfounded 8-variant ablation separating
                               SSMQ-NoCone, SSMQ-NoRisk, SSMQ-NoGraph,
                               SSMQ-NoFlaky, SSMQ-FixedRetry, SSMQ-NoBlame.
  - v4_exp5_graph_recall     : Fixed-density non-circularity sweep
                               (graph_recall 0.40..1.00 + zero-conflict control).
  - v4_exp6_flaky_sweep      : Flaky trade-off sweep (0%..15%) including matched
                               SSMQ-FixedRetry and SSMQ-NoFlaky inside SSMQ.
  - v4_exp7_blame_sweep      : Blame Top-1 accuracy sweep (0.50..measured_top1).
  - v4_exp8_blame_regime     : Controlled 2x2 factorial sweep (defect 8%/16% x
                               cap K=8/12 at fixed recall=0.80) with full root
                               batch size & culprit multiplicity instrumentation.
"""
from __future__ import annotations

import argparse
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
    "FIFOOutcomeTree",
    "DepAwareOutcomeTree",
    "FlakeAwareBatch",
    "SSMQ",
]

ABLATION_STRATEGIES = [
    "SSMQ",
    "SSMQ-NoCone",
    "SSMQ-NoRisk",
    "SSMQ-NoGraph",
    "SSMQ-NoFlaky",
    "SSMQ-FixedRetry",
    "SSMQ-NoBlame",
    "StaticBatchBisect",
]

SEEDS_MAIN = list(range(1001, 1021))  # 20 deterministic paired seeds
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
    *,
    force: bool = False,
) -> Path:
    run_dir = REPO_ROOT / "runs" / run_id
    if (run_dir / "manifest.json").exists() and not force:
        raise FileExistsError(f"Refusing to overwrite sealed run {run_id}; pass --force or a new version.")
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
        "schema_version": "4.0",
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
    parser = argparse.ArgumentParser(description="Frozen Sealed Experiment Harness v4.")
    parser.add_argument("--force", action="store_true", help="Allow overwriting existing v4 run directories.")
    args = parser.parse_args()

    # EXP-0: 80-batch real executed AST/stack-trace causal blame micro-benchmark
    blame_summary, blame_records = run_blame_benchmark(seed=2026)
    seal_run(
        "v4_exp0_blame_microbench",
        "v4_exp0_blame_microbench",
        [2026],
        {"summary": blame_summary.to_dict(), "records": blame_records},
        {"type": "executable_python_unittest_microbench", "batches": blame_summary.total_failing_batches},
        force=args.force,
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

    # EXP-1: Disjoint A/A Noise Calibration
    aa_rows: List[Dict[str, Any]] = []
    for block_name, seeds in (("A", SEEDS_MAIN), ("B", SEEDS_AA_B)):
        for repo_name, (g, prs) in loaded.items():
            for s in seeds:
                m = simulate_queue(g, prs, "SSMQ", base_cfg, s).to_dict()
                m["repo"] = repo_name
                m["block"] = block_name
                aa_rows.append(m)
    seal_run(
        "v4_exp1_aa_noise",
        "v4_exp1_aa_noise",
        SEEDS_MAIN + SEEDS_AA_B,
        aa_rows,
        {"graph_recall": 0.80, "flaky_rate": 0.05, "persistent_conflict_share": 0.03},
        force=args.force,
    )

    # EXP-2: Main Benchmark (7 strategies incl. FIFOOutcomeTree & DepAwareOutcomeTree)
    main_rows: List[Dict[str, Any]] = []
    for repo_name, (g, prs) in loaded.items():
        for strat in MAIN_STRATEGIES:
            for s in SEEDS_MAIN:
                m = simulate_queue(g, prs, strat, base_cfg, s).to_dict()
                m["repo"] = repo_name
                main_rows.append(m)
    seal_run(
        "v4_exp2_main_bench",
        "v4_exp2_main_bench",
        SEEDS_MAIN,
        main_rows,
        {
            "strategies": MAIN_STRATEGIES,
            "repos": list(TRACES.keys()),
            "graph_recall": 0.80,
            "persistent_conflict_share": 0.03,
            "blame_top1_acc": measured_top1,
        },
        force=args.force,
    )

    # EXP-3: Velocity Surge Sweep (1x, 3x, 5x, 10x)
    vel_rows: List[Dict[str, Any]] = []
    velocities = [1.0, 3.0, 5.0, 10.0]
    vel_strats = [
        "StaticBatchBisect",
        "SpeculativeLinear",
        "FIFOOutcomeTree",
        "DepAwareOutcomeTree",
        "FlakeAwareBatch",
        "SSMQ",
    ]
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
        "v4_exp3_velocity",
        "v4_exp3_velocity",
        SEEDS_MAIN,
        vel_rows,
        {"velocities": velocities, "strategies": vel_strats},
        force=args.force,
    )

    # EXP-4: Deconfounded 8-Variant Ablation
    abl_rows: List[Dict[str, Any]] = []
    for repo_name, (g, prs) in loaded.items():
        for strat in ABLATION_STRATEGIES:
            for s in SEEDS_MAIN:
                m = simulate_queue(g, prs, strat, base_cfg, s).to_dict()
                m["repo"] = repo_name
                abl_rows.append(m)
    seal_run(
        "v4_exp4_ablation",
        "v4_exp4_ablation",
        SEEDS_MAIN,
        abl_rows,
        {"strategies": ABLATION_STRATEGIES},
        force=args.force,
    )

    # EXP-5: Fixed-Density Non-Circularity Sweep (graph_recall 0.40..1.00 + Zero-Conflict)
    rec_rows: List[Dict[str, Any]] = []
    rec_settings = [
        ("no_conflict", 0.0, 1.0),
        ("recall_0.40", 0.18, 0.40),
        ("recall_0.60", 0.18, 0.60),
        ("recall_0.80", 0.18, 0.80),
        ("recall_1.00", 0.18, 1.00),
    ]
    rec_strats = ["StaticBatchBisect", "FIFOOutcomeTree", "DepAwareOutcomeTree", "SSMQ"]
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
        "v4_exp5_graph_recall",
        "v4_exp5_graph_recall",
        SEEDS_MAIN,
        rec_rows,
        {"regimes": [x[0] for x in rec_settings], "strategies": rec_strats},
        force=args.force,
    )

    # EXP-6: Flaky Trade-Off Sweep (0%..15%) including matched SSMQ-FixedRetry & SSMQ-NoFlaky
    flaky_rows: List[Dict[str, Any]] = []
    flaky_rates = [0.0, 0.02, 0.05, 0.10, 0.15]
    flaky_strats = [
        "StaticBatchBisect",
        "FlakeAwareBatch",
        "DepAwareOutcomeTree",
        "SSMQ-NoFlaky",
        "SSMQ-FixedRetry",
        "SSMQ",
    ]
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
        "v4_exp6_flaky_sweep",
        "v4_exp6_flaky_sweep",
        SEEDS_MAIN,
        flaky_rows,
        {"flaky_rates": flaky_rates, "strategies": flaky_strats},
        force=args.force,
    )

    # EXP-7: Blame Accuracy Sweep holding Top-2 offset explicit
    blame_rows: List[Dict[str, Any]] = []
    blame_accs = [0.50, 0.70, 0.85, measured_top1]
    for acc in blame_accs:
        top2 = min(0.99, max(acc, 0.95))
        cfg = SimConfig(**{**base_cfg.__dict__, "blame_top1_acc": acc, "blame_top2_acc": top2})
        for repo_name, (g, prs) in loaded.items():
            for strat in ("SSMQ", "SSMQ-NoBlame"):
                for s in SEEDS_MAIN:
                    m = simulate_queue(g, prs, strat, cfg, s).to_dict()
                    m["repo"] = repo_name
                    m["blame_top1_acc"] = round(acc, 4)
                    blame_rows.append(m)
    seal_run(
        "v4_exp7_blame_sweep",
        "v4_exp7_blame_sweep",
        SEEDS_MAIN,
        blame_rows,
        {"blame_top1_accs": [round(a, 4) for a in blame_accs]},
        force=args.force,
    )

    # EXP-8: Controlled 2x2 Factorial Blame-Regime Sweep (fixed graph_recall=0.80)
    regime_rows: List[Dict[str, Any]] = []
    regimes = [
        ("def8_k8", 0.08, 8),
        ("def16_k8", 0.16, 8),
        ("def8_k12", 0.08, 12),
        ("def16_k12", 0.16, 12),
    ]
    regime_strats = ["StaticBatchBisect", "SSMQ-NoBlame", "SSMQ"]
    for label, drate, kmax in regimes:
        cfg = SimConfig(**{
            **base_cfg.__dict__,
            "base_defect_rate": drate,
            "max_batch_size": kmax,
            "graph_recall": 0.80,
        })
        for repo_name, (g, prs) in loaded.items():
            for strat in regime_strats:
                for s in SEEDS_MAIN:
                    m = simulate_queue(g, prs, strat, cfg, s).to_dict()
                    m["repo"] = repo_name
                    m["regime"] = label
                    m["base_defect_rate"] = drate
                    m["max_batch_size"] = kmax
                    regime_rows.append(m)
    seal_run(
        "v4_exp8_blame_regime",
        "v4_exp8_blame_regime",
        SEEDS_MAIN,
        regime_rows,
        {"regimes": [r[0] for r in regimes], "strategies": regime_strats, "graph_recall": 0.80},
        force=args.force,
    )


if __name__ == "__main__":
    main()
