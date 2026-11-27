# Deterministic Audit & Paired Inference Report v5 (`reports/audit_report.md`)

- **Audit Verdict**: **PASS (0 Blocking Errors, Full Harness+Dataset+Artifact SHA-256 + Non-Drift Verified)**
- **Verified Sealed v5 Runs**: 11
- **Total Sealed Records**: 7440 simulation records + 86 real executed Python blame batches + 20 real Git-tree queue replays
- **Cumulative Escaped Defects**: `0` modeled / `0` real Git-tree (0.0%)
- **Generated LaTeX Macros**: `141` in `paper/generated/numbers.tex`
- **Manuscript Hygiene Warnings**: `0`

## Real Exact-Git-Tree Queue Replay (`v5_exp10_real_git_replay`)
- `StaticBatchBisect`: 0 escapes, 9.0 merges/stream, **12.3 real unittest runs** (4.8 bisection splits).
- `SSMQ` (live `ASTStackCausalRanker`): 0 escapes, 9.0 merges/stream, **10.2 real unittest runs** (1.2 bisection splits, 1.2/2.0 live 1-step blame hits).

## Decoupled Risk Sensitivity (`v5_exp9_decoupled_sens`, `risk_correlation=0.0`)
- Uniform defects at 80% recall: SSMQ **67.7 min** vs Static **96.0 min**.
- Uniform defects at 40% recall: SSMQ **74.4 min** vs Static **90.9 min**.

## 86-Batch Symmetrically Counterbalanced Blame Benchmark (`v5_exp0_blame_microbench`)
- `ASTStackCausalRanker`: **98.8% Top-1**, **95.5% 50/50 Sibling Top-1**, **98.8% Executed LOO Pass**, MRR **0.994**, mean **2.27 ms** (max 3.50 ms).
- `FileTriviaNestedRanker` (strict nested file ablation): **75.6% Top-1**, **50.0% 50/50 Sibling Top-1**, **75.6% Executed LOO Pass**, MRR **0.845**.

## Main Benchmark (`v5_exp2_main_bench`)

| Strategy | Tput (PR/h) | P95 (min) | Cost (m/PR) | FR% | Rej (D/P/F) | RootSz |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **SerialQueue** | 3.53 | 1432.6 | 17.05 | 4.08% | 12.1/26.2/5.6 | 1.00 |
| **StaticBatchBisect** | 18.68 | 95.0 | 20.42 | 2.52% | 12.1/26.8/3.5 | 2.21 |
| **SpeculativeLinear** | 4.98 | 958.3 | 32.87 | 2.16% | 12.1/26.8/3.0 | 2.71 |
| **FIFOOutcomeTree** | 10.51 | 329.8 | 45.60 | 1.89% | 12.1/26.8/2.6 | 2.09 |
| **DepAwareOutcomeTree** | 10.82 | 313.9 | 44.42 | 2.18% | 12.1/26.7/3.0 | 1.96 |
| **FlakeAwareBatch** | 13.38 | 215.3 | 32.12 | 0.18% | 12.1/27.4/0.2 | 2.84 |
| **SSMQ** | 19.81 | 66.0 | 19.21 | 1.44% | 12.1/27.1/2.0 | 1.20 |

## Fixed-Density Graph Recall (`v5_exp5_graph_recall`)

| Regime | Realized Recall | Total Conflicts | Static P95 | DepAwareTree P95 | SSMQ P95 |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `no_conflict` | 100.0% | 0 | 36.7 | 132.3 | **43.9** |
| `recall_0.00` | 0.0% | 1114 | 77.0 | 308.7 | **78.1** |
| `recall_0.40` | 42.2% | 1363 | 91.4 | 321.3 | **73.1** |
| `recall_0.60` | 60.0% | 1451 | 92.9 | 322.1 | **70.4** |
| `recall_0.80` | 80.0% | 1451 | 95.0 | 313.9 | **66.0** |
| `recall_1.00` | 100.0% | 1451 | 94.7 | 292.8 | **60.8** |

## 2x2 Factorial Blame Regime (`v5_exp8_blame_regime`, Holm-Adjusted)

| Cell | Static P95 | NoBlame P95 | SSMQ P95 | Δ% [Holm p] | FailRootSz | Multi-Culprit % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `def8_k8` | 95.0 | 66.0 | **66.0** | +0.0% (`p=1.0000`) | 1.29 | 3.6% |
| `def16_k8` | 102.6 | 56.5 | **56.5** | -0.2% (`p=1.0000`) | 1.25 | 3.8% |
| `def8_k12` | 105.8 | 66.0 | **66.0** | +0.0% (`p=1.0000`) | 1.29 | 3.6% |
| `def16_k12` | 114.9 | 56.5 | **56.5** | -0.2% (`p=1.0000`) | 1.25 | 3.8% |
