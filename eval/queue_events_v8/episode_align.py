"""Build queue episodes, censoring flags, and merge_group crosswalk from timeline nodes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def in_observation_week(ts: str, start: datetime, end: datetime) -> bool:
    return start <= _parse_ts(ts) <= end


def interval_overlaps_observation_week(
    added_at: Optional[str],
    removed_at: Optional[str],
    week_start: datetime,
    week_end: datetime,
) -> bool:
    """True when the half-open episode interval [added, removed) intersects the inclusive window."""
    if removed_at and not added_at:
        return in_observation_week(removed_at, week_start, week_end)
    if not added_at:
        return False
    start = _parse_ts(added_at)
    end = _parse_ts(removed_at) if removed_at else datetime.max.replace(tzinfo=timezone.utc)
    return start <= week_end and end >= week_start


def observation_boundary_flags(
    added_at: Optional[str],
    removed_at: Optional[str],
    week_start: datetime,
    week_end: datetime,
) -> List[str]:
    flags: List[str] = []
    if added_at and _parse_ts(added_at) < week_start:
        flags.append("enqueue_before_window_start")
    if removed_at and _parse_ts(removed_at) > week_end:
        flags.append("dequeue_after_window_end")
    elif added_at and removed_at is None and interval_overlaps_observation_week(
        added_at, None, week_start, week_end
    ):
        flags.append("episode_open_past_window_end")
    if (
        added_at
        and removed_at
        and in_observation_week(added_at, week_start, week_end)
        and in_observation_week(removed_at, week_start, week_end)
    ):
        flags.append("fully_inside_window")
    return flags


def build_episodes(
    nodes: List[Dict[str, Any]],
    *,
    week_start: datetime,
    week_end: datetime,
    head_ref_oid_at_fetch: Optional[str],
) -> Dict[str, Any]:
    """Pair ADDED/REMOVED events; label censoring per field_observability.json."""
    lifecycle: List[Dict[str, Any]] = []
    for n in nodes:
        kind = n.get("__typename")
        created = n.get("createdAt")
        if not kind or not created:
            continue
        row: Dict[str, Any] = {"type": kind, "created_at": created}
        if kind == "RemovedFromMergeQueueEvent":
            row["reason"] = n.get("reason")
        lifecycle.append(row)
    lifecycle.sort(key=lambda x: x["created_at"])

    episodes: List[Dict[str, Any]] = []
    open_added: Optional[Dict[str, Any]] = None
    left_censored = 0
    for ev in lifecycle:
        if ev["type"] == "AddedToMergeQueueEvent":
            if open_added is not None:
                episodes.append(
                    {
                        "added_at": open_added["created_at"],
                        "removed_at": None,
                        "reason": None,
                        "outcome_class": "truncated_prior_enqueue_superseded",
                        "in_observation_week": interval_overlaps_observation_week(
                            open_added["created_at"], None, week_start, week_end
                        ),
                        "observation_boundary_flags": observation_boundary_flags(
                            open_added["created_at"], None, week_start, week_end
                        ),
                    }
                )
            open_added = ev
        elif ev["type"] == "RemovedFromMergeQueueEvent":
            if open_added is None:
                left_censored += 1
                episodes.append(
                    {
                        "added_at": None,
                        "removed_at": ev["created_at"],
                        "reason": ev.get("reason"),
                        "outcome_class": "left_censored_removed_without_added",
                        "in_observation_week": interval_overlaps_observation_week(
                            None, ev["created_at"], week_start, week_end
                        ),
                        "observation_boundary_flags": observation_boundary_flags(
                            None, ev["created_at"], week_start, week_end
                        ),
                    }
                )
                continue
            reason = ev.get("reason")
            outcome = "merged" if reason == "merged" else "removed"
            if reason == "failed_checks":
                outcome = "failed_checks"
            added_at = open_added["created_at"]
            removed_at = ev["created_at"]
            episodes.append(
                {
                    "added_at": added_at,
                    "removed_at": removed_at,
                    "reason": reason,
                    "outcome_class": outcome,
                    "in_observation_week": interval_overlaps_observation_week(
                        added_at, removed_at, week_start, week_end
                    ),
                    "observation_boundary_flags": observation_boundary_flags(
                        added_at, removed_at, week_start, week_end
                    ),
                }
            )
            open_added = None
    if open_added is not None:
        episodes.append(
            {
                "added_at": open_added["created_at"],
                "removed_at": None,
                "reason": None,
                "outcome_class": "right_censored_episode_open",
                "in_observation_week": interval_overlaps_observation_week(
                    open_added["created_at"], None, week_start, week_end
                ),
                "observation_boundary_flags": observation_boundary_flags(
                    open_added["created_at"], None, week_start, week_end
                ),
            }
        )

    closes = [
        e
        for e in lifecycle
        if e["type"] == "ClosedEvent" and in_observation_week(e["created_at"], week_start, week_end)
    ]
    reopens = [
        e
        for e in lifecycle
        if e["type"] == "ReopenedEvent" and in_observation_week(e["created_at"], week_start, week_end)
    ]

    week_episodes = [e for e in episodes if e.get("in_observation_week")]

    return {
        "episodes": episodes,
        "episodes_in_observation_week": week_episodes,
        "episode_count": len(episodes),
        "episode_count_in_week": len(week_episodes),
        "left_censored_removals": left_censored,
        "close_events_in_week": len(closes),
        "reopen_events_in_week": len(reopens),
        "head_ref_oid_at_fetch": head_ref_oid_at_fetch,
        "comparable_hash_retry_episodes": None,
        "comparable_hash_retry_status": "unknown",
        "comparable_hash_note": (
            "Per-episode enqueue head OID is not exposed in public GraphQL (api_gaps.json). "
            "merge_group head_sha is a composite test commit and is not comparable to headRefOid at fetch; "
            "retry counts are not incremented from a single observed headRefOid."
        ),
    }


def _run_in_episode_interval(run_created: str, episode: Dict[str, Any]) -> bool:
    added = episode.get("added_at")
    if not added:
        return False
    rc = _parse_ts(run_created)
    if _parse_ts(added) > rc:
        return False
    removed = episode.get("removed_at")
    if removed and _parse_ts(removed) < rc:
        return False
    return True


def align_merge_group_runs(
    episodes: List[Dict[str, Any]],
    runs: List[Dict[str, Any]],
    head_at_fetch: Optional[str],
) -> Dict[str, Any]:
    """Crosswalk workflow runs to episodes when run created_at falls inside episode interval."""
    aligned: List[Dict[str, Any]] = []
    for run in runs:
        mg_sha = run.get("head_sha")
        branch_parent = (run.get("queue_identity") or {}).get("queue_base_parent_sha")
        run_created = run.get("created_at")
        candidates: List[Dict[str, Any]] = []
        if run_created:
            candidates = [ep for ep in episodes if _run_in_episode_interval(run_created, ep)]
        ambiguous = len(candidates) > 1
        nearest = candidates[-1] if candidates else None
        head_match_fetch = head_at_fetch == mg_sha if head_at_fetch and mg_sha else None
        aligned.append(
            {
                "workflow_run_id": run.get("workflow_run_id"),
                "merge_group_head_sha": mg_sha,
                "queue_base_parent_sha": branch_parent,
                "matching_episodes": candidates,
                "nearest_episode": nearest,
                "time_match_ambiguous": ambiguous,
                "head_sha_matches_pr_head_at_fetch": head_match_fetch,
                "attribution_eligible": False,
                "attribution_ineligible_reason": (
                    "enqueue-time head not observable; merge_group composite SHA not comparable to fetch headRefOid"
                ),
            }
        )
    discordant = sum(1 for a in aligned if a.get("head_sha_matches_pr_head_at_fetch") is False)
    unambiguous = sum(1 for a in aligned if a.get("nearest_episode") and not a.get("time_match_ambiguous"))
    return {
        "aligned_runs": aligned,
        "discordant_head_count": discordant,
        "attribution_eligible_run_count": 0,
        "time_aligned_run_count": unambiguous,
        "time_ambiguous_run_count": sum(1 for a in aligned if a.get("time_match_ambiguous")),
        "note": (
            "Discordant fetch heads are expected for merge_group runs. "
            "Time matches are limited to workflow created_at inside episode [added, removed] intervals."
        ),
    }