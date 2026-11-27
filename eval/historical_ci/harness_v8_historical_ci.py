#!/usr/bin/env python3
"""Orchestrate fetch, summarize, seal runs/v8_historical_ci."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
RUN_ID = "v8_historical_ci"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dataset_sha256(data_dir: Path) -> str:
    h = hashlib.sha256()
    for name in sorted(["raw_records.json", "provenance.json", "descriptive_summary.json"]):
        p = data_dir / name
        h.update(name.encode())
        h.update(_sha256(p).encode())
    return h.hexdigest()


def _harness_sha256() -> str:
    h = hashlib.sha256()
    for rel in sorted(Path(EVAL_DIR).glob("*.py")):
        h.update(rel.name.encode())
        h.update(_sha256(rel).encode())
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-fetch", action="store_true", help="Reuse existing raw_records.json")
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    args = parser.parse_args()
    data_dir = ROOT / "data" / "historical_ci"

    env = {**dict(__import__("os").environ)}
    env["PYTHONPATH"] = str(ROOT)
    try:
        tok = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
        env["GH_TOKEN"] = tok
    except Exception:
        pass

    if not args.skip_fetch:
        subprocess.check_call(
            [sys.executable, str(EVAL_DIR / "fetch_historical_ci.py"), "--data-dir", str(data_dir)],
            cwd=str(ROOT),
            env=env,
        )
    subprocess.check_call(
        [sys.executable, str(EVAL_DIR / "summarize_historical_ci.py"), "--data-dir", str(data_dir)],
        cwd=str(ROOT),
        env=env,
    )

    summary = json.loads((data_dir / "descriptive_summary.json").read_text(encoding="utf-8"))
    raw = json.loads((data_dir / "raw_records.json").read_text(encoding="utf-8"))
    metrics = {
        "schema_version": "8.0",
        "run_id": RUN_ID,
        "merge_group_runs_selected": raw["counts"]["merge_group_runs_selected"],
        "pull_requests_stored": raw["counts"]["pull_requests_stored"],
        "merge_group_failures": summary["merge_group_conclusions"].get("failure", 0),
        "integration_regression_cases": summary["failure_attribution_assay"]["label_histogram"].get(
            "integration_regression_at_least_one_check", 0
        ),
        "closed_unmerged_pulls": summary["closed_unmerged_pulls"],
        "selection_caveats": len(raw.get("selection_caveats") or []),
        "fetch_failures": len(raw.get("fetch_failures") or []),
        "merge_group_duration_p50_min": summary["merge_group_duration_minutes"]["p50"],
    }

    run_dir = args.runs_dir / RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = run_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = {
        "schema_version": "8.0",
        "run_id": RUN_ID,
        "experiment": RUN_ID,
        "git_commit": git_commit,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "harness_sha256": _harness_sha256(),
        "dataset_sha256": _dataset_sha256(data_dir),
        "input_sha256": {
            "eval/historical_ci/sample_spec.json": _sha256(EVAL_DIR / "sample_spec.json"),
            "data/historical_ci/raw_records.json": _sha256(data_dir / "raw_records.json"),
        },
        "record_count": raw["counts"]["merge_group_runs_selected"],
        "metrics_file": "metrics.json",
        "metrics_sha256": _sha256(metrics_path),
        "artifact_sha256": _sha256(metrics_path),
        "command": "python3 eval/historical_ci/harness_v8_historical_ci.py",
        "meta": {
            "repository": raw["repository"]["full_name"],
            "inclusion_window": raw["inclusion_window"],
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    subprocess.check_call(
        [sys.executable, str(EVAL_DIR / "verify_historical_ci.py")],
        cwd=str(ROOT),
        env=env,
    )
    print(json.dumps({"run_dir": str(run_dir), "metrics": metrics}, indent=2))


if __name__ == "__main__":
    main()