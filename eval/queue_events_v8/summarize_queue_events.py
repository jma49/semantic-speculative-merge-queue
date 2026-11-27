#!/usr/bin/env python3
"""Derive queue episode coverage, outcomes, and observer metrics from raw_records."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.queue_events_v8.episode_align import align_merge_group_runs, build_episodes  # noqa: E402
from eval.queue_events_v8.raw_event_counts import (  # noqa: E402
    calendar_days_inclusive,
    classify_fetch_failures,
    count_raw_timeline_events,
)

OBS_PATH = Path(__file__).resolve().parent / "field_observability.json"
API_GAPS = Path(__file__).resolve().parent / "api_gaps.json"
SUMMARY_SCHEMA_VERSION = "1.2"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def summarize_repo(
    repo_blob: Dict[str, Any],
    week_start: datetime,
    week_end: datetime,
) -> Dict[str, Any]:
    pulls = repo_blob.get("pull_requests") or {}
    with_queue = 0
    episode_rows: List[Dict[str, Any]] = []
    reason_hist: Counter[str] = Counter()
    outcome_hist: Counter[str] = Counter()

    runs_by_pr: Dict[int, List[Dict[str, Any]]] = {}
    for run in repo_blob.get("merge_group_runs_in_week") or []:
        num = run["queue_identity"]["pr_number"]
        runs_by_pr.setdefault(num, []).append(run)

    crosswalk_discordant = 0
    crosswalk_time_ambiguous = 0

    for num_str, pull in pulls.items():
        nodes = pull.get("timeline_nodes") or []
        has_queue = any("MergeQueue" in (n.get("__typename") or "") for n in nodes)
        if has_queue:
            with_queue += 1
        ep_blob = build_episodes(
            nodes,
            week_start=week_start,
            week_end=week_end,
            head_ref_oid_at_fetch=pull.get("headRefOid"),
        )
        for ep in ep_blob["episodes_in_observation_week"]:
            if ep.get("reason"):
                reason_hist[ep["reason"]] += 1
            outcome_hist[ep.get("outcome_class") or "unknown"] += 1

        align = align_merge_group_runs(
            ep_blob["episodes"],
            runs_by_pr.get(int(num_str), []),
            pull.get("headRefOid"),
        )
        crosswalk_discordant += align["discordant_head_count"]
        crosswalk_time_ambiguous += align["time_ambiguous_run_count"]

        episode_rows.append(
            {
                "pr_number": int(num_str),
                "head_ref_oid_at_fetch": pull.get("headRefOid"),
                "state_at_fetch": pull.get("state"),
                "merged": pull.get("merged"),
                **ep_blob,
                "merge_group_alignment": align,
            }
        )

    fetch_classes = classify_fetch_failures(repo_blob)
    raw_counts = count_raw_timeline_events(repo_blob, week_start, week_end)
    pulls_with_overlap = sum(1 for r in episode_rows if r.get("episode_count_in_week", 0) > 0)

    return {
        "enumerated_pr_count": repo_blob.get("enumerated_pr_count"),
        "pull_requests_fetched": repo_blob.get("pull_requests_fetched"),
        "draft_excluded": len(repo_blob.get("draft_excluded") or []),
        "pr_fetch_completeness": {
            **fetch_classes,
            "enumerated_minus_not_found_minus_draft_minus_fetched": (
                repo_blob.get("enumerated_pr_count", 0)
                - fetch_classes["pr_not_found_count"]
                - len(repo_blob.get("draft_excluded") or [])
                - repo_blob.get("pull_requests_fetched", 0)
            ),
        },
        "pulls_with_any_queue_timeline_event": with_queue,
        "pulls_with_episode_overlapping_observation_week": pulls_with_overlap,
        "raw_timeline_event_counts": raw_counts,
        "removal_reason_histogram": dict(reason_hist),
        "outcome_class_histogram": dict(outcome_hist),
        "merge_group_crosswalk": {
            "discordant_head_count": crosswalk_discordant,
            "attribution_eligible_run_count": 0,
            "time_ambiguous_run_count": crosswalk_time_ambiguous,
            "note": "merge_group head_sha is not comparable to headRefOid at fetch; attribution_eligible is always false.",
        },
        "per_pull": episode_rows,
    }


def summarize(raw: Dict[str, Any], *, data_dir: Path) -> Dict[str, Any]:
    t0, t1 = raw["observation_week"]["start"], raw["observation_week"]["end"]
    week_start, week_end = _parse_ts(t0), _parse_ts(t1)
    repos: Dict[str, Any] = {}
    for key, blob in (raw.get("repositories") or {}).items():
        repos[key] = summarize_repo(blob, week_start, week_end)

    observability = json.loads(OBS_PATH.read_text(encoding="utf-8"))
    api_gaps = json.loads(API_GAPS.read_text(encoding="utf-8"))
    coverage = {
        key: {
            "distinct_prs_with_episode_overlap_over_fetched": (
                repos[key]["pulls_with_episode_overlapping_observation_week"] / repos[key]["pull_requests_fetched"]
                if repos[key]["pull_requests_fetched"]
                else 0.0
            ),
            "queue_timeline_any_ratio": (
                repos[key]["pulls_with_any_queue_timeline_event"] / repos[key]["pull_requests_fetched"]
                if repos[key]["pull_requests_fetched"]
                else 0.0
            ),
            "distinct_prs_with_enqueue_in_week": repos[key]["raw_timeline_event_counts"][
                "distinct_prs_with_enqueue_in_week"
            ],
            "queue_admissions_in_week": repos[key]["raw_timeline_event_counts"]["added_to_merge_queue_in_week"],
        }
        for key in repos
    }

    superseded_path = data_dir / "descriptive_summary_schema_1_0_superseded.json"
    supersession: Dict[str, Any] = {
        "superseded_schema_version": "1.0",
        "reason": (
            "Flawed censoring labels, window overlap logic, comparable_hash_retry_episodes, "
            "and fetch failure bucketing."
        ),
    }
    if superseded_path.is_file():
        supersession["superseded_artifact_path"] = str(superseded_path.relative_to(ROOT))
        supersession["superseded_sha256"] = _sha256_file(superseded_path)

    combined = {
        "distinct_prs_with_enqueue_in_week": sum(
            coverage[k]["distinct_prs_with_enqueue_in_week"] for k in coverage
        ),
        "queue_admissions_in_week": sum(coverage[k]["queue_admissions_in_week"] for k in coverage),
    }

    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "supersedes": supersession,
        "observation_week": raw["observation_week"],
        "observation_calendar_days_inclusive": calendar_days_inclusive(week_start, week_end),
        "repositories": repos,
        "combined_repositories": combined,
        "observer_coverage": coverage,
        "observability": observability,
        "api_gaps_reference": "eval/queue_events_v8/api_gaps.json",
        "api_gaps_summary": api_gaps,
        "raw_records_sha256": _sha256_file(data_dir / "raw_records.json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "queue_events_v8")
    args = parser.parse_args()
    raw_path = args.data_dir / "raw_records.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    summary = summarize(raw, data_dir=args.data_dir)
    out = args.data_dir / "descriptive_summary.json"
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"summary_path": str(out), "sha256": _sha256_file(out)}, indent=2))


if __name__ == "__main__":
    main()