# Semantic Speculative Merge Queues (SSMQ) — v4 Artifact

> *Semantic Speculative Merge Queues: Graph-Disjoint Batching and Causal Fault Attribution for High-Velocity CI*

## What Is Included in the v4 Artifact

1. **Executable 80-Batch Python Blame Micro-Benchmark** (`src/ssmq/blame_bench.py`, `runs/v4_exp0_blame_microbench`): Executes **80 real failing Python `unittest` batches** across four strata (24 disjoint regressions, 16 comment co-file distractors, **16 functional sibling-function co-file distractors** with balanced churn and counterbalanced PR IDs, and 24 verified pairwise semantic conflicts) with zero silent patch skips and **real executed leave-one-out (`B \ {s_1}`) recovery verification**. Evaluates 5 live-timed deterministic rankers including a same-information `FileTriviaFilteredRanker` baseline vs. `ASTStackCausalRanker` (**98.8% Top-1 / 100.0% Top-2 / 0.994 MRR / 98.8% Executed LOO Recovery** in **2.27 ms mean**, **93.8% vs. 18.8%** on functional sibling-function co-file distractors).
2. **Fixed-Density Off-Graph Conflict Oracle & Leak-Free Warm-Up Priors** (`src/ssmq/graph.py`, `src/ssmq/simulator.py`, `runs/v4_exp5_graph_recall`): Decouples the ground-truth conflict oracle $G^*$ from the scheduler's warm-up graph $\hat{G}$ across $\rho \in [0.40, 1.00]$ while holding total conflict count constant (1,075--1,079 across 60 runs), alongside a Zero-Conflict Control ($P_c=0$). Scheduler priors scale strictly from warm-up mean risk (`warmup_mean_risk`) with zero evaluation-population leakage.
3. **13 Evaluated Schedulers & Deconfounded Ablations**: Includes `SerialQueue`, `StaticBatchBisect`, `SpeculativeLinear`, `FIFOOutcomeTree`, **`DepAwareOutcomeTree`** (EuroSys '19 conflict-graph speculation with independent commits for disjoint cones), `FlakeAwareBatch`, `SSMQ`, and six deconfounded ablations (`SSMQ-NoCone`, `SSMQ-NoRisk`, `SSMQ-NoGraph`, `SSMQ-NoFlaky`, `SSMQ-FixedRetry`, `SSMQ-NoBlame`).
4. **Instrumented $2\times 2$ Factorial Blame Analysis & Zero Cumulative Escapes** (`runs/v4_exp8_blame_regime`): Tracks realized root batch size, failed-root batch size, multi-culprit share, and exact Defective/Persistent/Flaky rejection counts across **9 SHA-256-sealed v4 suites (6,660 simulation records + 80 executed Python batches, 0 within-model cumulative escapes)**.
5. **ICSE `10+2` Double-Anonymous IEEEtran Manuscript & Automated Verification**: `eval/audit.py --verify-only` verifies manifest digests, record counts, zero escapes, manuscript hygiene, and exact byte-for-byte non-drift of `results/results.json` and `paper/generated/numbers.tex` (124 pure-letter macros, 33/33 Crossref-verified peer-reviewed citations).

## Headline Audited v4 Results (5× Velocity, 5% Flaky, 80% Graph Recall, 20 Paired Seeds × 3 Real OSS Repos)

| Strategy | Throughput (PR/h) | P95 Lat (min) | CI Cost (m/PR) | Invalid. (%) | False Rej. (%) | Rej. (D/P/F) | Escapes |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `SerialQueue` ($W=1$) | 3.75 | 1413.9 | 16.11 | 0.0% | 4.36% | 11.8/19.8/6.0 | 0 |
| `StaticBatchBisect` | 16.92 | 113.8 | 18.25 | 0.9% | 2.01% | 11.8/20.2/2.8 | 0 |
| `SpeculativeLinear` | 5.82 | 846.8 | 28.63 | 38.3% | 2.12% | 11.8/20.0/2.9 | 0 |
| `FIFOOutcomeTree` | 11.05 | 319.4 | 39.90 | 75.2% | 1.78% | 11.8/20.0/2.5 | 0 |
| `DepAwareOutcomeTree` | 11.75 | 283.5 | 36.95 | 71.5% | 2.19% | 11.8/20.0/3.0 | 0 |
| `FlakeAwareBatch` | 13.44 | 215.8 | 27.34 | 1.9% | 0.12% | 11.8/20.6/0.2 | 0 |
| **`SSMQ` (Ours)** | **18.18** | **81.0** | **16.75** | **2.0%** | **1.17%** | **11.8/20.2/1.6** | **0** |

## Verifying and Reproducing All v4 Results

```bash
# 1. Run all 20 unit tests (strict warnings-as-errors)
python3 -W error -m unittest discover -s tests -p "test_*.py" -v

# 2. Verify all 9 sealed v4 manifests, zero escapes, hygiene, and zero output drift
python3 eval/audit.py --verify-only

# 3. Re-execute all 9 suites into a fresh non-overwriting output directory (or pass --force)
python3 eval/harness.py --runs-dir runs_repro_check
```
