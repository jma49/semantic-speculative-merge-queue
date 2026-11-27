#!/usr/bin/env python3
"""Offline verifier for sealed runs/v8_baselines/ artifacts and deterministic exports."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.baselines_v8.export_v8_baselines import export_all  # noqa: E402
from eval.baselines_v8.harness_v8_baselines import RUN_ID, verify_v7_control_parity  # noqa: E402

SEAL_FIELDS = {
    "schema_version",
    "run_id",
    "timestamp_utc",
    "git_commit",
    "command",
    "seeds",
    "record_count",
    "input_sha256",
    "metrics_sha256",
    "artifact_sha256",
    "harness_sha256",
    "dataset_sha256",
    "v7_control_parity",
}

EXPORTS = (
    "summary.json",
    "tables_v8_baselines.tex",
    "baseline_coverage_sheet.json",
    "baseline_coverage_sheet.md",
)


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def member(root: Path, name: str) -> Path:
    path = PurePosixPath(name)
    require(
        name == path.as_posix() and not path.is_absolute() and ".." not in path.parts,
        f"invalid relative filename: {name}",
    )
    result = root / name
    require(result.is_file() and result.resolve() == result.absolute(), f"missing or linked file: {name}")
    return result


def verify_run(root: Path, run_dir: Path) -> dict:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    require(SEAL_FIELDS <= manifest.keys() and manifest["run_id"] == RUN_ID, "manifest schema mismatch")
    inputs = manifest["input_sha256"]
    require(bool(inputs), "missing input_sha256")
    for filename, expected in inputs.items():
        require(digest(member(root, filename)) == expected, f"changed input: {filename}")

    def combined(names):
        h = sha256()
        for name in sorted(names):
            h.update(Path(name).name.encode())
            h.update(inputs[name].encode())
        return h.hexdigest()

    data_names = [n for n in inputs if n.startswith("data/")]
    code_names = [n for n in inputs if n.startswith(("src/", "eval/"))]
    require(
        len(data_names) == 3 and code_names and len(data_names) + len(code_names) == len(inputs),
        "incomplete source/data inventory",
    )
    require(
        combined(code_names) == manifest["harness_sha256"]
        and combined(data_names) == manifest["dataset_sha256"],
        "aggregate input seal mismatch",
    )

    metrics_path = run_dir / "metrics.json"
    require(
        digest(metrics_path) == manifest["metrics_sha256"] == manifest["artifact_sha256"],
        "metrics seal mismatch",
    )
    rows = json.loads(metrics_path.read_text(encoding="utf-8"))
    require(isinstance(rows, list) and len(rows) == manifest["record_count"] == 2880, "record count mismatch")
    require(all(r["escaped_defects"] == 0 for r in rows), "non-zero escaped_defects found")

    parity = verify_v7_control_parity(rows, root=root)
    require(parity == manifest["v7_control_parity"], "v7 control parity metadata mismatch")

    # Verify byte-for-byte deterministic regeneration of all exported artifacts in a temp dir.
    with tempfile.TemporaryDirectory() as td:
        temp_out = Path(td)
        export_all(run_dir, temp_out)
        for name in EXPORTS:
            actual = run_dir / name
            regen = temp_out / name
            require(actual.is_file(), f"missing exported artifact: {name}")
            require(digest(actual) == digest(regen), f"non-deterministic export: {name}")

    return {
        "ok": True,
        "run_id": RUN_ID,
        "record_count": len(rows),
        "v7_control_parity": parity,
        "verified_exports": list(EXPORTS),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true", required=True)
    args = parser.parse_args()
    run_dir = args.run_dir if args.run_dir is not None else args.root / "runs" / RUN_ID
    result = verify_run(args.root, run_dir)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
