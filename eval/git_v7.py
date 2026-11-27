"""Seal executable v7 results (matched direct-callee weights & counterbalanced Git IDs)."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ssmq.blame_bench import run_blame_benchmark
from ssmq.git_replay import (
    GIT_REPLAY_STRATEGIES,
    run_concurrent_git_safety_suite,
    run_git_queue_replay,
)

RUNS = {
    "blame": "v7_exp0_blame_microbench",
    "git": "v7_exp10_real_git_replay",
    "stale": "v7_exp11_concurrent_git_safety",
}
INPUTS = ("eval/git_v7.py", "src/ssmq/blame_bench.py", "src/ssmq/git_replay.py")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _run_git_cell(args):
    seed, reverse_ids, strategy = args
    return run_git_queue_replay(strategy, seed=seed, max_batch=4, reverse_ids=reverse_ids).to_dict()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=RUNS)
    parser.add_argument("--runs-dir", default=ROOT / "runs", type=Path)
    parser.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1))
    args = parser.parse_args()
    dest = args.runs_dir / RUNS[args.suite]
    if dest.exists():
        parser.error(f"{dest} already exists; choose a fresh --runs-dir")
    inputs = {name: digest((ROOT / name).read_bytes()) for name in INPUTS}
    if args.suite == "blame":
        seeds = [2026]
        summary, records = run_blame_benchmark(seeds[0])
        payload = {"summary": summary.to_dict(), "records": records}
    elif args.suite == "git":
        seeds = list(range(10))
        tasks = [
            (seed, reverse_ids, strategy)
            for seed in seeds
            for reverse_ids in (False, True)
            for strategy in GIT_REPLAY_STRATEGIES
        ]
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            records = list(pool.map(_run_git_cell, tasks))
        payload = records
    else:
        seeds = []
        payload = records = run_concurrent_git_safety_suite()
    if any(digest((ROOT / name).read_bytes()) != sha for name, sha in inputs.items()):
        raise RuntimeError("Inputs changed during execution")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as temp:
        staging = Path(temp) / dest.name
        staging.mkdir()
        serialized = (json.dumps(payload, indent=2) + "\n").encode()
        (staging / "metrics.json").write_bytes(serialized)
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True)
        manifest = dict(
            schema_version="7.0",
            run_id=dest.name,
            experiment=dest.name,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            git_commit=commit.stdout.strip() or "anonymous-source",
            harness_sha256=digest(json.dumps(inputs, sort_keys=True).encode()),
            dataset_sha256=inputs[INPUTS[1]],
            input_sha256=inputs,
            command=f"python3 eval/git_v7.py {args.suite}",
            seeds=seeds,
            record_count=len(records),
            metrics_file="metrics.json",
            metrics_sha256=digest(serialized),
            artifact_sha256=digest(serialized),
            python_version=platform.python_version(),
            platform=platform.platform(),
            git_version=subprocess.check_output(["git", "--version"], text=True).strip(),
        )
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        staging.rename(dest)
    print(f"Sealed {dest.name} ({len(records)} records)")


if __name__ == "__main__":
    main()
