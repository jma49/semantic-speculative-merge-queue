"""Minimal trusted GitHub REST client for historical CI capture (stdlib only)."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

API_ROOT = "https://api.github.com"


def _token() -> Optional[str]:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(key)
        if val:
            return val.strip()
    try:
        out = urllib.request.urlopen(
            urllib.request.Request(
                "https://api.github.com/",
                headers={"User-Agent": "ssmq-historical-ci/1.0"},
            ),
            timeout=30,
        )
        out.read()
    except Exception:
        pass
    return None


def _auth_headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ssmq-historical-ci/1.0",
    }
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return headers


def request_json(
    path: str,
    *,
    query: Optional[Dict[str, str]] = None,
    max_retries: int = 6,
) -> Tuple[Any, Dict[str, str]]:
    """GET path (relative to api.github.com) and return (json_body, response_headers)."""
    if path.startswith("http"):
        url = path
    else:
        url = f"{API_ROOT}/{path.lstrip('/')}"
    if query:
        qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in query.items())
        url = f"{url}?{qs}"

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=_auth_headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8")
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return json.loads(body), headers
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code in (403, 429) or exc.code >= 500:
                reset = exc.headers.get("X-RateLimit-Reset") or exc.headers.get("x-ratelimit-reset")
                wait = 2 ** attempt
                if reset and reset.isdigit():
                    wait = max(wait, int(reset) - int(time.time()) + 1)
                time.sleep(min(wait, 60))
                continue
            raise
        except urllib.error.URLError as exc:
            last_err = exc
            time.sleep(2 ** attempt)
            continue
    raise RuntimeError(f"GitHub request failed after retries: {url}") from last_err


LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


def paginate(
    path: str,
    *,
    query: Optional[Dict[str, str]] = None,
    item_key: Optional[str] = None,
    max_pages: Optional[int] = None,
) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """Follow Link: rel=next headers. Returns (items, page_log)."""
    items: List[Any] = []
    page_log: List[Dict[str, Any]] = []
    url_path = path
    local_query = dict(query or {})
    page = 0
    while True:
        page += 1
        if max_pages is not None and page > max_pages:
            page_log.append({"page": page, "stopped_reason": "max_pages"})
            break
        body, headers = request_json(url_path, query=local_query if page == 1 else None)
        if item_key:
            chunk = body.get(item_key, [])
        elif isinstance(body, list):
            chunk = body
        else:
            chunk = [body]
        items.extend(chunk)
        link = headers.get("link", "")
        nxt = None
        for part in link.split(","):
            m = LINK_NEXT.search(part)
            if m:
                nxt = m.group(1)
                break
        page_log.append(
            {
                "page": page,
                "path": url_path,
                "query": local_query if page == 1 else None,
                "items_this_page": len(chunk),
                "items_cumulative": len(items),
                "has_next": bool(nxt),
            }
        )
        if not nxt:
            break
        url_path = nxt
        local_query = {}
    return items, page_log