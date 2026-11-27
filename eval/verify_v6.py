#!/usr/bin/env python3
"""Verify run seals, trace acquisition records and deterministic paper displays offline."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNS = (
    "v6_sim_main", "v6_sim_recall", "v6_sim_proxy", "v6_exp0_blame_microbench",
    "v6_exp10_real_git_replay", "v6_exp11_concurrent_git_safety", "v6_exp12_quixbugs",
)
REPOS = {"kubernetes/kubernetes": "master", "microsoft/TypeScript": "main", "vercel/next.js": "canary"}
SEAL_FIELDS = {
    "run_id", "timestamp_utc", "git_commit", "command", "seeds", "record_count",
    "input_sha256", "metrics_sha256", "artifact_sha256", "harness_sha256", "dataset_sha256",
}
PAPER_EXPORTS = ("results/v6_paper.json", "paper/generated/v6.tex")
TRACE_EXPORTS = ("results/trace_stats.json", "paper/generated/tables/trace_summary.tex")


def require(value, message):
    if not value:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def member(root, name):
    path = PurePosixPath(name)
    require(name == path.as_posix() and not path.is_absolute() and ".." not in path.parts,
            f"invalid relative filename: {name}")
    result = root / name
    require(result.is_file() and result.resolve() == result.absolute(), f"missing or linked file: {name}")
    return result


def verify_traces(root):
    traces = root / "data/traces"
    acquisition = read_json(traces / "fetch_manifest.json")["repositories"]
    summary_list = read_json(traces / "traces_summary.json")
    summaries = {row["repo"]: row for row in summary_list}
    windows = read_json(traces / "window_ids.json")
    for values in ([row["repo"] for row in acquisition], [row["repo"] for row in summary_list], list(windows)):
        require(sorted(values) == sorted(REPOS), "trace repository inventory mismatch")
    for meta in acquisition:
        repo = meta["repo"]
        branch = REPOS[repo]
        trace_file = repo.replace("/", "_").replace(".", "_") + ".json"
        rows = read_json(traces / trace_file)
        n = len(rows)
        require(rows and meta["base_branch"] == branch, f"{repo}: base branch mismatch")
        require(rows == sorted(rows, key=lambda r: (r["merged_at"], r["number"])), f"{repo}: trace ordering")
        ids = {pr["number"] for pr in rows}
        require(len(ids) == n == meta["target_prs"] == meta["collected_prs"] == 200, f"{repo}: trace count")
        span = [rows[0]["merged_at"], rows[-1]["merged_at"]]
        query = f"repo:{repo} is:pr is:merged base:{branch} merged:{span[0]}..{span[1]}"
        ledger = windows[repo]
        rest_ids = [pr for page in ledger["pages"] for pr in page]
        require(meta["verification_window_query"] == ledger["query"] == query
                and ledger["total_count"] == n == len(rest_ids) and set(rest_ids) == ids,
                f"{repo}: pinned REST and GraphQL membership differs")
        require(meta["public_window_merged_count"] == n and meta["omitted_pr_count"] == 0
                and meta["prs_with_truncated_files"] == 0 and meta["time_span_utc"] == span,
                f"{repo}: acquisition summary")
        for label, pr in (("oldest", rows[0]), ("newest", rows[-1])):
            require(meta[f"{label}_pr_number"] == pr["number"] and
                    meta[f"{label}_merge_commit_oid"] == pr["merge_commit_oid"], f"{repo}: window boundary")
        files = set()
        for pr in rows:
            paths = pr["files"]
            require(len(set(paths)) == len(paths) == pr["changed_files_count"]
                    and pr["files_truncated"] is False and pr["base_ref"] == branch
                    and re.fullmatch(r"[0-9a-f]{40}", pr["merge_commit_oid"]) is not None,
                    f"{repo} #{pr['number']}: file or commit metadata")
            files.update(paths)
        summary = {"repo": repo, "base_branch": branch, "prs": n, "unique_files": len(files),
                   "avg_files_per_pr": round(sum(len(p["files"]) for p in rows) / n, 2),
                   "public_window_merged_count": n, "omitted_pr_count": 0,
                   "prs_with_truncated_files": 0, "time_span": span}
        require(summary == summaries[repo], f"{repo}: exported trace summary")


def verify_runs(root):
    counts = {}
    for run_id in RUNS:
        manifest = read_json(member(root, f"runs/{run_id}/manifest.json"))
        require(SEAL_FIELDS <= manifest.keys() and manifest["run_id"] == run_id, f"{run_id}: manifest schema")
        inputs = manifest["input_sha256"]
        require(bool(inputs), f"{run_id}: missing input map")
        for filename, expected in inputs.items():
            require(digest(member(root, filename)) == expected, f"{run_id}: changed input {filename}")
        if run_id.startswith("v6_sim_"):
            def combined(names):
                h = hashlib.sha256()
                for name in sorted(names):
                    h.update(Path(name).name.encode())
                    h.update(inputs[name].encode())
                return h.hexdigest()
            data = [n for n in inputs if n.startswith("data/")]
            code = [n for n in inputs if n.startswith(("src/", "eval/"))]
            require(len(data) >= len(REPOS) and code and len(data) + len(code) == len(inputs),
                    f"{run_id}: incomplete source/data inventory")
            source_hash, data_hash = combined(code), combined(data)
        else:
            source_hash = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
            if run_id == "v6_exp12_quixbugs":
                data = {k: v for k, v in inputs.items() if k.startswith("data/")}
                data_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
            else:
                data_hash = inputs["src/ssmq/blame_bench.py"]
        require(source_hash == manifest["harness_sha256"] and data_hash == manifest["dataset_sha256"],
                f"{run_id}: aggregate input seal")
        path = member(root, f"runs/{run_id}/metrics.json")
        require(digest(path) == manifest["metrics_sha256"] == manifest["artifact_sha256"], f"{run_id}: metrics seal")
        payload = read_json(path)
        records = payload if isinstance(payload, list) else payload["records"]
        require(isinstance(records, list) and len(records) == manifest["record_count"] > 0,
                f"{run_id}: record count")
        counts[run_id] = len(records)
    return counts


def verify_checksums(root):
    index = root / "checksums.json"
    if not index.is_file():
        return
    hashes = read_json(index)
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
              and p != index and "__pycache__" not in p.parts}
    require(actual == set(hashes), "review package file inventory differs from checksums.json")
    for name, expected in hashes.items():
        require(digest(member(root, name)) == expected, f"package SHA-256 mismatch: {name}")


def verify_exports(root):
    # Regenerate outputs from scratch in isolation so verify-only never edits the bundle.
    with tempfile.TemporaryDirectory() as td:
        stage = Path(td)
        for source in ("eval", "src", "data/traces"):
            shutil.copytree(root / source, stage / source, ignore=shutil.ignore_patterns("__pycache__"))
        for run in RUNS:
            shutil.copytree(root / "runs" / run, stage / "runs" / run)
        for folder in ("results", "paper/generated/tables", "paper/generated/figures"):
            (stage / folder).mkdir(parents=True, exist_ok=True)
        for script in ("export_v6.py",):
            subprocess.run([sys.executable, "-B", str(stage / "eval" / script)], cwd=stage,
                           check=True, stdout=subprocess.DEVNULL)
        produced = {p.relative_to(stage).as_posix() for top in ("paper/generated", "results")
                    for p in (stage / top).rglob("*") if p.is_file()}
        require(set(PAPER_EXPORTS + TRACE_EXPORTS) <= produced, "missing regenerated paper/trace exports")
        for name in sorted(produced):
            require(digest(stage / name) == digest(member(root, name)), f"regeneration differs: {name}")
        return sorted(produced)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", required=True,
                        help="check sealed evidence and regenerate displays in a temporary directory")
    parser.parse_args()
    verify_checksums(ROOT)
    verify_traces(ROOT)
    counts = verify_runs(ROOT)
    outputs = verify_exports(ROOT)
    print("Pinned trace membership and file lists verified.")
    for label, count in counts.items():
        print(f"{label}: {count} records")
    print("Verified exports: " + ", ".join(outputs))


if __name__ == "__main__":
    main()
