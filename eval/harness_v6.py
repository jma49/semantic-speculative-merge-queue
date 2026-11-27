"""Reproduce the v6 simulation suites on exhaustive public merge-time windows."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ssmq import SimConfig, load_trace, simulate_queue

TRACES = {"kubernetes": "kubernetes_kubernetes", "typescript": "microsoft_TypeScript", "nextjs": "vercel_next_js"}
SEEDS = list(range(1001, 1021))
MAIN = ("StaticBatchBisect", "DepAwareOutcomeTree", "DepTreeRetry", "PerPRDAGRetry", "DisjointBatchRetry", "SSMQ")
CONTROLS = ("StaticBatchBisect", "DisjointBatchRetry", "SSMQ-NoCone", "SSMQ")
# Freeze a deliberately imperfect attribution setting, independent of measured fixture accuracy.
BASE = SimConfig(semantic_conflict_prob=0.18, persistent_conflict_share=0.03, blame_overhead_min=0.00004)
SUITES = {
    "main": {"default": (BASE, MAIN)},
    "recall": {f"recall_{r}": (replace(BASE, graph_recall=r, feasible_recall_budget=True), CONTROLS)
               for r in (0.0, 0.4, 0.6, 0.8, 1.0)},
    "proxy": {
        "directory_proxy": (replace(BASE, ground_truth_mode="independent_eval_coupling", risk_correlation=0), CONTROLS),
        "uniform_pairs": (replace(BASE, ground_truth_mode="uniform_pairs", risk_correlation=0), CONTROLS),
        "directory_no_persistence": (replace(BASE, ground_truth_mode="independent_eval_coupling", risk_correlation=0, persistent_conflict_share=0), CONTROLS),
        "directory_no_flakes": (replace(BASE, ground_truth_mode="independent_eval_coupling", risk_correlation=0, flaky_rate=0), CONTROLS),
    },
}
TRACE_FILES = [ROOT / "data/traces" / (stem + ".json") for stem in TRACES.values()]
SOURCE_FILES = [Path(__file__).resolve(), *(ROOT / "src/ssmq" / name for name in ("__init__.py", "models.py", "simulator.py", "graph.py", "loader.py"))]


@lru_cache(None)
def trace(repo):
    return load_trace(ROOT / "data/traces" / (TRACES[repo] + ".json"))


def run_cell(args):
    repo, seed, condition, cfg, strategies = args
    graph, records = trace(repo)
    return [dict(simulate_queue(graph, records, strategy, cfg, seed).to_dict(), repo=repo, condition=condition) for strategy in strategies]


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def seal(suite, rows, destination, inputs):
    def combined(paths):
        return sha256("".join(p.name + inputs[str(p.relative_to(ROOT))] for p in sorted(paths)).encode()).hexdigest()
    run_id = f"v6_sim_{suite}"
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except subprocess.CalledProcessError:
        commit = "anonymous-reproduction"
    with tempfile.TemporaryDirectory(dir=destination) as temp:
        path = Path(temp) / run_id
        path.mkdir()
        metric = path / "metrics.json"
        metric.write_text(json.dumps(rows, indent=2, allow_nan=False) + "\n")
        archive_hash = digest(metric)
        manifest = dict(schema_version="6.0", run_id=run_id, experiment=run_id, git_commit=commit,
                        timestamp_utc=datetime.now(timezone.utc).isoformat(), python_version=platform.python_version(),
                        platform=platform.platform(), git_version=subprocess.check_output(["git", "--version"], text=True).strip(),
                        harness_sha256=combined(SOURCE_FILES), dataset_sha256=combined(TRACE_FILES), input_sha256=inputs,
                        seeds=SEEDS, record_count=len(rows), metrics_file="metrics.json",
                        metrics_sha256=archive_hash, artifact_sha256=archive_hash,
                        command=f"python3 eval/harness_v6.py --suite {suite} --runs-dir <output-directory>",
                        meta={c: {"config": asdict(cfg), "strategies": list(strats)} for c,(cfg,strats) in SUITES[suite].items()})
        (path / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        path.rename(destination / run_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES)
    parser.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1))
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    args = parser.parse_args()
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    inputs = {str(path.relative_to(ROOT)): digest(path) for path in TRACE_FILES + SOURCE_FILES}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for suite in ([args.suite] if args.suite else SUITES):
            if (args.runs_dir / f"v6_sim_{suite}").exists():
                raise FileExistsError("Use a fresh --runs-dir to reproduce a sealed suite")
            cells = [(repo, seed, condition, cfg, strategies) for repo in TRACES for seed in SEEDS
                     for condition, (cfg, strategies) in SUITES[suite].items()]
            rows = [row for cell in pool.map(run_cell, cells) for row in cell]
            if inputs != {str(path.relative_to(ROOT)): digest(path) for path in TRACE_FILES + SOURCE_FILES}:
                raise RuntimeError("Engine or data changed during execution")
            seal(suite, rows, args.runs_dir, inputs)
            print(f"Sealed {suite}: {len(rows)} rows", flush=True)


if __name__ == "__main__":
    main()
