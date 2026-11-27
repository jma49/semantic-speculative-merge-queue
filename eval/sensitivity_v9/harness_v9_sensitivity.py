#!/usr/bin/env python3
"""Execute and seal v9 paired simulation sensitivities under runs/v9_sensitivity/.

Two focused same-family sensitivities comparing fixed v8 unstacked policies
(`SSMQ-FilteredCone` vs `SSMQ-NoCone`) without modifying any frozen engine:

  1. Longer CI duration (`ci_duration_min` in {12.0 default, 16.0, 20.0} min,
     keeping `ci_duration_std=2.0`, `persistent_conflict_share=0.03`, and all
     other features fixed). Motivated by observed TypeScript `merge_group`
     GitHub Actions workflow-run durations (n=45: P50=16.0m, mean=16.1m,
     P95=18.8m) — explicitly distinct from observed GraphQL queue sojourn
     times (enqueue to removal, which include queueing wait and serialization:
     TS P50=17.1m/P95=21.3m; Cargo P50=34.2m/P95=39.2m).

  2. Higher persistent-conflict fraction (`persistent_conflict_share` in
     {0.03 default, 0.10, 0.25}, keeping `ci_duration_min=12.0`, total sampled
     conflict pairs, unary defects, on/off-graph edges, and decoupled fixed
     scheduler priors identical per (repo, seed)).

Evaluated across 20 paired seeds (1001..1020) x 3 frozen v7 repositories
(`kubernetes`, `typescript`, `nextjs`) at both nominal (5x, 30 PRs/hr) and
saturated (15x, 90 PRs/hr) loads.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from eval.baselines_v8.engine import (  # noqa: E402
    load_trace_with_warmup,
    simulate_v8_queue,
)
from ssmq import SimConfig  # noqa: E402

RUN_ID = "v9_sensitivity"
STRATEGIES: Tuple[str, ...] = ("SSMQ-NoCone", "SSMQ-FilteredCone")
TRACES: Dict[str, str] = {
    "kubernetes": "kubernetes_kubernetes",
    "typescript": "microsoft_TypeScript",
    "nextjs": "vercel_next_js",
}
SEEDS: List[int] = list(range(1001, 1021))

# Identical base config to frozen v7/v8 (eval/baselines_v8/harness_v8_baselines.py).
BASE = SimConfig(
    semantic_conflict_prob=0.18,
    persistent_conflict_share=0.03,
    blame_overhead_min=0.00004,
    scheduler_defect_prior=0.10,
    scheduler_conflict_prior=0.10,
    scheduler_persistent_prior=0.02,
)

CONDITIONS: Dict[str, SimConfig] = {
    # Nominal load (5x, 30 PRs/hr)
    "nominal_default": BASE,
    "nominal_ci16": replace(BASE, ci_duration_min=16.0),
    "nominal_ci20": replace(BASE, ci_duration_min=20.0),
    "nominal_persist10": replace(BASE, persistent_conflict_share=0.10),
    "nominal_persist25": replace(BASE, persistent_conflict_share=0.25),
    # Saturated load (15x, 90 PRs/hr)
    "saturated_default": replace(BASE, velocity_multiplier=15.0),
    "saturated_ci16": replace(BASE, velocity_multiplier=15.0, ci_duration_min=16.0),
    "saturated_ci20": replace(BASE, velocity_multiplier=15.0, ci_duration_min=20.0),
    "saturated_persist10": replace(BASE, velocity_multiplier=15.0, persistent_conflict_share=0.10),
    "saturated_persist25": replace(BASE, velocity_multiplier=15.0, persistent_conflict_share=0.25),
}

CONDITION_META: Dict[str, Dict[str, Any]] = {
    "nominal_default": {
        "load": "nominal",
        "velocity_multiplier": 5.0,
        "sweep": "default",
        "ci_duration_min": 12.0,
        "ci_duration_std": 2.0,
        "persistent_conflict_share": 0.03,
        "v8_parity_condition": "nominal",
    },
    "nominal_ci16": {
        "load": "nominal",
        "velocity_multiplier": 5.0,
        "sweep": "ci_duration",
        "ci_duration_min": 16.0,
        "ci_duration_std": 2.0,
        "persistent_conflict_share": 0.03,
    },
    "nominal_ci20": {
        "load": "nominal",
        "velocity_multiplier": 5.0,
        "sweep": "ci_duration",
        "ci_duration_min": 20.0,
        "ci_duration_std": 2.0,
        "persistent_conflict_share": 0.03,
    },
    "nominal_persist10": {
        "load": "nominal",
        "velocity_multiplier": 5.0,
        "sweep": "persistent_share",
        "ci_duration_min": 12.0,
        "ci_duration_std": 2.0,
        "persistent_conflict_share": 0.10,
    },
    "nominal_persist25": {
        "load": "nominal",
        "velocity_multiplier": 5.0,
        "sweep": "persistent_share",
        "ci_duration_min": 12.0,
        "ci_duration_std": 2.0,
        "persistent_conflict_share": 0.25,
    },
    "saturated_default": {
        "load": "saturated",
        "velocity_multiplier": 15.0,
        "sweep": "default",
        "ci_duration_min": 12.0,
        "ci_duration_std": 2.0,
        "persistent_conflict_share": 0.03,
        "v8_parity_condition": "saturated",
    },
    "saturated_ci16": {
        "load": "saturated",
        "velocity_multiplier": 15.0,
        "sweep": "ci_duration",
        "ci_duration_min": 16.0,
        "ci_duration_std": 2.0,
        "persistent_conflict_share": 0.03,
    },
    "saturated_ci20": {
        "load": "saturated",
        "velocity_multiplier": 15.0,
        "sweep": "ci_duration",
        "ci_duration_min": 20.0,
        "ci_duration_std": 2.0,
        "persistent_conflict_share": 0.03,
    },
    "saturated_persist10": {
        "load": "saturated",
        "velocity_multiplier": 15.0,
        "sweep": "persistent_share",
        "ci_duration_min": 12.0,
        "ci_duration_std": 2.0,
        "persistent_conflict_share": 0.10,
    },
    "saturated_persist25": {
        "load": "saturated",
        "velocity_multiplier": 15.0,
        "sweep": "persistent_share",
        "ci_duration_min": 12.0,
        "ci_duration_std": 2.0,
        "persistent_conflict_share": 0.25,
    },
}

PROTOCOL_NOTES: Dict[str, Any] = {
    "policies": list(STRATEGIES),
    "policy_family": (
        "Fixed v8 same-family unstacked multi-worker policies (W=8, K<=8, risk lanes, "
        "Beta-Binomial adaptive retry, 1-step blame, strict exact-tree commit authority, "
        "zero commit-time oracle, zero escaped defects)."
    ),
    "decoupled_priors": (
        "Fixed generic scheduler priors (q0=0.10, p_c=0.10, p_persist=0.02, Beta(1,19) flake prior) "
        "held completely invariant across all CI-duration and persistent-conflict sensitivity levels."
    ),
    "ci_duration_motivation_and_distinction": (
        "Sweeps mean CI job duration ci_duration_min in {12.0 (default), 16.0, 20.0} min with fixed "
        "ci_duration_std=2.0 min. The 16.0 and 20.0 min levels bracket observed TypeScript merge_group "
        "GitHub Actions workflow-run execution times (n=45 selected runs: P50=16.0 min, mean=16.1 min, "
        "P95=18.8 min). These workflow execution times are explicitly distinct from observed GraphQL "
        "merge-queue episode sojourn times (enqueue to removal, which include queueing wait and "
        "serialization: TypeScript P50=17.1 min, P95=21.3 min, n=44; Cargo P50=34.2 min, mean=33.5 min, "
        "P95=39.2 min, n=56). Simulation sensitivity therefore does NOT calibrate to Cargo sojourn as a "
        "CI job duration."
    ),
    "persistent_conflict_mechanism_and_caveat": (
        "Sweeps persistent_conflict_share in {0.03 (v7/v8 default), 0.10, 0.25 (SimConfig dataclass default)} "
        "while holding ci_duration_min=12.0, semantic_conflict_prob=0.18, graph_recall=0.80, risk_correlation=1.0, "
        "unary defects, total sampled conflict pairs, and decoupled scheduler priors fixed per (repo, seed). "
        "Because GroundTruth samples persistent pairs via deterministic hash thresholds over the fixed conflict set, "
        "persistent pairs form a strict monotone superset across 0.03 -> 0.10 -> 0.25. Higher persistence causes "
        "more PRs to fail persistently against merged_mainline and be rejected, shrinking the merged survivor set "
        "over which P95 latency is computed; survivor P95 must therefore be interpreted jointly with accepted PRs "
        "and persistent-conflict rejections."
    ),
    "uncertainty_protocol": (
        "20 paired seeds (1001..1020) x 3 repositories = 60 paired blocks per condition; "
        "10,000 paired seed-block bootstrap resamples (Random(11629)) with equal repository weight "
        "plus exact two-sided sign test."
    ),
}

TRACE_FILES = [ROOT / "data/traces" / f"{stem}.json" for stem in TRACES.values()]
SRC_FILES = [
    ROOT / "src/ssmq" / name
    for name in ("__init__.py", "models.py", "simulator.py", "graph.py", "loader.py")
]
V8_ENGINE_FILES = [
    ROOT / "eval/baselines_v8" / name
    for name in ("__init__.py", "engine.py")
]
V9_FILES = sorted(
    EVAL_DIR / name
    for name in (
        "__init__.py",
        "harness_v9_sensitivity.py",
        "export_v9_sensitivity.py",
        "verify_v9_sensitivity.py",
        "test_sensitivity_v9.py",
    )
)


@lru_cache(maxsize=None)
def _load_repo(repo: str):
    return load_trace_with_warmup(ROOT / "data/traces" / f"{TRACES[repo]}.json")


def _run_cell(args: Tuple[str, int, str, SimConfig]) -> List[Dict[str, Any]]:
    repo, seed, condition, cfg = args
    graph, fgraph, records = _load_repo(repo)
    rows: List[Dict[str, Any]] = []
    for strategy in STRATEGIES:
        metrics = simulate_v8_queue(graph, records, strategy, cfg, seed, filtered_graph=fgraph)
        if metrics.escaped_defects != 0:
            raise RuntimeError(
                f"Exact-tree invariant violated: {strategy} escaped {metrics.escaped_defects} "
                f"in {condition}/{repo}/seed={seed}"
            )
        rows.append(dict(metrics.to_dict(), repo=repo, condition=condition))
    return rows


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def verify_default_parity(rows: List[Dict[str, Any]], root: Path = ROOT) -> Dict[str, int]:
    """Verify exact numerical identity of default anchor rows vs sealed v8_baselines and v7_sim_main."""
    v8_cond_map = {
        "nominal": "nominal_default",
        "saturated": "saturated_default",
    }
    v8_index: Dict[Tuple[str, str, int, str], Dict[str, Any]] = {}
    v8_path = root / "runs" / "v8_baselines" / "metrics.json"
    if not v8_path.is_file():
        raise FileNotFoundError(f"Missing sealed v8 reference metrics: {v8_path}")
    for row in json.loads(v8_path.read_text(encoding="utf-8")):
        mapped = v8_cond_map.get(row["condition"])
        if mapped and row["strategy"] in STRATEGIES:
            v8_index[(mapped, row["repo"], int(row["seed"]), row["strategy"])] = row

    v7_index: Dict[Tuple[str, str, int, str], Dict[str, Any]] = {}
    v7_path = root / "runs" / "v7_sim_main" / "metrics.json"
    if not v7_path.is_file():
        raise FileNotFoundError(f"Missing sealed v7 reference metrics: {v7_path}")
    for row in json.loads(v7_path.read_text(encoding="utf-8")):
        mapped = v8_cond_map.get(row["condition"])
        if mapped and row["strategy"] == "SSMQ-NoCone":
            v7_index[(mapped, row["repo"], int(row["seed"]), row["strategy"])] = row

    def _compare(cur: Dict[str, Any], ref: Dict[str, Any], label: str, key: Tuple[str, str, int, str]) -> None:
        for field_name, ref_val in ref.items():
            if field_name == "condition":
                continue
            cur_val = cur[field_name]
            if isinstance(ref_val, float):
                if abs(cur_val - ref_val) > 1e-12:
                    raise ValueError(f"{label} parity mismatch at {key}.{field_name}: {cur_val} != {ref_val}")
            elif cur_val != ref_val:
                raise ValueError(f"{label} parity mismatch at {key}.{field_name}: {cur_val} != {ref_val}")

    checked_v8 = 0
    checked_v7 = 0
    for row in rows:
        key = (row["condition"], row["repo"], int(row["seed"]), row["strategy"])
        if key in v8_index:
            _compare(row, v8_index[key], "v8_baselines", key)
            checked_v8 += 1
        if key in v7_index:
            _compare(row, v7_index[key], "v7_sim_main", key)
            checked_v7 += 1

    expected_v8 = len(v8_cond_map) * len(TRACES) * len(SEEDS) * len(STRATEGIES)
    expected_v7 = len(v8_cond_map) * len(TRACES) * len(SEEDS)
    if checked_v8 != expected_v8 or len(v8_index) != expected_v8:
        raise ValueError(
            f"Incomplete v8 default parity check: checked={checked_v8}, indexed={len(v8_index)}, expected={expected_v8}"
        )
    if checked_v7 != expected_v7 or len(v7_index) != expected_v7:
        raise ValueError(
            f"Incomplete v7 default parity check: checked={checked_v7}, indexed={len(v7_index)}, expected={expected_v7}"
        )
    return {
        "checked_v8_rows": checked_v8,
        "expected_v8_rows": expected_v8,
        "checked_v7_rows": checked_v7,
        "expected_v7_rows": expected_v7,
    }


def run_all_cells(workers: int) -> List[Dict[str, Any]]:
    cells = [
        (repo, seed, cond, cfg)
        for cond, cfg in CONDITIONS.items()
        for repo in TRACES
        for seed in SEEDS
    ]
    if workers <= 1:
        return [row for cell in cells for row in _run_cell(cell)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return [row for cell in pool.map(_run_cell, cells) for row in cell]


def seal(rows: List[Dict[str, Any]], destination: Path, inputs: Dict[str, str], parity: Dict[str, int]) -> Path:
    def combined(paths: List[Path]) -> str:
        return sha256(
            "".join(p.name + inputs[str(p.relative_to(ROOT))] for p in sorted(paths)).encode()
        ).hexdigest()

    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except subprocess.CalledProcessError:
        commit = "anonymous-reproduction"

    target = destination / RUN_ID
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing sealed run directory: {target}")

    with tempfile.TemporaryDirectory(dir=destination) as temp:
        stage = Path(temp) / RUN_ID
        stage.mkdir()
        metrics_path = stage / "metrics.json"
        metrics_path.write_text(json.dumps(rows, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        metrics_hash = digest(metrics_path)
        manifest = {
            "schema_version": "9.0",
            "run_id": RUN_ID,
            "experiment": RUN_ID,
            "git_commit": commit,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "git_version": subprocess.check_output(["git", "--version"], text=True).strip(),
            "harness_sha256": combined(SRC_FILES + V8_ENGINE_FILES + V9_FILES),
            "dataset_sha256": combined(TRACE_FILES),
            "input_sha256": inputs,
            "seeds": SEEDS,
            "strategies": list(STRATEGIES),
            "repos": list(TRACES.keys()),
            "record_count": len(rows),
            "metrics_file": "metrics.json",
            "metrics_sha256": metrics_hash,
            "artifact_sha256": metrics_hash,
            "default_parity": parity,
            "protocol_notes": PROTOCOL_NOTES,
            "command": "python3 eval/sensitivity_v9/harness_v9_sensitivity.py --runs-dir <output-directory>",
            "meta": {
                cond: {
                    **CONDITION_META[cond],
                    "config": asdict(cfg),
                    "strategies": list(STRATEGIES),
                    "repos": list(TRACES.keys()),
                }
                for cond, cfg in CONDITIONS.items()
            },
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        stage.rename(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1))
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    args = parser.parse_args()

    args.runs_dir.mkdir(parents=True, exist_ok=True)
    if (args.runs_dir / RUN_ID).exists():
        raise FileExistsError(f"Use a fresh --runs-dir or remove existing {args.runs_dir / RUN_ID}")

    all_inputs = TRACE_FILES + SRC_FILES + V8_ENGINE_FILES + V9_FILES
    inputs = {str(path.relative_to(ROOT)): digest(path) for path in all_inputs}

    rows = run_all_cells(args.workers)

    if inputs != {str(path.relative_to(ROOT)): digest(path) for path in all_inputs}:
        raise RuntimeError("Engine, harness, or trace files changed during execution")

    parity = verify_default_parity(rows)
    out_dir = seal(rows, args.runs_dir, inputs, parity)
    print(
        f"Sealed {RUN_ID}: {len(rows)} rows at {out_dir} "
        f"(default parity verified: {parity['checked_v8_rows']} v8 rows, {parity['checked_v7_rows']} v7 rows)",
        flush=True,
    )


if __name__ == "__main__":
    main()
