#!/usr/bin/env python3
"""Deterministic descriptive output and failure-attribution assay from frozen raw CI records."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = Path(__file__).resolve().parent / "sample_spec.json"
OBS_PATH = Path(__file__).resolve().parent / "field_observability.json"

CHECK_NAME_KEY = re.compile(r"[^a-z0-9]+")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canon_name(name: Optional[str]) -> str:
    if not name:
        return ""
    return CHECK_NAME_KEY.sub("_", name.lower()).strip("_")


def _duration_min(start: Optional[str], end: Optional[str]) -> Optional[float]:
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return (e - s).total_seconds() / 60.0
    except ValueError:
        return None


def _index_checks(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = _canon_name(row.get("name"))
        if not key:
            continue
        out[key] = row
    return out


def attribution_assay(
    merge_runs: List[Dict[str, Any]],
    pulls: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare PR-head checks vs merge-group-head checks (independent workflow invocations)."""
    cases: List[Dict[str, Any]] = []
    for run in merge_runs:
        if run.get("conclusion") != "failure":
            continue
        pr_num = run["queue_identity"]["pr_number"]
        pull = pulls.get(str(pr_num))
        if not pull:
            continue
        mg = _index_checks(run.get("check_runs") or [])
        pr = _index_checks(pull.get("check_runs_at_fetch") or [])
        shared = sorted(set(mg) & set(pr))
        failing_only_queue: List[str] = []
        integration_regressions: List[Dict[str, Any]] = []
        for key in shared:
            mc = mg[key].get("conclusion")
            pc = pr[key].get("conclusion")
            if mc == "failure" and pc == "success":
                integration_regressions.append(
                    {
                        "check_key": key,
                        "check_name": mg[key].get("name"),
                        "pr_head_conclusion": pc,
                        "merge_group_conclusion": mc,
                    }
                )
        for key, row in mg.items():
            if row.get("conclusion") == "failure" and key not in pr:
                failing_only_queue.append(row.get("name") or key)
        cases.append(
            {
                "workflow_run_id": run["workflow_run_id"],
                "pr_number": pr_num,
                "merge_group_head_sha": run.get("head_sha"),
                "queue_base_parent_sha": run["queue_identity"].get("queue_base_parent_sha"),
                "pr_head_sha_at_fetch": pull.get("head_sha"),
                "shared_named_checks": len(shared),
                "integration_regression_labels": integration_regressions,
                "merge_group_failures_without_pr_head_analog": failing_only_queue,
                "independent_label_basis": (
                    "success on PR-head check run vs failure on merge-group check run "
                    "for the same normalized name (different SHAs/commits)."
                ),
            }
        )
    label_counts = Counter()
    for case in cases:
        if case["integration_regression_labels"]:
            label_counts["integration_regression_at_least_one_check"] += 1
        elif case["merge_group_failures_without_pr_head_analog"]:
            label_counts["queue_only_failure_signature"] += 1
        else:
            label_counts["ambiguous_shared_checks"] += 1
    return {
        "failed_merge_group_runs": len(cases),
        "label_histogram": dict(label_counts),
        "cases": cases,
        "limits": [
            "PR-head checks are sampled at fetch time, not guaranteed to be the pre-queue push.",
            "No authoritative queue-internal culprit label exists in the public API.",
        ],
    }


def summarize(raw: Dict[str, Any]) -> Dict[str, Any]:
    runs = raw.get("merge_group_runs") or []
    pulls = raw.get("pull_requests") or {}
    conclusions = Counter(r.get("conclusion") for r in runs)
    states = Counter((p.get("state"), p.get("merged")) for p in pulls.values())
    closed_unmerged = sum(
        1 for p in pulls.values() if p.get("state") == "closed" and not p.get("merged")
    )
    durations = [
        d
        for r in runs
        for d in [_duration_min(r.get("run_started_at") or r.get("created_at"), r.get("updated_at"))]
        if d is not None
    ]
    durations.sort()

    def pct(q: float) -> Optional[float]:
        if not durations:
            return None
        pos = (len(durations) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(durations) - 1)
        return durations[lo] + (pos - lo) * (durations[hi] - durations[lo])

    attribution = attribution_assay(runs, pulls)
    observability = json.loads(OBS_PATH.read_text(encoding="utf-8"))
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": raw.get("repository"),
        "window": raw.get("inclusion_window"),
        "counts": raw.get("counts"),
        "merge_group_conclusions": dict(conclusions),
        "pull_request_states": {f"state={s[0]} merged={s[1]}": n for s, n in states.items()},
        "closed_unmerged_pulls": closed_unmerged,
        "merge_group_duration_minutes": {
            "mean": sum(durations) / len(durations) if durations else None,
            "p50": pct(0.5),
            "p95": pct(0.95),
            "n": len(durations),
        },
        "queue_identity_coverage": {
            "runs_with_parseable_branch": len(runs),
            "unique_pr_numbers_in_queue_runs": len({r["queue_identity"]["pr_number"] for r in runs}),
        },
        "observability": observability,
        "failure_attribution_assay": attribution,
        "selection_caveat_count": len(raw.get("selection_caveats") or []),
        "fetch_failure_count": len(raw.get("fetch_failures") or []),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "historical_ci")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    raw_path = args.data_dir / "raw_records.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    summary = summarize(raw)
    out = args.output or (args.data_dir / "descriptive_summary.json")
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"summary_path": str(out), "sha256": _sha256_file(out)}, indent=2))


if __name__ == "__main__":
    main()