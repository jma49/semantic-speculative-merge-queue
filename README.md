# Semantic Speculative Merge Queues (SSMQ) — v2 Artifact

> **Research Topic #1** instantiated by [`jma49/research-paper-factory`](https://github.com/jma49/research-paper-factory)  
> *Semantic Speculative Merge Queues: Graph-Disjoint Batching and Causal Fault Attribution for High-Velocity CI*

## What Changed in v2 (Addressing 100% of Codex v1 PC Review Critiques)

1. **Non-Circular Off-Graph Conflict Oracle** (`src/ssmq/simulator.py`, `runs/v2_exp5_graph_recall`): Decoupled oracle conflict graph $G^*$ from scheduler warm-up graph $\hat{G}$ via `graph_recall` $\in [0.40, 1.00]$ plus a Zero-Conflict Control ($P_c=0$). Even when **60% of true semantic conflicts are latent off-graph edges**, SSMQ still cuts P95 latency by **25.2%** vs. static batching (`111.2` vs `148.6 min`).
2. **Real Executable Python AST + Traceback Causal Blame Micro-Benchmark** (`src/ssmq/blame_bench.py`, `runs/v2_exp0_blame_microbench`): Executed **48 real failing Python multi-PR batches** (24 functional regressions + 24 true pairwise semantic conflicts verified to pass individually and fail combined) -> **`ASTStackCausalRanker` achieves 91.7% Top-1 / 100.0% Top-2 / 0.958 MRR in 2.29 ms** (vs 29.2% Random / 2.1% Churn), directly calibrating the macro simulator.
3. **Cumulative Mainline Safety + Faithful EuroSys '19 Outcome-Tree `UberSubmitQueue`**: Tracks cumulative mainline merges with persistent cross-PR conflicts (**0 escaped defects across 4,920 sealed v2 records**) and implements parallel pass/fail outcome-tree speculation ranked by expected merges per CI minute.
4. **Matched Paired Wilcoxon + Holm-Bonferroni Inference & Clean-PR False-Rejection Pareto** (`eval/audit.py`, `runs/v2_exp6_flaky_sweep`): Full `[95% CI]` tables, per-repository breakdown, Holm-adjusted $p$-values (`p < 0.0001`), and explicit clean-PR false-rejection accounting.
5. **Automated IEEEtran PDF Compilation**: GitHub Actions (`.github/workflows/compile-ieee-pdf.yml`) compiles `paper/main.tex` into downloadable `ssmq-ieeetran-pdf` on every push.

## Headline Audited v2 Results (5× Velocity, 5% Flaky, 80% Graph Recall, 20 Paired Seeds × 3 Real OSS Repos)

| Strategy | Throughput (PR/h) | P95 Lat (min) | CI Cost (m/PR) | Invalid. (%) | False Rej. (%) | Cumulative Escapes |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `SerialQueue` | 3.63 | 1408.8 | 16.65 | 0.0% | 4.22% | 0 |
| `StaticBatchBisect` | 16.05 | 121.9 | 19.78 | 0.9% | 1.96% | 0 |
| `SpeculativeLinear` | 5.37 | 899.8 | 30.98 | 40.3% | 2.13% | 0 |
| `UberSubmitQueue` (EuroSys '19 Tree) | 10.27 | 342.7 | 43.42 | 75.2% | 1.73% | 0 |
| `FlakeAwareBatch` | 12.57 | 235.5 | 29.92 | 1.9% | 0.12% | 0 |
| **`SSMQ` (Ours)** | **17.77** | **79.5** | **17.06** | **1.5%** | **1.64%** | **0** |

## Reproducing All v2 Results

```bash
python3 -W error -m unittest discover -s tests -p "test_*.py" -v
python3 eval/harness.py
python3 eval/audit.py
python3 eval/audit.py --verify-only
```
