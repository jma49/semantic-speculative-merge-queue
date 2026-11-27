#!/usr/bin/env python3
"""Offline verifier for v8 historical CI artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
SPEC_PATH = EVAL_DIR / "sample_spec.json"
RUN_ID = "v8_historical_ci"


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def member(root: Path, name: str) -> Path:
    path = PurePosixPath(name)
    require(name == path.as_posix() and not path.is_absolute() and ".." not in path.parts, f"bad path {name}")
    result = root / name
    require(result.is_file(), f"missing {name}")
    return result


def verify_spec_unchanged(data_dir: Path) -> None:
    spec = read_json(SPEC_PATH)
    prov = read_json(data_dir / "provenance.json")
    require(prov.get("spec_sha256") == digest(SPEC_PATH), "sample_spec.json changed after fetch")
    raw = read_json(data_dir / "raw_records.json")
    require(raw.get("spec_sha256") == prov.get("spec_sha256"), "raw spec hash mismatch")
    regex = re.compile(spec["parsed_identities"]["merge_group_branch_regex"])
    for run in raw.get("merge_group_runs", []):
        branch = run.get("head_branch") or ""
        m = regex.search(branch)
        require(m is not None, f"unparseable branch persisted: {branch}")
        ident = run["queue_identity"]
        require(int(ident["pr_number"]) == int(m.group("pr_number")), "pr_number mismatch")
        require(ident["queue_base_parent_sha"] == m.group("base_parent_sha"), "parent sha mismatch")


def verify_summary(data_dir: Path) -> None:
    summary_path = data_dir / "descriptive_summary.json"
    require(summary_path.is_file(), "missing descriptive_summary.json")
    summary = read_json(summary_path)
    raw = read_json(data_dir / "raw_records.json")
    require(summary["counts"] == raw["counts"], "summary counts mismatch")


def verify_run_seal(root: Path) -> Dict[str, Any]:
    manifest = read_json(root / f"runs/{RUN_ID}/manifest.json")
    metrics = read_json(root / f"runs/{RUN_ID}/metrics.json")
    data_dir = root / "data/historical_ci"
    files = {
        "raw_records.json": digest(data_dir / "raw_records.json"),
        "provenance.json": digest(data_dir / "provenance.json"),
        "descriptive_summary.json": digest(data_dir / "descriptive_summary.json"),
    }
    require(manifest.get("metrics_sha256") == digest(root / f"runs/{RUN_ID}/metrics.json"), "metrics seal")
    stored = manifest.get("dataset_sha256")
    combo = hashlib.sha256()
    for name in sorted(files):
        combo.update(name.encode())
        combo.update(files[name].encode())
    require(stored == combo.hexdigest(), "dataset_sha256 mismatch")
    require(metrics["merge_group_runs_selected"] == manifest.get("record_count"), "record_count mismatch")
    return {"ok": True, "files": files}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    data_dir = args.root / "data/historical_ci"
    verify_spec_unchanged(data_dir)
    verify_summary(data_dir)
    if (args.root / f"runs/{RUN_ID}/manifest.json").is_file():
        print(json.dumps(verify_run_seal(args.root), indent=2))
    else:
        print(json.dumps({"ok": True, "stage": "data_only"}, indent=2))


if __name__ == "__main__":
    main()