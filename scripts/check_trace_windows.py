#!/usr/bin/env python3
"""Record and compare all PR IDs returned by GitHub REST search for the pinned windows."""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "data" / "traces"


def acquire(query):
    pages = []
    for page in range(1, 11):
        response = subprocess.run(
            ["gh", "api", "--method", "GET", "search/issues", "-f", f"q={query}",
             "-F", "per_page=100", "-F", f"page={page}", "-f", "sort=created", "-f", "order=asc"],
            check=True, capture_output=True, text=True,
        )
        result = json.loads(response.stdout)
        if result["incomplete_results"] or result["total_count"] > 1000:
            raise ValueError(f"incomplete GitHub search for {query}")
        numbers = [item["number"] for item in result["items"] if "pull_request" in item]
        pages.append(numbers)
        if len(result["items"]) < 100 or page * 100 >= result["total_count"]:
            break
    ids = [n for page in pages for n in page]
    if len(set(ids)) != len(ids) or len(ids) != result["total_count"]:
        raise ValueError(f"duplicate or missing records for {query}")
    return {"query": query, "total_count": result["total_count"], "pages": pages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ledger = {}
    for meta in json.loads((TRACES / "fetch_manifest.json").read_text())["repositories"]:
        repo = meta["repo"]
        response = acquire(meta["verification_window_query"])
        trace = TRACES / (repo.replace("/", "_").replace(".", "_") + ".json")
        expected = {pr["number"] for pr in json.loads(trace.read_text())}
        actual = {n for page in response["pages"] for n in page}
        if expected != actual:
            raise ValueError(f"{repo}: missing {sorted(actual - expected)}, extra {sorted(expected - actual)}")
        ledger[repo] = response
        print(f"{repo}: all {len(actual)} identifiers match", flush=True)
    args.output.write_text(json.dumps(ledger, indent=2) + "\n")


if __name__ == "__main__":
    main()
