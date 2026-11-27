#!/usr/bin/env python3
"""Enumerate frozen PR universe and fetch GraphQL merge-queue timeline events."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.historical_ci._github_http import paginate  # noqa: E402
from eval.queue_events_v8._github_graphql import fetch_pr_timeline  # noqa: E402
from eval.queue_events_v8.raw_event_counts import is_not_found_graphql_error  # noqa: E402

SPEC_PATH = Path(__file__).resolve().parent / "sample_spec.json"
EVAL_DIR = Path(__file__).resolve().parent


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_spec(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def enumerate_pr_numbers(lo: int, hi: int) -> List[int]:
    return list(range(lo, hi + 1))


def fetch_merge_group_runs(
    owner: str,
    repo: str,
    branch_re: re.Pattern[str],
    week_start: datetime,
    week_end: datetime,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    raw, pages = paginate(
        f"repos/{owner}/{repo}/actions/runs",
        query={"event": "merge_group", "per_page": "100"},
        item_key="workflow_runs",
        max_pages=None,
    )
    selected: List[Dict[str, Any]] = []
    for run in raw:
        created = run.get("created_at")
        if not created or not (week_start <= _parse_ts(created) <= week_end):
            continue
        branch = run.get("head_branch") or ""
        m = branch_re.search(branch)
        if not m:
            continue
        selected.append(
            {
                "workflow_run_id": run["id"],
                "created_at": created,
                "conclusion": run.get("conclusion"),
                "head_sha": run.get("head_sha"),
                "head_branch": branch,
                "queue_identity": {
                    "pr_number": int(m.group("pr_number")),
                    "queue_base_parent_sha": m.group("base_parent_sha"),
                },
            }
        )
    selected.sort(key=lambda r: (r["created_at"], r["workflow_run_id"]))
    return selected, pages


def collect_repository(
    repo_spec: Dict[str, Any],
    pr_cfg: Dict[str, Any],
    week_start: datetime,
    week_end: datetime,
) -> Dict[str, Any]:
    owner, name = repo_spec["owner"], repo_spec["name"]
    lo, hi = pr_cfg["number_inclusive"]
    numbers = enumerate_pr_numbers(lo, hi)
    branch_re = re.compile(repo_spec["queue_branch_regex"])

    pulls: Dict[str, Any] = {}
    missing: List[int] = []
    not_found: List[int] = []
    drafts: List[int] = []
    pagination_log: Dict[str, List[Dict[str, Any]]] = {}
    failures: List[Dict[str, Any]] = []

    for n in numbers:
        try:
            pull, nodes, pages = fetch_pr_timeline(owner, name, n)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            if is_not_found_graphql_error(err):
                not_found.append(n)
            else:
                failures.append({"pr_number": n, "error": err, "failure_class": "transport_or_unclassified"})
            continue
        pagination_log[str(n)] = pages
        if pull is None:
            missing.append(n)
            continue
        if pull.get("isDraft") and not pr_cfg.get("include_drafts"):
            drafts.append(n)
            continue
        pulls[str(n)] = {
            **pull,
            "timeline_nodes": nodes,
        }

    merge_runs, mg_pages = fetch_merge_group_runs(owner, name, branch_re, week_start, week_end)

    return {
        "repository": repo_spec,
        "pr_number_window": [lo, hi],
        "enumerated_pr_count": len(numbers),
        "pull_requests_fetched": len(pulls),
        "missing_pull_requests": missing,
        "pr_not_found_numbers": not_found,
        "draft_excluded": drafts,
        "pull_requests": pulls,
        "timeline_pagination": pagination_log,
        "fetch_failures": failures,
        "merge_group_runs_in_week": merge_runs,
        "merge_group_pagination": mg_pages,
    }


def collect(spec: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    t0, t1 = spec["observation_week_utc_inclusive"]
    week_start, week_end = _parse_ts(t0), _parse_ts(t1)
    repos_out: Dict[str, Any] = {}

    repo_map = {r["key"]: r for r in spec["repositories"]}
    for key, pr_cfg in spec["pr_universe"]["per_repository"].items():
        repos_out[key] = collect_repository(repo_map[key], pr_cfg, week_start, week_end)

    out_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "spec_sha256": _sha256_file(SPEC_PATH),
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "observation_week": {"start": t0, "end": t1},
        "repositories": repos_out,
        "spec_reference": str(SPEC_PATH.relative_to(ROOT)),
    }
    raw_path = out_dir / "raw_records.json"
    raw_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    provenance = {
        "raw_records_sha256": _sha256_file(raw_path),
        "spec_sha256": raw["spec_sha256"],
        "spec_path": str(SPEC_PATH.relative_to(ROOT)),
    }
    (out_dir / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"raw_path": str(raw_path), **provenance}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "queue_events_v8")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    args = parser.parse_args()
    spec = load_spec(args.spec)
    print(json.dumps(collect(spec, args.data_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()