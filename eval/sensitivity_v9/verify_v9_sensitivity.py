#!/usr/bin/env python3
"""Offline verifier for sealed runs/v9_sensitivity/ artifacts, parity, and reproducibility."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.sensitivity_v9.export_v9_sensitivity import export_all  # noqa: E402
from eval.sensitivity_v9.harness_v9_sensitivity import (  # noqa: E402
    CONDITIONS,
    RUN_ID,
    SEEDS,
    STRATEGIES,
    TRACES,
    run_all_cells,
    verify_default_parity,
)

SEAL_FIELDS = {
    "schema_version",
    "run_id",
    "timestamp_utc",
    "git_commit",
    "command",
    "seeds",
    "strategies",
    "repos",
    "record_count",
    "input_sha256",
    "metrics_sha256",
    "artifact_sha256",
    "harness_sha256",
    "dataset_sha256",
    "default_parity",
    "protocol_notes",
    "meta",
}

EXPORTS = (
    "summary.json",
    "tables_v9_sensitivity.tex",
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


def verify_run(
    root: Path,
    run_dir: Path,
    *,
    check_paper_table: bool = True,
    check_repro: bool = False,
    workers: int = min(6, os.cpu_count() or 1),
) -> dict:
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
    expected_count = len(CONDITIONS) * len(TRACES) * len(SEEDS) * len(STRATEGIES)
    require(
        isinstance(rows, list) and len(rows) == manifest["record_count"] == expected_count,
        f"record count mismatch: {len(rows)} vs expected {expected_count}",
    )
    require(all(int(r["escaped_defects"]) == 0 for r in rows), "non-zero escaped_defects found")

    seen_keys = {
        (r["condition"], r["repo"], int(r["seed"]), r["strategy"])
        for r in rows
    }
    require(len(seen_keys) == expected_count, "duplicate or missing (condition, repo, seed, strategy) cells")

    parity = verify_default_parity(rows, root=root)
    require(parity == manifest["default_parity"], "default parity metadata mismatch")

    # Verify byte-for-byte deterministic regeneration of all exported artifacts in a temp dir.
    verified_exports = list(EXPORTS)
    with tempfile.TemporaryDirectory() as td:
        temp_out = Path(td)
        temp_paper = temp_out / "paper_v9_sensitivity.tex"
        export_all(run_dir, temp_out, paper_table_path=temp_paper)
        for name in EXPORTS:
            actual = run_dir / name
            regen = temp_out / name
            require(actual.is_file(), f"missing exported artifact: {name}")
            require(digest(actual) == digest(regen), f"non-deterministic export: {name}")
        if check_paper_table:
            paper_actual = root / "paper" / "generated" / "tables" / "v9_sensitivity.tex"
            require(paper_actual.is_file(), f"missing generated paper table: {paper_actual}")
            require(
                digest(paper_actual) == digest(temp_paper),
                "stale or non-deterministic paper/generated/tables/v9_sensitivity.tex",
            )
            verified_exports.append("paper/generated/tables/v9_sensitivity.tex")

    repro_checked = False
    if check_repro:
        rerun_rows = run_all_cells(workers)
        rerun_bytes = (json.dumps(rerun_rows, indent=2, allow_nan=False) + "\n").encode("utf-8")
        require(
            sha256(rerun_bytes).hexdigest() == manifest["metrics_sha256"],
            "full re-run metrics.json does not match sealed metrics_sha256 byte-for-byte",
        )
        repro_checked = True

    return {
        "ok": True,
        "run_id": RUN_ID,
        "record_count": len(rows),
        "default_parity": parity,
        "verified_exports": verified_exports,
        "full_reproducibility_checked": repro_checked,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true", required=True)
    parser.add_argument("--check-repro", action="store_true")
    parser.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1))
    args = parser.parse_args()
    run_dir = args.run_dir if args.run_dir is not None else args.root / "runs" / RUN_ID
    result = verify_run(
        args.root,
        run_dir,
        check_paper_table=True,
        check_repro=args.check_repro,
        workers=args.workers,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
