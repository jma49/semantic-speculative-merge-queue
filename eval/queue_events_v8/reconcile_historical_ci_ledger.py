#!/usr/bin/env python3
"""Offline ledger for v8_historical_ci selection (does not rewrite sealed artifacts)."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HIST_SPEC = ROOT / "eval/historical_ci/sample_spec.json"
HIST_RAW = ROOT / "data/historical_ci/raw_records.json"


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def reconcile(raw: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    t0, t1 = spec["inclusion"]["merge_group_workflow_runs"]["created_at_utc_inclusive"]
    window_start, window_end = _parse_ts(t0), _parse_ts(t1)
    pr_lo, pr_hi = spec["inclusion"]["pull_requests"]["number_inclusive"]
    # Re-fetch semantics: we only have selected runs in raw; use pagination total for scanned count.
    scanned = raw["counts"]["merge_group_runs_scanned"]
    caveats = raw.get("selection_caveats") or []
    caveat_counts = Counter(c.split(":")[0] for c in caveats)

    excluded_pr = [c for c in caveats if c.startswith("excluded_pr_outside_window")]
    pr_numbers_excluded = [int(c.split(":")[-1]) for c in excluded_pr]

    selected = raw.get("merge_group_runs") or []

    discordant_cases: List[Dict[str, Any]] = []
    for run in selected:
        pr_num = run["queue_identity"]["pr_number"]
        pull = (raw.get("pull_requests") or {}).get(str(pr_num))
        if not pull:
            continue
        mg_sha = run.get("head_sha")
        fetch_sha = pull.get("head_sha")
        if mg_sha and fetch_sha and mg_sha != fetch_sha:
            discordant_cases.append(
                {
                    "pr_number": pr_num,
                    "workflow_run_id": run["workflow_run_id"],
                    "merge_group_head_sha": mg_sha,
                    "pr_head_sha_at_fetch": fetch_sha,
                    "queue_base_parent_sha": run["queue_identity"].get("queue_base_parent_sha"),
                }
            )

    errata: List[str] = [
        (
            f"merge_group_runs_scanned={scanned} counts all REST pages for event=merge_group "
            f"before client-side time filter; it is not the in-window total."
        ),
        (
            f"merge_group_runs_selected={len(selected)} requires created_at in "
            f"[{t0}, {t1}], parseable queue branch, and PR in [{pr_lo}, {pr_hi}]."
        ),
        (
            f"selection_caveats excluded_pr_outside_window={len(excluded_pr)} are runs inside the "
            f"time window whose parsed PR number falls outside [{pr_lo}, {pr_hi}] "
            f"(not calendar out-of-range)."
        ),
        (
            "pr_head_sha_at_fetch in historical_ci is REST pulls/{n}.head.sha at collection time; "
            "it may differ from the SHA tested by merge_group for the same episode."
        ),
        (
            "failure_attribution_assay integration_regression labels require comparable heads; "
            "see queue_events_v8 alignment for discordant-head guardrails."
        ),
    ]

    n_discordant = len(discordant_cases)
    n_sel = len(selected)
    if n_sel and n_discordant == n_sel:
        errata.append(
            f"ERRATUM: all {n_sel} selected merge_group runs have merge_group_head_sha != "
            f"pr_head_sha_at_fetch; integration_regression_assay cases must not be read as "
            f"independently attributable semantic regressions without enqueue-time head alignment."
        )

    return {
        "schema_version": "1.0",
        "historical_ci_run_id": "v8_historical_ci",
        "historical_ci_spec_path": str(HIST_SPEC.relative_to(ROOT)),
        "ledger": {
            "merge_group_runs_scanned_api_total": scanned,
            "merge_group_runs_selected": len(selected),
            "selection_caveat_total": len(caveats),
            "caveat_prefix_counts": dict(caveat_counts),
            "excluded_pr_outside_number_window": {
                "count": len(excluded_pr),
                "pr_numbers": pr_numbers_excluded,
                "workflow_run_ids": [c.split(":")[1] for c in excluded_pr],
            },
            "in_window_not_in_selected_approximation": (
                "scanned_total_minus_selected_minus_excluded_pr_caveats is dominated by "
                "runs outside the calendar window (not logged per-run in raw_records)."
            ),
        },
        "errata": errata,
        "discordant_head_cases": discordant_cases,
        "seal_preservation": {
            "runs/v8_historical_ci/manifest.json": "unchanged",
            "data/historical_ci/raw_records.json": "unchanged",
            "note": "This ledger is additive evidence only.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data/queue_events_v8/historical_ci_selection_ledger.json")
    args = parser.parse_args()
    spec = json.loads(HIST_SPEC.read_text(encoding="utf-8"))
    raw = json.loads(HIST_RAW.read_text(encoding="utf-8"))
    out = reconcile(raw, spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    errata_path = args.output.parent / "historical_ci_errata.json"
    errata_path.write_text(
        json.dumps({"errata": out["errata"], "discordant_head_count": len(out["discordant_head_cases"])}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ledger_path": str(args.output), "discordant_heads": len(out["discordant_head_cases"])}, indent=2))


if __name__ == "__main__":
    main()