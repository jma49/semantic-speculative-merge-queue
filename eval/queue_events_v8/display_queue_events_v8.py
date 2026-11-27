#!/usr/bin/env python3
"""Deterministic text table for queue_events v8 metrics (no external deps)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    metrics = json.loads((ROOT / "runs/v8_queue_events/metrics.json").read_text(encoding="utf-8"))
    ledger = json.loads((ROOT / "data/queue_events_v8/historical_ci_selection_ledger.json").read_text(encoding="utf-8"))
    summary = json.loads((ROOT / "data/queue_events_v8/descriptive_summary.json").read_text(encoding="utf-8"))

    lines = [
        "v8 queue events — observation summary (schema 8.2)",
        "=" * 52,
        f"week: {summary['observation_week']['start']} .. {summary['observation_week']['end']}",
        f"calendar days (inclusive): {metrics.get('observation_calendar_days_inclusive')}",
        "",
        "microsoft/TypeScript",
        f"  PRs fetched (enumerated universe): {metrics['typescript_prs_fetched']}",
        f"  distinct PRs with enqueue in week: {metrics['typescript_distinct_prs_enqueue_in_week']}",
        f"  queue admissions (ADDED) in week: {metrics['typescript_queue_admissions_in_week']}",
        f"  PRs with an episode overlapping observation week: {metrics['typescript_prs_with_episode_overlapping_week']}",
        f"  removal reasons: {summary['repositories']['typescript'].get('removal_reason_histogram')}",
        f"  discordant merge_group heads:    {metrics['typescript_discordant_merge_group_heads']}",
        "",
        "rust-lang/cargo",
        f"  PRs fetched: {metrics['cargo_prs_fetched']}",
        f"  distinct PRs with enqueue in week: {metrics['cargo_distinct_prs_enqueue_in_week']}",
        f"  queue admissions (ADDED) in week: {metrics['cargo_queue_admissions_in_week']}",
        f"  PRs with an episode overlapping observation week: {metrics['cargo_prs_with_episode_overlapping_week']}",
        f"  removal reasons: {summary['repositories']['cargo'].get('removal_reason_histogram')}",
        "",
        "combined",
        f"  distinct PRs with enqueue: {metrics['combined_distinct_prs_enqueue_in_week']}",
        f"  queue admissions (ADDED): {metrics['combined_queue_admissions_in_week']}",
        "",
        "historical_ci v8 ledger (sealed artifacts unchanged)",
        f"  merge_group scanned (API total): {metrics['historical_ci_scanned']}",
        f"  merge_group selected:            {metrics['historical_ci_selected']}",
        f"  excluded PR# outside window:     {metrics['historical_ci_excluded_pr_number_window']}",
        f"  discordant head cases @ fetch:   {metrics['discordant_head_cases_historical_ci']}",
        "",
        "author erratum (bilingual): data/queue_events_v8/author_erratum_v8_1.json",
        "errata (historical_ci_selection_ledger):",
    ]
    for item in ledger.get("errata") or []:
        lines.append(f"  - {item}")
    text = "\n".join(lines) + "\n"
    out = ROOT / "data" / "queue_events_v8" / "display.txt"
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()