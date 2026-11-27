import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from eval.queue_events_v8.episode_align import align_merge_group_runs, build_episodes, interval_overlaps_observation_week
from eval.queue_events_v8.raw_event_counts import classify_fetch_failures, is_not_found_graphql_error

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "eval/queue_events_v8/fixtures/minimal_timeline.json"


class QueueEventsV8Tests(unittest.TestCase):
    def setUp(self) -> None:
        blob = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.week_start = datetime.fromisoformat(blob["week_start"].replace("Z", "+00:00"))
        self.week_end = datetime.fromisoformat(blob["week_end"].replace("Z", "+00:00"))
        self.nodes = blob["nodes"]

    def test_interval_overlap_not_point_or(self) -> None:
        """Episode spanning window boundaries overlaps even if neither endpoint is inside."""
        self.assertTrue(
            interval_overlaps_observation_week(
                "2026-11-09T12:00:00Z",
                "2026-11-18T01:00:00Z",
                self.week_start,
                self.week_end,
            )
        )
        self.assertFalse(
            interval_overlaps_observation_week(
                "2026-11-18T00:01:00Z",
                "2026-11-19T00:00:00Z",
                self.week_start,
                self.week_end,
            )
        )

    def test_censoring_labels_and_comparable_unknown(self) -> None:
        out = build_episodes(
            self.nodes,
            week_start=self.week_start,
            week_end=self.week_end,
            head_ref_oid_at_fetch="abc123",
        )
        classes = {e["outcome_class"] for e in out["episodes"]}
        self.assertIn("left_censored_removed_without_added", classes)
        self.assertIn("truncated_prior_enqueue_superseded", classes)
        self.assertIn("right_censored_episode_open", classes)
        self.assertEqual(out["comparable_hash_retry_status"], "unknown")
        self.assertIsNone(out["comparable_hash_retry_episodes"])

    def test_merge_group_alignment_within_interval_only(self) -> None:
        episodes = build_episodes(
            self.nodes,
            week_start=self.week_start,
            week_end=self.week_end,
            head_ref_oid_at_fetch="abc123",
        )["episodes"]
        runs = [
            {
                "workflow_run_id": 1,
                "created_at": "2026-11-11T10:00:00Z",
                "head_sha": "merge1",
                "queue_identity": {"pr_number": 1, "queue_base_parent_sha": "p" * 40},
            },
            {
                "workflow_run_id": 2,
                "created_at": "2026-11-09T10:00:00Z",
                "head_sha": "merge2",
                "queue_identity": {"pr_number": 1, "queue_base_parent_sha": "p" * 40},
            },
        ]
        align = align_merge_group_runs(episodes, runs, "abc123")
        matched = [r for r in align["aligned_runs"] if r.get("nearest_episode")]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["workflow_run_id"], 1)
        self.assertEqual(align["attribution_eligible_run_count"], 0)

    def test_not_found_classifier(self) -> None:
        err = '[{"type": "NOT_FOUND", "message": "Could not resolve to a PullRequest with the number of 1."}]'
        self.assertTrue(is_not_found_graphql_error(err))
        classes = classify_fetch_failures(
            {
                "fetch_failures": [
                    {"pr_number": 1, "error": err},
                    {"pr_number": 2, "error": "GraphQL request failed after retries"},
                ],
                "missing_pull_requests": [],
            }
        )
        self.assertEqual(classes["pr_not_found_count"], 1)
        self.assertEqual(classes["transport_failure_count"], 1)

    def test_frozen_raw_regression_counts(self) -> None:
        raw_path = ROOT / "data/queue_events_v8/raw_records.json"
        if not raw_path.is_file():
            self.skipTest("frozen raw_records not present")
        summary = json.loads((ROOT / "data/queue_events_v8/descriptive_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["schema_version"], "1.2")
        comb = summary["combined_repositories"]
        self.assertEqual(comb["distinct_prs_with_enqueue_in_week"], 83)
        self.assertEqual(comb["queue_admissions_in_week"], 100)
        self.assertEqual(len(summary["observation_calendar_days_inclusive"]), 8)


if __name__ == "__main__":
    unittest.main()