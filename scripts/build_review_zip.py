#!/usr/bin/env python3
"""Build an anonymous supplement from an allowlist and verify the delivered ZIP after extraction."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from verify_v7 import RUNS, digest, member, read_json, require, verify_exports

README = """# Semantic Speculative Merge Queues — review supplement (v7)

Unzip the archive and run the following commands from its root. The verification uses
Python 3.9+ and its standard library and runs offline.

```sh
python3 -B eval/verify_v7.py --verify-only
python3 -B -W error -m unittest discover -s tests
```

The verifier checks file inventory and hashes, original metrics and source inputs, pinned
merge-window membership, changed-file counts and regenerated paper displays. Each sealed
run includes its command, workload, environment, seeds and raw output. Published-repository
commit identifiers are withheld during anonymous review. Source-file hashes identify the
exact experimental code in this package. Merge OIDs belonging to the public trace repositories
and temporary fixture tree OIDs remain available.

To repeat the experiments, use a fresh output directory beside the unpacked supplement.
Simulation uses worker processes. Executable replays require Git with support for worktrees
and `git init -b`. Wall-clock measurements depend on the host.

```sh
python3 eval/harness_v7.py --runs-dir ../ssmq-rerun
python3 eval/git_v7.py blame --runs-dir ../ssmq-rerun
python3 eval/git_v7.py git --runs-dir ../ssmq-rerun
python3 eval/git_v7.py stale --runs-dir ../ssmq-rerun
python3 eval/quixbugs_v7.py --runs-dir ../ssmq-rerun
```

Optional live trace recheck (requires the authenticated GitHub CLI):
`python3 scripts/check_trace_windows.py --output ../fresh-window-ids.json`.
The sealed ledger contains the page-by-page PR IDs from GitHub REST search, compared to
the recorded GraphQL traces. Both endpoints use GitHub's search index.

The paper source starts at `paper/main.tex`. On a system with TeX Live and IEEEtran run
`latexmk -pdf -cd paper/main.tex`. The bibliographic sources are in `paper/refs.bib`.
"""


def tex_sources():
    base = ROOT / "paper"
    found = set()
    todo = [base / "main.tex"]
    while todo:
        src = todo.pop()
        if src in found:
            continue
        found.add(src)
        content = re.sub(r"(?<!\\)%[^\n]*", "", src.read_text())
        for name in re.findall(r"\\(?:input|include)\{([^}]+)\}", content):
            child = base / name
            todo.append(child if child.suffix else child.with_suffix(".tex"))
    return {p.relative_to(ROOT).as_posix() for p in found}


def git_info(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def check_identity(stage):
    # Derive private identifiers from the checkout, without writing them into review materials.
    denied = {Path.home().name, git_info("config", "user.email"), git_info("config", "user.name")}
    remote = git_info("remote", "get-url", "origin")
    match = re.search(r"github\.com[:/]([^/]+)/", remote)
    if match:
        denied.add(match.group(1))
    denied.update(git_info("rev-list", "--all").splitlines())
    tokens = [t.casefold() for t in denied if len(t) >= 4]
    for path in stage.rglob("*"):
        if path.is_file():
            data = path.read_text(encoding="utf-8").casefold()
            if any(t in data or t in str(path.relative_to(stage)).casefold() for t in tokens):
                raise ValueError(f"checkout identity in {path.relative_to(stage)}")
            if re.search(r"(?:/users/|/home/|/private/var/folders/|/var/folders/|file://)", data):
                raise ValueError(f"local user path in {path.relative_to(stage)}")


def populate(stage):
    names = set(verify_exports(ROOT)) | tex_sources()
    names.update({"paper/refs.bib", "scripts/fetch_traces.py", "scripts/check_trace_windows.py",
                  "eval/verify_v7.py", "eval/analyze_traces.py", "eval/git_displays_v7.py",
                  "eval/sim_displays_v7.py", "eval/export_v7.py"})
    for pattern in ("data/traces/*.json", "src/ssmq/*.py", "tests/test_*.py"):
        names.update(p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern))
    for run_id in RUNS:
        prefix = f"runs/{run_id}"
        manifest = read_json(member(ROOT, f"{prefix}/manifest.json"))
        names.update(manifest["input_sha256"])
        names.add(f"{prefix}/metrics.json")
        manifest["git_commit"] = "withheld-for-anonymous-review"
        target = stage / prefix / "manifest.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(manifest, indent=2) + "\n")
    for name in sorted(names):
        source = member(ROOT, name)
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (stage / "README.md").write_text(README)
    check_identity(stage)
    inventory = {p.relative_to(stage).as_posix(): digest(p) for p in sorted(stage.rglob("*")) if p.is_file()}
    (stage / "checksums.json").write_text(json.dumps(inventory, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as td:
        tmp = Path(td)
        stage = tmp / "contents"
        stage.mkdir()
        populate(stage)
        archive = tmp / "review.zip"
        with ZipFile(archive, "w") as bundle:
            for src in sorted(stage.rglob("*")):
                if src.is_file():
                    entry = ZipInfo(src.relative_to(stage).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                    entry.create_system = 3
                    entry.external_attr = 0o100644 << 16
                    bundle.writestr(entry, src.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)
        extracted = tmp / "offline-check"
        with ZipFile(archive) as bundle:
            require(bundle.testzip() is None, "corrupted review ZIP")
            bundle.extractall(extracted)
        subprocess.run([sys.executable, "-B", "eval/verify_v7.py", "--verify-only"], cwd=extracted, check=True)
        os.replace(archive, output)
    print(f"{digest(output)}  {output}")


if __name__ == "__main__":
    main()
