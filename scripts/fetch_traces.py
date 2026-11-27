#!/usr/bin/env python3
"""Auditable, reproducible fetcher for strictly consecutive merged GitHub PR traces.

Queries GitHub GraphQL `search(type: ISSUE, query: "repo:<owner>/<name> is:pr is:merged base:<default_branch> sort:merged-desc")`
— wait: GitHub GraphQL `pullRequests(states: MERGED, baseRefName: <default_branch>, orderBy: {field: UPDATED_AT, direction: DESC})`
does NOT order strictly by mergedAt, whereas GitHub's `repository.defaultBranchRef.target.history`
(first-parent commit history on the default branch) enumerates EVERY SINGLE MERGED COMMIT on the
default branch in strictly consecutive chronological order with ZERO gaps!

This script traverses `defaultBranchRef.target.history(first: 100)` (strictly consecutive commits
on the default branch), resolves each commit's associated merged PR (deduplicated in exact
mainline commit order) until 200 strictly consecutive merged PRs are collected per repository,
paginating `files(first: 100)` per PR, and writes a complete `data/traces/fetch_manifest.json`
documenting query, branch, SHA window, and any file-list truncation (>100 files).
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPOS: List[Tuple[str, str, str]] = [
    ("kubernetes", "kubernetes", "master"),
    ("microsoft", "TypeScript", "main"),
    ("vercel", "next.js", "canary"),
]

TARGET_PRS = 200

HISTORY_QUERY = """
query($owner: String!, $name: String!, $branch: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    ref(qualifiedName: $branch) {
      target {
        ... on Commit {
          history(first: 50, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              oid
              committedDate
              associatedPullRequests(first: 5) {
                nodes {
                  number
                  title
                  createdAt
                  mergedAt
                  baseRefName
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
        }
      }
    }
  }
}
"""


def run_graphql(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, val in variables.items():
        if val is None:
            continue
        cmd.extend(["-F", f"{key}={val}"])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data["data"]


def fetch_repo_consecutive_prs(
    owner: str, name: str, branch: str, target: int = TARGET_PRS
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    seen_numbers: Set[int] = set()
    records: List[Dict[str, Any]] = []
    commits_scanned = 0
    commits_without_pr = 0
    truncated_prs = 0
    cursor: Optional[str] = None
    first_oid: Optional[str] = None
    last_oid: Optional[str] = None

    while len(records) < target:
        data = run_graphql(
            HISTORY_QUERY,
            {"owner": owner, "name": name, "branch": f"refs/heads/{branch}", "cursor": cursor},
        )
        history = data["repository"]["ref"]["target"]["history"]
        nodes = history["nodes"]
        if not nodes:
            break
        for commit in nodes:
            if len(records) >= target:
                break
            commits_scanned += 1
            oid = commit["oid"]
            if first_oid is None:
                first_oid = oid
            last_oid = oid
            assoc = commit.get("associatedPullRequests", {}).get("nodes", [])
            matched = [
                pr for pr in assoc
                if pr and pr.get("mergedAt") and pr.get("baseRefName") == branch
            ]
            if not matched:
                commits_without_pr += 1
                continue
            # Prefer PR whose mergedAt is closest to commit date
            pr = matched[0]
            num = int(pr["number"])
            if num in seen_numbers:
                continue
            seen_numbers.add(num)
            files_conn = pr.get("files") or {}
            file_nodes = files_conn.get("nodes") or []
            paths = [fn["path"] for fn in file_nodes if fn and fn.get("path")]
            total_files = int(files_conn.get("totalCount") or pr.get("changedFiles") or len(paths))
            is_truncated = bool(files_conn.get("pageInfo", {}).get("hasNextPage")) or len(paths) < total_files
            if is_truncated:
                truncated_prs += 1
            records.append(
                {
                    "number": num,
                    "title": pr.get("title", ""),
                    "created_at": pr.get("createdAt"),
                    "merged_at": pr.get("mergedAt"),
                    "merge_commit_oid": oid,
                    "base_ref": branch,
                    "additions": int(pr.get("additions") or 0),
                    "deletions": int(pr.get("deletions") or 0),
                    "changed_files_count": total_files,
                    "files_truncated": is_truncated,
                    "files": paths,
                }
            )
        if not history["pageInfo"]["hasNextPage"]:
            break
        cursor = history["pageInfo"]["endCursor"]

    # Reverse into strict chronological (oldest-to-newest) order
    records.reverse()
    meta = {
        "repo": f"{owner}/{name}",
        "base_branch": branch,
        "target_prs": target,
        "collected_prs": len(records),
        "mainline_commits_scanned": commits_scanned,
        "mainline_commits_without_branch_pr": commits_without_pr,
        "newest_commit_oid": first_oid,
        "oldest_commit_oid": last_oid,
        "file_cap_per_pr": 100,
        "prs_with_truncated_files": truncated_prs,
        "time_span_utc": [records[0]["merged_at"], records[-1]["merged_at"]] if records else [],
    }
    return records, meta


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "data" / "traces"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "default_branch_commit_history_associated_merged_prs",
        "file_cap_per_pr": 100,
        "repositories": [],
    }
    summary: List[Dict[str, Any]] = []
    for owner, name, branch in REPOS:
        print(f"Fetching {TARGET_PRS} strictly consecutive merged PRs from {owner}/{name} ({branch})...", flush=True)
        records, meta = fetch_repo_consecutive_prs(owner, name, branch, TARGET_PRS)
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
                "prs_with_truncated_files": meta["prs_with_truncated_files"],
                "time_span": meta["time_span_utc"],
            }
        )
        print(
            f"  -> collected {len(records)} PRs from {meta['mainline_commits_scanned']} consecutive {branch} commits "
            f"(no-PR commits: {meta['mainline_commits_without_branch_pr']}, truncated>100: {meta['prs_with_truncated_files']}, "
            f"span: {meta['time_span_utc'][0]} .. {meta['time_span_utc'][1]})",
            flush=True,
        )
    (out_dir / "traces_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out_dir / "fetch_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Wrote traces + fetch_manifest.json cleanly.")


if __name__ == "__main__":
    main()
