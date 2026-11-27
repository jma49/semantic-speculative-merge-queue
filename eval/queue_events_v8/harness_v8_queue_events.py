#!/usr/bin/env python3
"""Orchestrate fetch, ledger reconcile, summarize, seal runs/v8_queue_events."""
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
RUN_ID = "v8_queue_events"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dataset_sha256(data_dir: Path) -> str:
    h = hashlib.sha256()
    for name in sorted(
        [
            "raw_records.json",
            "provenance.json",
            "descriptive_summary.json",
            "descriptive_summary_schema_1_0_superseded.json",
            "author_erratum_v8_1.json",
            "historical_ci_selection_ledger.json",
            "historical_ci_errata.json",
        ]
    ):
        p = data_dir / name
        h.update(name.encode())
        h.update(_sha256(p).encode())
    return h.hexdigest()


def _harness_sha256() -> str:
    h = hashlib.sha256()
    for rel in sorted(EVAL_DIR.rglob("*")):
        if rel.is_file() and rel.suffix in {".py", ".json", ".ts"}:
            h.update(str(rel.relative_to(EVAL_DIR)).encode())
            h.update(_sha256(rel).encode())
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    args = parser.parse_args()
    data_dir = ROOT / "data" / "queue_events_v8"

    env = {**dict(__import__("os").environ), "PYTHONPATH": str(ROOT)}
    try:
        env["GH_TOKEN"] = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception:
        pass

    if not args.skip_fetch:
        subprocess.check_call(
            [sys.executable, str(EVAL_DIR / "fetch_queue_events.py"), "--data-dir", str(data_dir)],
            cwd=str(ROOT),
            env=env,
        )
    subprocess.check_call(
        [sys.executable, str(EVAL_DIR / "reconcile_historical_ci_ledger.py")],
        cwd=str(ROOT),
        env=env,
    )
    subprocess.check_call(
        [sys.executable, str(EVAL_DIR / "summarize_queue_events.py"), "--data-dir", str(data_dir)],
        cwd=str(ROOT),
        env=env,
    )

    summary = json.loads((data_dir / "descriptive_summary.json").read_text(encoding="utf-8"))
    ts_repo = summary["repositories"].get("typescript") or {}
    cargo_repo = summary["repositories"].get("cargo") or {}
    ts_raw = (ts_repo.get("raw_timeline_event_counts") or {})
    cargo_raw = (cargo_repo.get("raw_timeline_event_counts") or {})
    ledger = json.loads((data_dir / "historical_ci_selection_ledger.json").read_text(encoding="utf-8"))

    metrics = {
        "schema_version": "8.2",
        "run_id": RUN_ID,
        "observation_calendar_days_inclusive": len(summary.get("observation_calendar_days_inclusive") or []),
        "typescript_prs_fetched": ts_repo.get("pull_requests_fetched"),
        "typescript_distinct_prs_enqueue_in_week": ts_raw.get("distinct_prs_with_enqueue_in_week"),
        "typescript_queue_admissions_in_week": ts_raw.get("added_to_merge_queue_in_week"),
        "typescript_prs_with_episode_overlapping_week": ts_repo.get("pulls_with_episode_overlapping_observation_week"),
        "cargo_prs_fetched": cargo_repo.get("pull_requests_fetched"),
        "cargo_distinct_prs_enqueue_in_week": cargo_raw.get("distinct_prs_with_enqueue_in_week"),
        "cargo_queue_admissions_in_week": cargo_raw.get("added_to_merge_queue_in_week"),
        "cargo_prs_with_episode_overlapping_week": cargo_repo.get("pulls_with_episode_overlapping_observation_week"),
        "combined_distinct_prs_enqueue_in_week": (summary.get("combined_repositories") or {}).get(
            "distinct_prs_with_enqueue_in_week"
        ),
        "combined_queue_admissions_in_week": (summary.get("combined_repositories") or {}).get(
            "queue_admissions_in_week"
        ),
        "historical_ci_scanned": ledger["ledger"]["merge_group_runs_scanned_api_total"],
        "historical_ci_selected": ledger["ledger"]["merge_group_runs_selected"],
        "historical_ci_excluded_pr_number_window": ledger["ledger"]["excluded_pr_outside_number_window"]["count"],
        "discordant_head_cases_historical_ci": len(ledger.get("discordant_head_cases") or []),
        "typescript_discordant_merge_group_heads": (ts_repo.get("merge_group_crosswalk") or {}).get(
            "discordant_head_count"
        ),
        "descriptive_summary_schema_version": summary.get("schema_version"),
    }

    run_dir = args.runs_dir / RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = run_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = {
        "schema_version": "8.2",
        "run_id": RUN_ID,
        "experiment": RUN_ID,
        "git_commit": git_commit,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "harness_sha256": _harness_sha256(),
        "dataset_sha256": _dataset_sha256(data_dir),
        "input_sha256": {
            "eval/queue_events_v8/sample_spec.json": _sha256(EVAL_DIR / "sample_spec.json"),
            "eval/historical_ci/sample_spec.json": _sha256(ROOT / "eval/historical_ci/sample_spec.json"),
        },
        "record_count": metrics["combined_queue_admissions_in_week"],
        "metrics_file": "metrics.json",
        "metrics_sha256": _sha256(metrics_path),
        "artifact_sha256": _sha256(metrics_path),
        "command": "python3 eval/queue_events_v8/harness_v8_queue_events.py",
        "meta": {
            "observation_week": summary.get("observation_week"),
            "repositories": ["microsoft/TypeScript", "rust-lang/cargo"],
            "supersedes_schema_8_0_metrics": True,
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    subprocess.check_call(
        [sys.executable, str(EVAL_DIR / "verify_queue_events.py")],
        cwd=str(ROOT),
        env=env,
    )
    print(json.dumps({"run_dir": str(run_dir), "metrics": metrics}, indent=2))


if __name__ == "__main__":
    main()