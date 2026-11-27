# Semantic Speculative Merge Queues (SSMQ) — v5 Artifact

> *Semantic Speculative Merge Queues: Graph-Disjoint Batching and Causal Fault Attribution for High-Velocity CI*

## What Is Included in the v5 Artifact

1. **Auditable Strictly Consecutive GitHub PR Traces (`scripts/fetch_traces.py`, `data/traces/fetch_manifest.json`)**: Traverses default-branch commit history (`master`, `main`, `canary`) via GitHub GraphQL with 100-file pagination to collect **200 strictly consecutive merged PRs** per repository (`kubernetes/kubernetes`, `microsoft/TypeScript`, `vercel/next.js`; 600 PRs total, 10.9–29.6 day contiguous spans).
2. **86-Batch Symmetrically Counterbalanced Python Blame Benchmark (`src/ssmq/blame_bench.py`, `runs/v5_exp0_blame_microbench`)**: Executes **86 real failing Python `unittest` batches** with unique-hunk patch enforcement (`count == 1`), verified nonzero test counts, 50/50 lexical PR-ID & matched-churn symmetry across comment (`16`) and functional sibling-function (`22`) co-file distractors, strict nested file baseline (`FileTriviaNestedRanker`), and **real executed leave-one-out recovery across all 5 rankers**:
   - `FileRawOverlapRanker`: 66.3% Top-1 / 50.0% 50/50 Comment / 50.0% 50/50 Sibling / 66.3% LOO
   - `FileTriviaNestedRanker`: 75.6% Top-1 / 100.0% 50/50 Comment / 50.0% 50/50 Sibling / 75.6% LOO
   - **`ASTStackCausalRanker`**: **98.8% Top-1 / 100.0% Top-2 / 0.994 MRR / 100.0% Comment / 95.5% 50/50 Sibling / 98.8% Executed LOO** in **2.27 ms mean**
3. **10-Seed Real Exact-Git-Tree Queue Replay (`src/ssmq/git_replay.py`, `runs/v5_exp10_real_git_replay`)**: Executes live `git init`, `git merge --no-edit`, `python3 -B -m unittest`, and live `ASTStackCausalRanker` inside SSMQ's recovery loop with zero synthetic oracle or attribution proxy — achieving **0 real Git-tree escapes** (`10/10` passing final `main` suites), **17.1% fewer real `unittest` runs** (`10.2` vs `12.3`), and **75.0% fewer bisection splits** (`1.2` vs `4.8`).
4. **Bridge-Safe `DepAwareOutcomeTree`, Exact Paired Fixed-Density Recall, & Decoupled Risk Controls (`src/ssmq/simulator.py`)**: Enforces strict pairwise cone-disjointness across active lanes under bridge arrivals, anchors per-seed conflict counts without replacement across $\rho \in [0\%, 100\%]$ plus $P_c=0$, and adds a zero-risk-correlation sensitivity suite (`runs/v5_exp9_decoupled_sens`, `risk_correlation=0.0`).
5. **Strict ICSE `10+2` Double-Anonymous Manuscript & Deterministic Verification**: Explicit `[10pt,conference]{IEEEtran}` with default IEEEtran float spacing, after-conclusion `Data Availability`, 11 SHA-256-sealed v5 suites (`7,440` simulation records + `86` executed blame batches + `20` real-Git replays, `0` escapes), 141 pure-letter macros, and 33/33 verified peer-reviewed references.

## Verifying and Reproducing All v5 Results

```bash
# 1. Run all 25 unit tests (strict warnings-as-errors)
python3 -W error -m unittest discover -s tests -p "test_*.py" -v

# 2. Verify all 11 sealed v5 manifests, zero escapes, hygiene, and zero output drift
python3 eval/audit.py --verify-only

# 3. Re-execute all 11 suites into a fresh non-overwriting output directory
python3 eval/harness.py --runs-dir runs_repro_check
```
