#!/usr/bin/env python3
"""Offline verifier for v8 queue events artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
SPEC_PATH = EVAL_DIR / "sample_spec.json"
RUN_ID = "v8_queue_events"
EXPECTED = {
    "typescript": {
        "distinct_prs_with_enqueue_in_week": 43,
        "added_to_merge_queue_in_week": 44,
        "pull_requests_fetched": 246,
        "pr_not_found_count": 164,
    },
    "cargo": {
        "distinct_prs_with_enqueue_in_week": 40,
        "added_to_merge_queue_in_week": 56,
        "pull_requests_fetched": 161,
        "pr_not_found_count": 71,
    },
    "combined": {"distinct_prs_with_enqueue_in_week": 83, "queue_admissions_in_week": 100},
    "calendar_days": 8,
}


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def verify_summary_against_raw(raw: dict, summary: dict) -> None:
    require(summary.get("schema_version") == "1.2", "descriptive_summary schema_version must be 1.2")
    require(len(summary.get("observation_calendar_days_inclusive") or []) == EXPECTED["calendar_days"], "calendar days")
    require(
        digest(ROOT / "data/queue_events_v8/raw_records.json") == summary.get("raw_records_sha256"),
        "raw_records_sha256 mismatch in summary",
    )

    for key, exp in EXPECTED.items():
        if key in ("combined", "calendar_days"):
            continue
        repo_sum = (summary.get("repositories") or {}).get(key) or {}
        counts = repo_sum.get("raw_timeline_event_counts") or {}
        require(counts.get("distinct_prs_with_enqueue_in_week") == exp["distinct_prs_with_enqueue_in_week"], key)
        require(counts.get("added_to_merge_queue_in_week") == exp["added_to_merge_queue_in_week"], key)
        require(repo_sum.get("pull_requests_fetched") == exp["pull_requests_fetched"], f"{key} fetched")
        fetch_cls = repo_sum.get("pr_fetch_completeness") or {}
        require(fetch_cls.get("pr_not_found_count") == exp["pr_not_found_count"], f"{key} not_found")

    comb = summary.get("combined_repositories") or {}
    require(
        comb.get("distinct_prs_with_enqueue_in_week") == EXPECTED["combined"]["distinct_prs_with_enqueue_in_week"],
        "combined distinct PRs",
    )
    require(
        comb.get("queue_admissions_in_week") == EXPECTED["combined"]["queue_admissions_in_week"],
        "combined admissions",
    )

    # Recompute raw counts independently from raw_records (no summary fields).
    from eval.queue_events_v8.raw_event_counts import count_raw_timeline_events  # noqa: WPS433

    t0, t1 = raw["observation_week"]["start"], raw["observation_week"]["end"]
    ws, we = _parse_ts(t0), _parse_ts(t1)
    for key, exp in EXPECTED.items():
        if key in ("combined", "calendar_days"):
            continue
        rt = count_raw_timeline_events(raw["repositories"][key], ws, we)
        require(rt["distinct_prs_with_enqueue_in_week"] == exp["distinct_prs_with_enqueue_in_week"], f"recompute {key}")
        require(rt["added_to_merge_queue_in_week"] == exp["added_to_merge_queue_in_week"], f"recompute {key} adds")

    for row in (summary.get("repositories") or {}).values():
        for pull in row.get("per_pull") or []:
            require(
                pull.get("comparable_hash_retry_status") == "unknown",
                "comparable_hash_retry must remain unknown",
            )
            require(pull.get("comparable_hash_retry_episodes") is None, "comparable_hash_retry_episodes must be null")


def verify_data(data_dir: Path) -> None:
    spec = read_json(SPEC_PATH)
    prov = read_json(data_dir / "provenance.json")
    require(prov.get("spec_sha256") == digest(SPEC_PATH), "sample_spec.json changed after fetch")
    raw = read_json(data_dir / "raw_records.json")
    require(raw.get("spec_sha256") == prov.get("spec_sha256"), "raw spec hash mismatch")
    summary_path = data_dir / "descriptive_summary.json"
    require(summary_path.is_file(), "missing descriptive_summary.json")
    summary = read_json(summary_path)
    verify_summary_against_raw(raw, summary)
    erratum = data_dir / "author_erratum_v8_1.json"
    require(erratum.is_file(), "missing author_erratum_v8_1.json")
    superseded = data_dir / "descriptive_summary_schema_1_0_superseded.json"
    require(superseded.is_file(), "missing superseded schema 1.0 snapshot")
    for key in spec["pr_universe"]["per_repository"]:
        require(key in (raw.get("repositories") or {}), f"missing repo {key} in raw")
        require(key in (summary.get("repositories") or {}), f"missing repo {key} in summary")
    ledger = data_dir / "historical_ci_selection_ledger.json"
    require(ledger.is_file(), "missing historical_ci_selection_ledger.json")


def verify_run_seal(root: Path) -> dict:
    manifest = read_json(root / f"runs/{RUN_ID}/manifest.json")
    metrics_path = root / f"runs/{RUN_ID}/metrics.json"
    require(manifest.get("metrics_sha256") == digest(metrics_path), "metrics seal")
    data_dir = root / "data/queue_events_v8"
    files = {
        "raw_records.json": digest(data_dir / "raw_records.json"),
        "provenance.json": digest(data_dir / "provenance.json"),
        "descriptive_summary.json": digest(data_dir / "descriptive_summary.json"),
        "descriptive_summary_schema_1_0_superseded.json": digest(
            data_dir / "descriptive_summary_schema_1_0_superseded.json"
        ),
        "author_erratum_v8_1.json": digest(data_dir / "author_erratum_v8_1.json"),
        "historical_ci_selection_ledger.json": digest(data_dir / "historical_ci_selection_ledger.json"),
        "historical_ci_errata.json": digest(data_dir / "historical_ci_errata.json"),
    }
    combo = hashlib.sha256()
    for name in sorted(files):
        combo.update(name.encode())
        combo.update(files[name].encode())
    require(manifest.get("dataset_sha256") == combo.hexdigest(), "dataset_sha256 mismatch")
    return {"ok": True, "files": files}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    data_dir = args.root / "data/queue_events_v8"
    verify_data(data_dir)
    if (args.root / f"runs/{RUN_ID}/manifest.json").is_file():
        print(json.dumps(verify_run_seal(args.root), indent=2))
    else:
        print(json.dumps({"ok": True, "stage": "data_only"}, indent=2))


if __name__ == "__main__":
    main()