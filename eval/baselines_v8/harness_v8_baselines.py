#!/usr/bin/env python3
"""Execute and seal Batch 2 baseline experiments under runs/v8_baselines/."""

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
    ALL_V8_STRATEGIES,
    V7_CONTROL_STRATEGIES,
    load_trace_with_warmup,
    simulate_v8_queue,
)
from ssmq import SimConfig  # noqa: E402

RUN_ID = "v8_baselines"
TRACES = {
    "kubernetes": "kubernetes_kubernetes",
    "typescript": "microsoft_TypeScript",
    "nextjs": "vercel_next_js",
}
SEEDS = list(range(1001, 1021))

# Identical base config to frozen v7 (eval/harness_v7.py) for exact paired comparison.
BASE = SimConfig(
    semantic_conflict_prob=0.18,
    persistent_conflict_share=0.03,
    blame_overhead_min=0.00004,
    scheduler_defect_prior=0.10,
    scheduler_conflict_prior=0.10,
    scheduler_persistent_prior=0.02,
)

CONDITIONS: Dict[str, SimConfig] = {
    # Plausible effect regimes (identical to v7 main/nominal and main/saturated)
    "nominal": BASE,
    "saturated": replace(BASE, velocity_multiplier=15.0),
    # Non-circular directory-structure effect / risk-null regime (identical to v7 proxy/directory_proxy)
    "directory_proxy": replace(BASE, ground_truth_mode="independent_eval_coupling", risk_correlation=0.0),
    # Full structural + risk null regime (identical to v7 proxy/uniform_pairs)
    "null_uniform": replace(BASE, ground_truth_mode="uniform_pairs", risk_correlation=0.0),
}

TRACE_FILES = [ROOT / "data/traces" / f"{stem}.json" for stem in TRACES.values()]
SRC_FILES = [
    ROOT / "src/ssmq" / name
    for name in ("__init__.py", "models.py", "simulator.py", "graph.py", "loader.py")
]
V8_FILES = sorted(
    EVAL_DIR / name
    for name in (
        "__init__.py",
        "engine.py",
        "specs.py",
        "harness_v8_baselines.py",
        "export_v8_baselines.py",
        "verify_v8_baselines.py",
        "test_baselines_v8.py",
    )
)


@lru_cache(maxsize=None)
def _load_repo(repo: str):
    return load_trace_with_warmup(ROOT / "data/traces" / f"{TRACES[repo]}.json")


def _run_cell(args: Tuple[str, int, str, SimConfig]) -> List[Dict[str, Any]]:
    repo, seed, condition, cfg = args
    graph, fgraph, records = _load_repo(repo)
    rows: List[Dict[str, Any]] = []
    for strategy in ALL_V8_STRATEGIES:
        metrics = simulate_v8_queue(graph, records, strategy, cfg, seed, filtered_graph=fgraph)
        if metrics.escaped_defects != 0:
            raise RuntimeError(f"Exact-tree invariant violated: {strategy} escaped {metrics.escaped_defects}")
        rows.append(dict(metrics.to_dict(), repo=repo, condition=condition))
    return rows


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def verify_v7_control_parity(rows: List[Dict[str, Any]], root: Path = ROOT) -> Dict[str, int]:
    """Verify exact numerical identity against sealed runs/v7_sim_main and runs/v7_sim_proxy."""
    v7_index: Dict[Tuple[str, str, int, str], Dict[str, Any]] = {}
    cond_map = {
        ("v7_sim_main", "nominal"): "nominal",
        ("v7_sim_main", "saturated"): "saturated",
        ("v7_sim_proxy", "directory_proxy"): "directory_proxy",
        ("v7_sim_proxy", "uniform_pairs"): "null_uniform",
    }
    for run_id in ("v7_sim_main", "v7_sim_proxy"):
        metrics_path = root / "runs" / run_id / "metrics.json"
        if not metrics_path.is_file():
            continue
        for row in json.loads(metrics_path.read_text(encoding="utf-8")):
            mapped = cond_map.get((run_id, row["condition"]))
            if mapped and row["strategy"] in V7_CONTROL_STRATEGIES:
                v7_index[(mapped, row["repo"], row["seed"], row["strategy"])] = row

    checked = 0
    for row in rows:
        key = (row["condition"], row["repo"], row["seed"], row["strategy"])
        if key not in v7_index:
            continue
        ref = v7_index[key]
        for field_name, ref_val in ref.items():
            if field_name == "condition":
                continue
            cur_val = row[field_name]
            if isinstance(ref_val, float):
                if abs(cur_val - ref_val) > 1e-12:
                    raise ValueError(f"v7 control parity mismatch at {key}.{field_name}: {cur_val} != {ref_val}")
            elif cur_val != ref_val:
                raise ValueError(f"v7 control parity mismatch at {key}.{field_name}: {cur_val} != {ref_val}")
        checked += 1
    if checked == 0:
        raise ValueError("Expected non-zero v7 control parity comparisons against sealed v7 runs")
    return {"checked_control_rows": checked, "expected_v7_index_rows": len(v7_index)}


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
            "schema_version": "8.0",
            "run_id": RUN_ID,
            "experiment": RUN_ID,
            "git_commit": commit,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "git_version": subprocess.check_output(["git", "--version"], text=True).strip(),
            "harness_sha256": combined(SRC_FILES + V8_FILES),
            "dataset_sha256": combined(TRACE_FILES),
            "input_sha256": inputs,
            "seeds": SEEDS,
            "record_count": len(rows),
            "metrics_file": "metrics.json",
            "metrics_sha256": metrics_hash,
            "artifact_sha256": metrics_hash,
            "v7_control_parity": parity,
            "command": "python3 eval/baselines_v8/harness_v8_baselines.py --runs-dir <output-directory>",
            "meta": {
                cond: {
                    "config": asdict(cfg),
                    "strategies": list(ALL_V8_STRATEGIES),
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

    all_inputs = TRACE_FILES + SRC_FILES + V8_FILES
    inputs = {str(path.relative_to(ROOT)): digest(path) for path in all_inputs}

    cells = [
        (repo, seed, cond, cfg)
        for cond, cfg in CONDITIONS.items()
        for repo in TRACES
        for seed in SEEDS
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = [row for cell in pool.map(_run_cell, cells) for row in cell]

    if inputs != {str(path.relative_to(ROOT)): digest(path) for path in all_inputs}:
        raise RuntimeError("Engine or trace files changed during execution")

    parity = verify_v7_control_parity(rows)
    out_dir = seal(rows, args.runs_dir, inputs, parity)
    print(
        f"Sealed {RUN_ID}: {len(rows)} rows at {out_dir} "
        f"(v7 control parity verified on {parity['checked_control_rows']} rows)",
        flush=True,
    )


if __name__ == "__main__":
    main()
