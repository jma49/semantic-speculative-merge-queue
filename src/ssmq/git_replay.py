"""Executable packing-by-recovery factorial and stale-snapshot Git controls."""
from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .blame_bench import (
    BASE_REPO,
    CLEAN_PRS,
    DEFECT_PRS,
    SEMANTIC_CONFLICT_PAIRS,
    SIBLING_FUNCTION_PAIRINGS,
    PatchPR,
    _apply_single_patch_strict,
    rank_ast_stack_causal,
    rank_file_trivia_filtered,
)

GIT_REPLAY_STRATEGIES: Tuple[str, ...] = (
    "FIFO+Bisect",
    "FIFO+FileTrivia",
    "FIFO+ASTStack",
    "Disjoint+Bisect",
    "Disjoint+FileTrivia",
    "Disjoint+ASTStack",
)


@dataclass(frozen=True)
class GitReplayMetrics:
    strategy: str
    packing: str
    recovery: str
    seed: int
    total_prs: int
    merged_prs: int
    rejected_prs: int
    final_main_tests_pass: bool
    defects_on_main: int
    conflict_pairs_on_main: int
    real_escaped_defects: int
    candidate_unittest_runs: int
    total_unittest_runs: int
    total_git_merges: int
    git_merge_conflicts: int
    blame_invocations: int
    blame_one_step_hits: int
    bisection_splits: int
    total_decompositions: int
    mean_batch_size: float
    ci_runs_per_merged_pr: float
    recovery_rounds_total: int
    wall_time_ms: float
    events: List[dict]
    final_tree: str
    landed_ids: List[str]
    polarity: str = "asc"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ConcurrentGitSafetyResult:
    pair_id: str
    pr_a: str
    pr_b: str
    worker_a_pass_on_m0: bool
    worker_b_pass_on_m0: bool
    unsafe_stale_commit_escapes: bool
    ssmq_stale_recheck_catches: bool
    ssmq_final_main_pass: bool
    base_commit: str
    worker_a_tested_tree: str
    worker_b_tested_tree: str
    advanced_main_tree: str
    unsafe_main_tree: str
    rechecked_tree: str
    final_main_tree: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
             "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"},
    )


def _rev(repo_dir: Path, name: str) -> str:
    result = _git(repo_dir, "rev-parse", name)
    result.check_returncode()
    return result.stdout.strip()


def _init_base_git_repo(repo_dir: Path) -> None:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "SSMQ Test")
    _git(repo_dir, "config", "user.email", "ssmq@example.invalid")
    for rel, content in BASE_REPO.items():
        p = repo_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    res = _git(repo_dir, "add", ".")
    if res.returncode != 0:
        raise RuntimeError(res.stderr)
    res = _git(repo_dir, "commit", "-m", "base")
    if res.returncode != 0:
        raise RuntimeError(res.stderr)


def _create_pr_branch(repo_dir: Path, pr: PatchPR) -> str:
    """Create a real Git feature branch off initial `base_tag` applying pr's exact edit."""
    branch = f"pr/{pr.pr_id}"
    res = _git(repo_dir, "checkout", "-b", branch, "base_tag")
    if res.returncode != 0:
        raise RuntimeError(f"git checkout -b {branch} failed: {res.stderr}")
    for rel, (old, new) in pr.files.items():
        p = repo_dir / rel
        src = p.read_text(encoding="utf-8")
        patched = _apply_single_patch_strict(src, old, new, pr.pr_id, rel)
        p.write_text(patched, encoding="utf-8")
    _git(repo_dir, "add", ".")
    res = _git(repo_dir, "commit", "-m", f"{pr.pr_id}: {pr.description}")
    if res.returncode != 0:
        raise RuntimeError(f"git commit {branch} failed: {res.stderr}")
    return branch


def _run_real_unittest(repo_dir: Path) -> Tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        timeout=15,
    )
    out = proc.stdout + "\n" + proc.stderr
    if not re.search(r"Ran [1-9][0-9]* tests? in", out):
        raise RuntimeError(f"unittest produced no test count: {out}")
    return proc.returncode == 0, out


def _test_candidate_batch_on_main(
    repo_dir: Path, batch: List[PatchPR], events: List[dict]
) -> Tuple[bool, bool, str, int]:
    """Create a temporary detached merge tree atop current `main`, merge each PR branch
    with real `git merge --no-edit`, and execute real `python3 -B -m unittest`.

    Returns: (merge_clean, tests_passed, output, git_merges_attempted)
    """
    _git(repo_dir, "checkout", "--detach", "main").check_returncode()
    event = {"base_commit": _rev(repo_dir, "main"), "prs": [p.pr_id for p in batch],
             "tested_tree": None, "committed_tree": None}
    events.append(event)
    merges = 0
    for pr in batch:
        merges += 1
        res = _git(repo_dir, "merge", "--no-edit", f"pr/{pr.pr_id}")
        if res.returncode != 0:
            event["merge_conflict_at"] = pr.pr_id
            _git(repo_dir, "merge", "--abort").check_returncode()
            _git(repo_dir, "checkout", "main")
            return False, False, f"GIT_MERGE_CONFLICT on {pr.pr_id}:\n{res.stderr}", merges
    event["tested_tree"] = _rev(repo_dir, "HEAD^{tree}")
    ok, out = _run_real_unittest(repo_dir)
    event["test_pass"] = ok
    event["failed_tests"] = re.findall(r"^(?:FAIL|ERROR): (\w+)", out, re.M)
    if ok:
        head_sha = _git(repo_dir, "rev-parse", "HEAD").stdout.strip()
        _git(repo_dir, "checkout", "main")
        ff = _git(repo_dir, "merge", "--ff-only", head_sha)
        if ff.returncode != 0:
            raise RuntimeError(f"Fast-forward main failed: {ff.stderr}")
        event["committed_tree"] = _rev(repo_dir, "main^{tree}")
        if event["tested_tree"] != event["committed_tree"]:
            raise RuntimeError("Committed content differs from tested content")
        return True, True, out, merges
    _git(repo_dir, "checkout", "main")
    return True, False, out, merges


def build_git_replay_stream(
    seed: int, reverse_ids: bool = False
) -> Tuple[List[PatchPR], Set[str], Tuple[str, str]]:
    """Build a deterministic 12-PR real-Git stream containing:
      - 1 real pairwise semantic conflict (2 PRs, passes individually, fails combined)
      - 2 real single-PR defects (on disjoint hunks from the conflict)
      - 8 real clean functional PRs (including sibling co-file PRs)
    Assigns role-neutral lexical PR IDs (`pr_00`..`pr_11` or reversed `pr_11`..`pr_00`)
    so tie-breaking cannot encode clean/defect/conflict labels.
    Returns: (stream, defect_ids, semantic_conflict_pair_ids)
    """
    rng = random.Random(seed)
    pair = SEMANTIC_CONFLICT_PAIRS[seed % len(SEMANTIC_CONFLICT_PAIRS)]
    used_hunks: Set[Tuple[str, str]] = {
        (rel, old) for pr in pair for rel, (old, _) in pr.files.items()
    }
    def_candidates = [
        d for d in DEFECT_PRS
        if all((rel, old) not in used_hunks for rel, (old, _) in d.files.items())
    ]
    rng.shuffle(def_candidates)
    chosen_defects: List[PatchPR] = []
    for d in def_candidates:
        if len(chosen_defects) >= 2:
            break
        if all((rel, old) not in used_hunks for rel, (old, _) in d.files.items()):
            chosen_defects.append(d)
            for rel, (old, _) in d.files.items():
                used_hunks.add((rel, old))

    chosen_clean: List[PatchPR] = []
    for def_pr, sib_pr in SIBLING_FUNCTION_PAIRINGS:
        if def_pr.pr_id in {d.pr_id for d in chosen_defects}:
            if all((rel, old) not in used_hunks for rel, (old, _) in sib_pr.files.items()):
                chosen_clean.append(sib_pr)
                for rel, (old, _) in sib_pr.files.items():
                    used_hunks.add((rel, old))
                break

    clean_pool = list(CLEAN_PRS)
    rng.shuffle(clean_pool)
    for c in clean_pool:
        if len(chosen_clean) >= 8:
            break
        if all((rel, old) not in used_hunks for rel, (old, _) in c.files.items()):
            chosen_clean.append(c)
            for rel, (old, _) in c.files.items():
                used_hunks.add((rel, old))

    raw_stream = list(pair) + chosen_defects + chosen_clean
    rng.shuffle(raw_stream)
    n = len(raw_stream)
    id_map: Dict[str, str] = {}
    stream: List[PatchPR] = []
    for idx, pr in enumerate(raw_stream):
        neutral_id = f"pr_{(n - 1 - idx):02d}" if reverse_ids else f"pr_{idx:02d}"
        id_map[pr.pr_id] = neutral_id
        stream.append(PatchPR(neutral_id, pr.files, pr.description, pr.added_lines))
    return (
        stream,
        {id_map[d.pr_id] for d in chosen_defects},
        (id_map[pair[0].pr_id], id_map[pair[1].pr_id]),
    )


def _pack_disjoint_batch(ready: List[PatchPR], max_batch: int = 4) -> Tuple[List[PatchPR], List[PatchPR]]:
    """Head-anchored file-disjoint batch packer (Algorithm 1 on repository files)."""
    head = ready[0]
    batch = [head]
    used_files = set(head.files.keys())
    rest: List[PatchPR] = []
    for pr in ready[1:]:
        fset = set(pr.files.keys())
        if len(batch) < max_batch and used_files.isdisjoint(fset):
            batch.append(pr)
            used_files.update(fset)
        else:
            rest.append(pr)
    return batch, rest


def run_git_queue_replay(
    strategy: str, seed: int, max_batch: int = 4, reverse_ids: bool = False
) -> GitReplayMetrics:
    """Execute one cell of the 2x3 real-Git factorial queue matrix."""
    cell = {"StaticBatchBisect": "FIFO+Bisect", "SSMQ": "Disjoint+ASTStack"}.get(strategy, strategy)
    if cell not in GIT_REPLAY_STRATEGIES:
        raise ValueError(f"Unsupported real-Git replay strategy: {strategy}")
    packing, recovery = cell.split("+")
    t_start = time.perf_counter()

    stream, defect_ids, conflict_pair = build_git_replay_stream(seed, reverse_ids=reverse_ids)
    with tempfile.TemporaryDirectory() as td:
        repo_dir = Path(td)
        _init_base_git_repo(repo_dir)
        _git(repo_dir, "tag", "base_tag")
        for pr in stream:
            _create_pr_branch(repo_dir, pr)
        _git(repo_dir, "checkout", "main")

        ready: List[PatchPR] = list(stream)
        pending: List[Tuple[List[PatchPR], bool, int]] = []
        merged_ids: Set[str] = set()
        events: List[dict] = []
        rejected = 0
        candidate_runs = 0
        git_merges = 0
        merge_conflicts = 0
        blame_invocations = 0
        blame_hits = 0
        bisection_splits = 0
        batch_sizes: List[int] = []
        recovery_rounds = 0

        while ready or pending:
            if pending:
                batch, allow_blame, depth = pending.pop(0)
            else:
                if packing == "Disjoint":
                    batch, ready = _pack_disjoint_batch(ready, max_batch=max_batch)
                else:
                    batch, ready = ready[:max_batch], ready[max_batch:]
                allow_blame = recovery in ("FileTrivia", "ASTStack")
                depth = 0

            batch_sizes.append(len(batch))
            merge_ok, test_ok, out, merges_used = _test_candidate_batch_on_main(repo_dir, batch, events)
            git_merges += merges_used
            if not merge_ok:
                merge_conflicts += 1
            else:
                candidate_runs += 1

            if merge_ok and test_ok:
                merged_ids.update(p.pr_id for p in batch)
                if depth > 0:
                    recovery_rounds += depth
                continue

            if len(batch) == 1:
                rejected += 1
                continue

            if allow_blame and merge_ok:
                blame_invocations += 1
                if recovery == "ASTStack":
                    ranked_ids = rank_ast_stack_causal(batch, out)
                else:
                    ranked_ids = rank_file_trivia_filtered(batch, out)
                events[-1]["ranking"] = ranked_ids
                top1_id = ranked_ids[0]
                suspect = [p for p in batch if p.pr_id == top1_id]
                remainder = [p for p in batch if p.pr_id != top1_id]

                rem_merge_ok, rem_test_ok, _, rem_merges = _test_candidate_batch_on_main(repo_dir, remainder, events)
                git_merges += rem_merges
                if not rem_merge_ok:
                    merge_conflicts += 1
                else:
                    candidate_runs += 1
                batch_sizes.append(len(remainder))

                if rem_merge_ok and rem_test_ok:
                    blame_hits += 1
                    merged_ids.update(p.pr_id for p in remainder)
                    recovery_rounds += depth + 1
                elif len(remainder) == 1:
                    rejected += 1
                else:
                    bisection_splits += 1
                    mid = len(remainder) // 2
                    pending.insert(0, (remainder[mid:], False, depth + 2))
                    pending.insert(0, (remainder[:mid], False, depth + 2))
                pending.append((suspect, False, depth + 1))
            else:
                bisection_splits += 1
                mid = len(batch) // 2
                pending.insert(0, (batch[mid:], False, depth + 1))
                pending.insert(0, (batch[:mid], False, depth + 1))

        _git(repo_dir, "checkout", "main")
        final_pass, _ = _run_real_unittest(repo_dir)
        wall_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
        landed = set()
        for pr in stream:
            check = _git(repo_dir, "merge-base", "--is-ancestor", f"pr/{pr.pr_id}", "main")
            if check.returncode == 0:
                landed.add(pr.pr_id)
            elif check.returncode != 1:
                check.check_returncode()
        if landed != merged_ids:
            raise RuntimeError("Scheduler accounting disagrees with Git ancestry")
        defects_landed = len(landed & defect_ids)
        pair_landed = int(conflict_pair[0] in landed and conflict_pair[1] in landed)
        real_escapes = defects_landed + pair_landed
        merged_count = len(merged_ids)
        total_runs_incl_final = candidate_runs + 1
        return GitReplayMetrics(
            strategy=strategy,
            packing=packing,
            recovery=recovery,
            seed=seed,
            total_prs=len(stream),
            merged_prs=merged_count,
            rejected_prs=rejected,
            final_main_tests_pass=final_pass,
            defects_on_main=defects_landed,
            conflict_pairs_on_main=pair_landed,
            real_escaped_defects=real_escapes,
            candidate_unittest_runs=candidate_runs,
            total_unittest_runs=total_runs_incl_final,
            total_git_merges=git_merges,
            git_merge_conflicts=merge_conflicts,
            blame_invocations=blame_invocations,
            blame_one_step_hits=blame_hits,
            bisection_splits=bisection_splits,
            total_decompositions=blame_invocations + bisection_splits,
            mean_batch_size=round(sum(batch_sizes) / max(1, len(batch_sizes)), 3),
            ci_runs_per_merged_pr=round(candidate_runs / max(1, merged_count), 4),
            recovery_rounds_total=recovery_rounds,
            wall_time_ms=wall_ms, events=events, final_tree=_rev(repo_dir, "main^{tree}"),
            landed_ids=sorted(landed),
            polarity="desc" if reverse_ids else "asc",
        )


def run_git_replay_suite(seeds: List[int] = list(range(10))) -> List[Dict[str, object]]:
    """Keep the frozen two-arm harness on its original strategy names."""
    return [
        run_git_queue_replay(strategy, seed).to_dict()
        for seed in seeds
        for strategy in ("StaticBatchBisect", "SSMQ")
    ]


def run_git_factorial_suite(
    seeds: List[int] = list(range(10)), counterbalance: bool = True
) -> List[Dict[str, object]]:
    """Run the counterbalanced 2x3 real-Git factorial suite (120 real repository replays)."""
    polarities = (False, True) if counterbalance else (False,)
    rows: List[Dict[str, object]] = []
    for seed in seeds:
        for reverse_ids in polarities:
            for strategy in GIT_REPLAY_STRATEGIES:
                rows.append(
                    run_git_queue_replay(
                        strategy, seed=seed, max_batch=4, reverse_ids=reverse_ids
                    ).to_dict()
                )
    return rows


def run_concurrent_git_safety_suite() -> List[Dict[str, object]]:
    """Execute real concurrent-snapshot exact-Git-tree stale-pass verification across all 6
    semantic conflict pairs.

    Worker A tests PR_A atop base M_0 (passes); concurrently, Worker B tests PR_B atop the SAME
    stale snapshot M_0 (passes). Worker A commits PR_A to `main` (M_1).
      - UnsafeStaleCommit merges PR_B onto M_1 without exact-tree re-testing -> FAILS main!
      - SSMQExactTreeRecheck detects `main != M_0`, re-merges PR_B atop M_1 and re-runs unittest
        -> CATCHES the semantic conflict and keeps `main` 100% green (0 escapes)!
    """
    results: List[Dict[str, object]] = []
    for idx, (pr_a, pr_b) in enumerate(SEMANTIC_CONFLICT_PAIRS):
        with tempfile.TemporaryDirectory() as td:
            repo_dir = Path(td)
            _init_base_git_repo(repo_dir)
            _git(repo_dir, "tag", "base_tag")
            _create_pr_branch(repo_dir, pr_a)
            _create_pr_branch(repo_dir, pr_b)
            _git(repo_dir, "checkout", "main")
            m0_sha = _git(repo_dir, "rev-parse", "main").stdout.strip()

            # Both candidates start from the same commit and run in isolated worktrees.
            with tempfile.TemporaryDirectory() as workers_dir:
                workers = []
                for pr in (pr_a, pr_b):
                    path = Path(workers_dir) / pr.pr_id
                    _git(repo_dir, "worktree", "add", "--detach", str(path), m0_sha).check_returncode()
                    _git(path, "merge", "--no-edit", f"pr/{pr.pr_id}").check_returncode()
                    workers.append(path)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    (a_pass_m0, _), (b_pass_m0, _) = pool.map(_run_real_unittest, workers)
                if not (a_pass_m0 and b_pass_m0):
                    raise RuntimeError("Both candidates must pass on the original snapshot")
                a_commit = _rev(workers[0], "HEAD")
                a_tree, b_tree = [_rev(w, "HEAD^{tree}") for w in workers]

            # Worker A commits first -> advances main from M_0 to M_1
            _git(repo_dir, "checkout", "main")
            _git(repo_dir, "merge", "--ff-only", a_commit).check_returncode()
            m1_sha = _git(repo_dir, "rev-parse", "main").stdout.strip()
            main_tree = _rev(repo_dir, "main^{tree}")
            if m1_sha == m0_sha or main_tree != a_tree:
                raise RuntimeError("Failed to advance to worker A tested content")

            # Policy 1 (UnsafeStaleCommit): merge PR_B onto M_1 trusting stale M_0 pass
            _git(repo_dir, "checkout", "-b", "unsafe-main", m1_sha).check_returncode()
            _git(repo_dir, "merge", "--no-edit", f"pr/{pr_b.pr_id}").check_returncode()
            unsafe_tree = _rev(repo_dir, "unsafe-main^{tree}")
            unsafe_main_pass, _ = _run_real_unittest(repo_dir)
            unsafe_escapes = not unsafe_main_pass

            # Policy 2 (SSMQExactTreeRecheck): detects main (M_1) != M_0, re-merges & re-tests
            # atop M_1 BEFORE advancing main; rejects on fail so main remains at clean M_1!
            _git(repo_dir, "checkout", "main")
            stale_detected = (_git(repo_dir, "rev-parse", "main").stdout.strip() != m0_sha)
            _git(repo_dir, "checkout", "--detach", "main")
            _git(repo_dir, "merge", "--no-edit", f"pr/{pr_b.pr_id}").check_returncode()
            rechecked_tree = _rev(repo_dir, "HEAD^{tree}")
            recheck_pass, _ = _run_real_unittest(repo_dir)
            ssmq_catches = stale_detected and (not recheck_pass)
            _git(repo_dir, "checkout", "main")
            if recheck_pass:
                raise RuntimeError(f"Expected semantic conflict {pr_a.pr_id}+{pr_b.pr_id} to fail recheck")
            final_ssmq_pass, _ = _run_real_unittest(repo_dir)

            results.append(
                ConcurrentGitSafetyResult(
                    pair_id=f"pair_{idx + 1}",
                    pr_a=pr_a.pr_id,
                    pr_b=pr_b.pr_id,
                    worker_a_pass_on_m0=a_pass_m0,
                    worker_b_pass_on_m0=b_pass_m0,
                    unsafe_stale_commit_escapes=unsafe_escapes,
                    ssmq_stale_recheck_catches=ssmq_catches,
                    ssmq_final_main_pass=final_ssmq_pass,
                    base_commit=m0_sha, worker_a_tested_tree=a_tree, worker_b_tested_tree=b_tree,
                    advanced_main_tree=main_tree, unsafe_main_tree=unsafe_tree,
                    rechecked_tree=rechecked_tree, final_main_tree=_rev(repo_dir, "main^{tree}"),
                ).to_dict()
            )
    return results
