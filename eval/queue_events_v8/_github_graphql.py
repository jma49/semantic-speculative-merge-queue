"""Minimal GitHub GraphQL client for queue timeline capture (stdlib only)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

GRAPHQL_URL = "https://api.github.com/graphql"

TIMELINE_QUERY = """
query QueueTimeline($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      state
      headRefOid
      merged
      closed
      isDraft
      createdAt
      closedAt
      mergedAt
      url
      timelineItems(
        first: 100
        after: $after
        itemTypes: [
          ADDED_TO_MERGE_QUEUE_EVENT
          REMOVED_FROM_MERGE_QUEUE_EVENT
          CLOSED_EVENT
          REOPENED_EVENT
        ]
      ) {
        pageInfo { hasNextPage endCursor }
        nodes {
          __typename
          ... on AddedToMergeQueueEvent { createdAt }
          ... on RemovedFromMergeQueueEvent { createdAt reason }
          ... on ClosedEvent { createdAt }
          ... on ReopenedEvent { createdAt }
        }
      }
    }
  }
}
"""


def _token() -> Optional[str]:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(key)
        if val:
            return val.strip()
    return None


def graphql(
    query: str,
    variables: Dict[str, Any],
    *,
    max_retries: int = 6,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ssmq-queue-events-v8/1.0",
    }
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    page_log: List[Dict[str, Any]] = []
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if body.get("errors"):
                raise RuntimeError(json.dumps(body["errors"]))
            page_log.append({"attempt": attempt + 1, "ok": True})
            return body.get("data") or {}, page_log
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code in (403, 429, 502, 503) or exc.code >= 500:
                time.sleep(min(2**attempt, 60))
                continue
            raise
        except urllib.error.URLError as exc:
            last_err = exc
            time.sleep(2**attempt)
            continue
    raise RuntimeError("GraphQL request failed after retries") from last_err


def fetch_pr_timeline(owner: str, name: str, number: int) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (pull_record, timeline_nodes, pagination_log)."""
    nodes: List[Dict[str, Any]] = []
    pagination: List[Dict[str, Any]] = []
    pull: Optional[Dict[str, Any]] = None
    after: Optional[str] = None
    page = 0
    while True:
        page += 1
        data, _ = graphql(
            TIMELINE_QUERY,
            {"owner": owner, "name": name, "number": number, "after": after},
        )
        pr = ((data.get("repository") or {}).get("pullRequest"))
        if pr is None:
            pagination.append({"page": page, "pr_number": number, "missing": True})
            return None, [], pagination
        if pull is None:
            pull = {
                "number": pr["number"],
                "state": pr.get("state"),
                "headRefOid": pr.get("headRefOid"),
                "merged": pr.get("merged"),
                "closed": pr.get("closed"),
                "isDraft": pr.get("isDraft"),
                "createdAt": pr.get("createdAt"),
                "closedAt": pr.get("closedAt"),
                "mergedAt": pr.get("mergedAt"),
                "url": pr.get("url"),
            }
        items = pr.get("timelineItems") or {}
        chunk = items.get("nodes") or []
        nodes.extend(chunk)
        pi = items.get("pageInfo") or {}
        pagination.append(
            {
                "page": page,
                "pr_number": number,
                "nodes_this_page": len(chunk),
                "nodes_cumulative": len(nodes),
                "has_next": bool(pi.get("hasNextPage")),
                "end_cursor": pi.get("endCursor"),
            }
        )
        if not pi.get("hasNextPage"):
            break
        after = pi.get("endCursor")
    return pull, nodes, pagination