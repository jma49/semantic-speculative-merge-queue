"""Recompute merge-queue timeline event totals directly from frozen raw_records."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Set

from eval.queue_events_v8.episode_align import in_observation_week, _parse_ts


def calendar_days_inclusive(week_start: datetime, week_end: datetime) -> List[str]:
    days: List[str] = []
    d = week_start.date()
    end_d = week_end.date()
    while d <= end_d:
        days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def is_not_found_graphql_error(error_text: str) -> bool:
    return '"type": "NOT_FOUND"' in error_text or "Could not resolve to a PullRequest" in error_text


def classify_fetch_failures(repo_blob: Dict[str, Any]) -> Dict[str, Any]:
    not_found: List[int] = []
    transport: List[Dict[str, Any]] = []
    for row in repo_blob.get("fetch_failures") or []:
        err = row.get("error") or ""
        pr = int(row["pr_number"])
        if is_not_found_graphql_error(err):
            not_found.append(pr)
        else:
            transport.append({"pr_number": pr, "error": err})
    legacy_missing = [int(x) for x in repo_blob.get("missing_pull_requests") or []]
    not_found_nums = sorted(set(not_found) | set(legacy_missing))
    return {
        "pr_not_found_numbers": not_found_nums,
        "pr_not_found_count": len(not_found_nums),
        "transport_or_unclassified_failures": transport,
        "transport_failure_count": len(transport),
    }


def count_raw_timeline_events(
    repo_blob: Dict[str, Any],
    week_start: datetime,
    week_end: datetime,
) -> Dict[str, Any]:
    """Count ADDED/REMOVED timestamps in the observation window; distinct PRs vs admissions."""
    pulls = repo_blob.get("pull_requests") or {}
    added_in_week = 0
    removed_in_week = 0
    prs_with_added: Set[int] = set()
    prs_overlapping: Set[int] = set()

    for num_str, pull in pulls.items():
        num = int(num_str)
        nodes = pull.get("timeline_nodes") or []
        added_this_pr = 0
        removed_this_pr = 0
        for n in nodes:
            typ = n.get("__typename")
            created = n.get("createdAt")
            if not created:
                continue
            if typ == "AddedToMergeQueueEvent" and in_observation_week(created, week_start, week_end):
                added_in_week += 1
                added_this_pr += 1
            elif typ == "RemovedFromMergeQueueEvent" and in_observation_week(created, week_start, week_end):
                removed_in_week += 1
                removed_this_pr += 1
        if added_this_pr:
            prs_with_added.add(num)
        if added_this_pr or removed_this_pr:
            prs_overlapping.add(num)

    mg_runs = repo_blob.get("merge_group_runs_in_week") or []
    mg_prs = {int(r["queue_identity"]["pr_number"]) for r in mg_runs}

    return {
        "added_to_merge_queue_in_week": added_in_week,
        "removed_from_merge_queue_in_week": removed_in_week,
        "distinct_prs_with_enqueue_in_week": len(prs_with_added),
        "distinct_prs_with_queue_event_timestamp_in_week": len(prs_overlapping),
        "merge_group_workflow_runs_in_week": len(mg_runs),
        "distinct_prs_with_merge_group_run_in_week": len(mg_prs),
        "admissions_vs_distinct_prs_note": (
            "enqueue counts (ADDED in week) can exceed distinct PRs when one PR enqueues multiple times."
        ),
    }