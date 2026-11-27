#!/usr/bin/env python3
"""Fetch frozen-window merge queue CI evidence from GitHub (API metadata only)."""
from __future__ import annotations

import argparse
import sys
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from eval.historical_ci._github_http import paginate, request_json

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SPEC_PATH = Path(__file__).resolve().parent / "sample_spec.json"


def _parse_ts(value: str) -> datetime:
    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def load_spec(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compile_branch_regex(spec: Dict[str, Any]) -> re.Pattern[str]:
    return re.compile(spec["parsed_identities"]["merge_group_branch_regex"])


def parse_queue_branch(branch: str, pattern: re.Pattern[str]) -> Optional[Dict[str, Any]]:
    m = pattern.search(branch)
    if not m:
        return None
    return {
        "pr_number": int(m.group("pr_number")),
        "queue_base_parent_sha": m.group("base_parent_sha"),
        "branch": branch,
    }


def in_window(ts: str, start: datetime, end: datetime) -> bool:
    return start <= _parse_ts(ts) <= end


def normalize_check(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "conclusion": row.get("conclusion"),
        "status": row.get("status"),
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "details_url": row.get("details_url"),
        "app_slug": (row.get("app") or {}).get("slug"),
    }


def fetch_check_runs(owner: str, repo: str, sha: str, max_pages: int, per_page: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    path = f"repos/{owner}/{repo}/commits/{sha}/check-runs"
    rows, pages = paginate(
        path,
        query={"per_page": str(per_page)},
        item_key="check_runs",
        max_pages=max_pages,
    )
    return [normalize_check(r) for r in rows], pages


def fetch_pull(owner: str, repo: str, number: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        body, _ = request_json(f"repos/{owner}/{repo}/pulls/{number}")
    except Exception as exc:  # noqa: BLE001 — log and continue per PR
        return None, str(exc)
    return {
        "number": body["number"],
        "state": body["state"],
        "merged": body.get("merged"),
        "draft": body.get("draft"),
        "title": body.get("title"),
        "created_at": body.get("created_at"),
        "updated_at": body.get("updated_at"),
        "closed_at": body.get("closed_at"),
        "merged_at": body.get("merged_at"),
        "head_sha": body["head"]["sha"],
        "head_ref": body["head"]["ref"],
        "base_ref": body["base"]["ref"],
        "base_sha": body["base"]["sha"],
        "merge_commit_sha": body.get("merge_commit_sha"),
        "html_url": body.get("html_url"),
    }, None


def collect(spec: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    owner = spec["repository"]["owner"]
    repo = spec["repository"]["name"]
    branch_re = compile_branch_regex(spec)
    t0, t1 = spec["inclusion"]["merge_group_workflow_runs"]["created_at_utc_inclusive"]
    window_start, window_end = _parse_ts(t0), _parse_ts(t1)
    pr_lo, pr_hi = spec["inclusion"]["pull_requests"]["number_inclusive"]
    max_pages = spec["inclusion"]["check_runs_per_record"]["max_pages_per_commit"]
    per_page = spec["inclusion"]["check_runs_per_record"]["per_page"]

    caveats: List[str] = []
    failures: List[Dict[str, Any]] = []
    merge_group_pages: List[Dict[str, Any]] = []

    raw_runs, run_pages = paginate(
        f"repos/{owner}/{repo}/actions/runs",
        query={"event": "merge_group", "per_page": "100"},
        item_key="workflow_runs",
        max_pages=None,
    )
    merge_group_pages.extend(run_pages)

    selected_runs: List[Dict[str, Any]] = []
    for run in raw_runs:
        created = run.get("created_at")
        if not created or not in_window(created, window_start, window_end):
            continue
        ident = parse_queue_branch(run.get("head_branch") or "", branch_re)
        if ident is None:
            caveats.append(f"excluded_unparseable_branch:{run.get('id')}:{run.get('head_branch')}")
            continue
        if ident["pr_number"] < pr_lo or ident["pr_number"] > pr_hi:
            caveats.append(f"excluded_pr_outside_window:{run['id']}:{ident['pr_number']}")
            continue
        selected_runs.append(
            {
                "workflow_run_id": run["id"],
                "event": run.get("event"),
                "conclusion": run.get("conclusion"),
                "status": run.get("status"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
                "run_started_at": run.get("run_started_at"),
                "run_attempt": run.get("run_attempt"),
                "head_sha": run.get("head_sha"),
                "head_branch": run.get("head_branch"),
                "html_url": run.get("html_url"),
                "queue_identity": ident,
            }
        )

    selected_runs.sort(key=lambda r: (r["created_at"], r["workflow_run_id"]))

    pr_numbers_from_runs = {r["queue_identity"]["pr_number"] for r in selected_runs}
    pulls: Dict[str, Any] = {}
    pull_errors: Dict[str, str] = {}
    closed_list, closed_pages = paginate(
        f"repos/{owner}/{repo}/pulls",
        query={"state": "closed", "sort": "updated", "direction": "desc", "per_page": "100"},
        item_key=None,
        max_pages=30,
    )
    merge_group_pages.extend([{**p, "list": "closed_pulls"} for p in closed_pages])
    closed_in_range: List[int] = []
    for row in closed_list:
        num = row.get("number")
        if not isinstance(num, int):
            continue
        if num < pr_lo:
            break
        if num > pr_hi:
            continue
        closed_in_range.append(num)

    pr_numbers = sorted(set(pr_numbers_from_runs) | set(closed_in_range))
    for num in pr_numbers:
        row, err = fetch_pull(owner, repo, num)
        if err:
            pull_errors[str(num)] = err
            failures.append({"stage": "pull", "pr_number": num, "error": err})
            continue
        if row is None:
            continue
        if row.get("draft"):
            caveats.append(f"excluded_draft_pr:{num}")
            continue
        pulls[str(num)] = row

    check_logs: Dict[str, List[Dict[str, Any]]] = {"merge_group_heads": {}, "pr_heads": {}}
    for run in selected_runs:
        sha = run["head_sha"]
        checks, pages = fetch_check_runs(owner, repo, sha, max_pages, per_page)
        check_logs["merge_group_heads"][str(run["workflow_run_id"])] = pages
        run["check_runs"] = checks
        run["check_run_count"] = len(checks)
        if len(checks) >= max_pages * per_page:
            caveats.append(f"check_runs_maybe_truncated:merge_group:{run['workflow_run_id']}")

    for num_str, pull in pulls.items():
        sha = pull["head_sha"]
        checks, pages = fetch_check_runs(owner, repo, sha, max_pages, per_page)
        check_logs["pr_heads"][num_str] = pages
        pull["check_runs_at_fetch"] = checks
        pull["check_run_count_at_fetch"] = len(checks)
        if len(checks) >= max_pages * per_page:
            caveats.append(f"check_runs_maybe_truncated:pr_head:{num_str}")

    out_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "spec_sha256": _sha256_file(SPEC_PATH),
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": spec["repository"],
        "inclusion_window": {"start": t0, "end": t1},
        "pr_number_window": [pr_lo, pr_hi],
        "pagination": {
            "merge_group_workflow_runs": merge_group_pages,
            "check_runs": check_logs,
        },
        "selection_caveats": caveats,
        "fetch_failures": failures,
        "merge_group_runs": selected_runs,
        "pull_requests": pulls,
        "pull_fetch_errors": pull_errors,
        "counts": {
            "merge_group_runs_scanned": len(raw_runs),
            "merge_group_runs_selected": len(selected_runs),
            "pull_requests_stored": len(pulls),
            "pull_requests_with_queue_run": len(pr_numbers_from_runs),
        },
    }
    raw_path = out_dir / "raw_records.json"
    raw_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    provenance = {
        "raw_records_sha256": _sha256_file(raw_path),
        "spec_path": str(SPEC_PATH.relative_to(ROOT)),
        "spec_sha256": raw["spec_sha256"],
        "caveat_count": len(caveats),
        "failure_count": len(failures),
    }
    (out_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"raw_path": str(raw_path), **provenance, **raw["counts"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "data" / "historical_ci",
    )
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    args = parser.parse_args()
    spec = load_spec(args.spec)
    summary = collect(spec, args.data_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()