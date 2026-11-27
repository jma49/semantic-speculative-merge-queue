#!/usr/bin/env python3
"""Auditable, cross-verified fetcher for strictly consecutive merged GitHub PR traces (v6).

Queries GitHub GraphQL `search(type: ISSUE)` over an inclusive `merged:>=START` window
on each repository's default branch (`is:pr is:merged base:<branch>`), fetches ALL merged
PRs in the window (paginating cursor pages exhaustively), sorts deterministically by
`(mergedAt, number)`, selects the exact latest 200 strictly consecutive merged PRs,
paginates `files(first: 100, after: $cursor)` to retrieve complete file lists (zero
truncation up to GitHub's 3,000-file PR diff ceiling), records each PR's true
`mergeCommit.oid`, and cross-verifies completeness against GitHub's public merged-PR
index over the exact inclusive `[oldest_merged_at, newest_merged_at]` interval so that
`public_window_merged_count == 200` and `omitted_pr_count == 0` for every repository.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPOS: List[Tuple[str, str, str, int]] = [
    # (owner, name, default_branch, lookback_days)
    ("kubernetes", "kubernetes", "master", 30),
    ("microsoft", "TypeScript", "main", 45),
    ("vercel", "next.js", "canary", 25),
]

TARGET_PRS = 200

SEARCH_PRS_QUERY = """
query($q: String!, $cursor: String) {
  search(query: $q, type: ISSUE, first: 50, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        title
        createdAt
        mergedAt
        baseRefName
        mergeCommit { oid }
        additions
        deletions
        changedFiles
        files(first: 100) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes { path }
        }
      }
    }
  }
}
"""

PR_FILES_PAGE_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      files(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { path }
      }
    }
  }
}
"""

WINDOW_COUNT_QUERY = """
query($q: String!) {
  search(query: $q, type: ISSUE, first: 1) {
    issueCount
  }
}
"""


def run_graphql(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, val in variables.items():
        if val is None:
            continue
        if isinstance(val, int):
            cmd.extend(["-F", f"{key}={val}"])
        else:
            cmd.extend(["-f", f"{key}={val}"])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data["data"]


def fetch_all_pr_files(
    owner: str,
    name: str,
    number: int,
    initial_paths: List[str],
    initial_cursor: Optional[str],
    has_next: bool,
) -> Tuple[List[str], int]:
    paths = list(initial_paths)
    pages = 1
    cursor = initial_cursor
    while has_next and cursor:
        data = run_graphql(
            PR_FILES_PAGE_QUERY,
            {"owner": owner, "name": name, "number": number, "cursor": cursor},
        )
        fconn = data["repository"]["pullRequest"]["files"]
        for fn in fconn.get("nodes") or []:
            if fn and fn.get("path"):
                paths.append(fn["path"])
        pages += 1
        pinfo = fconn.get("pageInfo") or {}
        has_next = bool(pinfo.get("hasNextPage"))
        cursor = pinfo.get("endCursor")
    return paths, pages


def fetch_repo_exact_merged_prs(
    owner: str, name: str, branch: str, lookback_days: int, target: int = TARGET_PRS
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    # Pin an explicit upper bound slightly in the past to avoid mid-fetch race conditions
    upper_dt = (now - timedelta(minutes=2)).replace(microsecond=0)
    lower_dt = (upper_dt - timedelta(days=lookback_days)).replace(microsecond=0)
    upper_iso = upper_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    lower_iso = lower_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    q = f"repo:{owner}/{name} is:pr is:merged base:{branch} merged:{lower_iso}..{upper_iso}"
    raw_nodes: Dict[int, Dict[str, Any]] = {}
    cursor: Optional[str] = None
    pages_fetched = 0
    reported_issue_count = 0

    while True:
        data = run_graphql(SEARCH_PRS_QUERY, {"q": q, "cursor": cursor})
        search = data["search"]
        reported_issue_count = int(search["issueCount"])
        if reported_issue_count > 950:
            raise RuntimeError(
                f"Window too wide for {owner}/{name} ({reported_issue_count} PRs > 950); shrink lookback_days"
            )
        pages_fetched += 1
        for node in search.get("nodes") or []:
            if not node or not node.get("mergedAt") or node.get("baseRefName") != branch:
                continue
            raw_nodes[int(node["number"])] = node
        pinfo = search.get("pageInfo") or {}
        if not pinfo.get("hasNextPage"):
            break
        cursor = pinfo.get("endCursor")

    if len(raw_nodes) < target:
        raise RuntimeError(
            f"Only found {len(raw_nodes)} merged PRs for {owner}/{name} in {lookback_days}d (need {target})"
        )

    # Sort strictly chronologically by (mergedAt, number) and take the latest exact `target` PRs
    ordered = sorted(raw_nodes.values(), key=lambda pr: (pr["mergedAt"], int(pr["number"])))
    selected = ordered[-target:]

    # Boundary-tie guard: if the oldest selected PR shares exact mergedAt with an unselected earlier PR,
    # shift window forward by 1 second or assert uniqueness at the lower boundary!
    if len(ordered) > target and ordered[-target - 1]["mergedAt"] == selected[0]["mergedAt"]:
        raise RuntimeError(
            f"Lower-boundary timestamp tie in {owner}/{name} at {selected[0]['mergedAt']}; retry window"
        )

    oldest_iso = selected[0]["mergedAt"]
    newest_iso = selected[-1]["mergedAt"]
    verify_q = f"repo:{owner}/{name} is:pr is:merged base:{branch} merged:{oldest_iso}..{newest_iso}"
    verify_count = int(run_graphql(WINDOW_COUNT_QUERY, {"q": verify_q})["search"]["issueCount"])
    omitted_count = verify_count - len(selected)
    if omitted_count != 0:
        raise RuntimeError(
            f"Continuity check failed for {owner}/{name}: public index has {verify_count} PRs in "
            f"[{oldest_iso}, {newest_iso}], selected {len(selected)} (omitted={omitted_count})"
        )

    records: List[Dict[str, Any]] = []
    truncated_prs = 0
    extra_file_pages = 0
    for pr in selected:
        num = int(pr["number"])
        fconn = pr.get("files") or {}
        init_paths = [fn["path"] for fn in (fconn.get("nodes") or []) if fn and fn.get("path")]
        pinfo = fconn.get("pageInfo") or {}
        paths, fpages = fetch_all_pr_files(
            owner, name, num, init_paths, pinfo.get("endCursor"), bool(pinfo.get("hasNextPage"))
        )
        extra_file_pages += max(0, fpages - 1)
        total_files = int(fconn.get("totalCount") or pr.get("changedFiles") or len(paths))
        is_truncated = len(paths) < total_files
        if is_truncated:
            truncated_prs += 1
        mc = pr.get("mergeCommit") or {}
        records.append(
            {
                "number": num,
                "title": pr.get("title", ""),
                "created_at": pr.get("createdAt"),
                "merged_at": pr.get("mergedAt"),
                "merge_commit_oid": mc.get("oid"),
                "base_ref": branch,
                "additions": int(pr.get("additions") or 0),
                "deletions": int(pr.get("deletions") or 0),
                "changed_files_count": total_files,
                "files_truncated": is_truncated,
                "files": paths,
            }
        )

    meta = {
        "repo": f"{owner}/{name}",
        "base_branch": branch,
        "acquisition_query": q,
        "verification_window_query": verify_q,
        "search_pages_fetched": pages_fetched,
        "extra_file_pages_fetched": extra_file_pages,
        "window_total_candidates": reported_issue_count,
        "target_prs": target,
        "collected_prs": len(records),
        "public_window_merged_count": verify_count,
        "omitted_pr_count": omitted_count,
        "oldest_pr_number": records[0]["number"],
        "newest_pr_number": records[-1]["number"],
        "oldest_merge_commit_oid": records[0]["merge_commit_oid"],
        "newest_merge_commit_oid": records[-1]["merge_commit_oid"],
        "prs_with_truncated_files": truncated_prs,
        "time_span_utc": [oldest_iso, newest_iso],
    }
    return records, meta


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "data" / "traces"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "github_graphql_merged_pr_window_exhaustive_with_full_file_pagination",
        "repositories": [],
    }
    summary: List[Dict[str, Any]] = []
    for owner, name, branch, lookback_days in REPOS:
        print(f"Fetching {TARGET_PRS} exact consecutive merged PRs from {owner}/{name} ({branch})...", flush=True)
        records, meta = fetch_repo_exact_merged_prs(owner, name, branch, lookback_days, TARGET_PRS)
        out_path = out_dir / f"{owner}_{name.replace('.', '_')}.json"
        out_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        uniq_files = len({f for r in records for f in r["files"]})
        avg_files = round(sum(len(r["files"]) for r in records) / max(1, len(records)), 2)
        manifest["repositories"].append(meta)
        summary.append(
            {
                "repo": f"{owner}/{name}",
                "base_branch": branch,
                "prs": len(records),
                "unique_files": uniq_files,
                "avg_files_per_pr": avg_files,
                "public_window_merged_count": meta["public_window_merged_count"],
                "omitted_pr_count": meta["omitted_pr_count"],
                "prs_with_truncated_files": meta["prs_with_truncated_files"],
                "time_span": meta["time_span_utc"],
            }
        )
        print(
            f"  -> collected {len(records)}/{meta['public_window_merged_count']} PRs "
            f"(omitted={meta['omitted_pr_count']}, extra_file_pages={meta['extra_file_pages_fetched']}, "
            f"truncated={meta['prs_with_truncated_files']}, span={meta['time_span_utc'][0]}..{meta['time_span_utc'][1]})",
            flush=True,
        )
    (out_dir / "traces_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out_dir / "fetch_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Wrote exact cross-verified traces + fetch_manifest.json cleanly.")


if __name__ == "__main__":
    main()
