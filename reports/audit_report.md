# Deterministic Audit & Paired Inference Report v4 (`reports/audit_report.md`)

- **Audit Verdict**: **PASS (0 Blocking Errors, Full Harness+Dataset+Artifact SHA-256 + Non-Drift Verified)**
- **Verified Sealed v4 Runs**: 9
- **Total Sealed Simulation Records**: 6660 + 80 real executed Python unittest batches
- **Cumulative Mainline Escaped Defects**: `0` (0.0%)
- **Generated LaTeX Macros**: `124` in `paper/generated/numbers.tex`
- **Manuscript Hygiene Warnings**: `0`

## 80-Batch Real Executed Blame Benchmark (`v4_exp0_blame_microbench`)
- `ASTStackCausalRanker`: **98.8% Top-1**, **93.8% Functional Sibling Top-1**, **98.8% Executed LOO Pass**, MRR **0.994**, mean **2.27 ms** (max 3.12 ms).
- `FileTriviaFilteredRanker` (strong same-info baseline): **70.0% Top-1**, **18.8% Functional Sibling Top-1**, **70.0% Executed LOO Pass**, MRR **0.815**.

## Main Benchmark (`v4_exp2_main_bench`)

| Strategy | Tput (PR/h) | P95 (min) | Cost (m/PR) | FR% | Rej (D/P/F) | RootSz |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **SerialQueue** | 3.75 | 1413.9 | 16.11 | 4.36% | 11.8/19.8/6.0 | 1.00 |
| **StaticBatchBisect** | 16.92 | 113.8 | 18.25 | 2.01% | 11.8/20.2/2.8 | 2.72 |
| **SpeculativeLinear** | 5.82 | 846.8 | 28.63 | 2.12% | 11.8/20.0/2.9 | 2.75 |
| **FIFOOutcomeTree** | 11.05 | 319.4 | 39.90 | 1.78% | 11.8/20.0/2.5 | 2.43 |
| **DepAwareOutcomeTree** | 11.75 | 283.5 | 36.95 | 2.19% | 11.8/20.0/3.0 | 2.03 |
| **FlakeAwareBatch** | 13.44 | 215.8 | 27.34 | 0.12% | 11.8/20.6/0.2 | 2.74 |
| **SSMQ** | 18.18 | 81.0 | 16.75 | 1.17% | 11.8/20.2/1.6 | 1.44 |

## Fixed-Density Graph Recall (`v4_exp5_graph_recall`)

| Regime | Realized Recall | Total Conflicts | Static P95 | DepAwareTree P95 | SSMQ P95 |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `no_conflict` | 100.0% | 0 | 43.7 | 140.6 | **50.9** |
| `recall_0.40` | 39.6% | 1079 | 110.6 | 286.2 | **90.1** |
| `recall_0.60` | 59.8% | 1075 | 114.4 | 289.2 | **87.7** |
| `recall_0.80` | 80.1% | 1075 | 113.8 | 283.5 | **81.0** |
| `recall_1.00` | 100.0% | 1075 | 112.1 | 277.7 | **74.7** |

## 2x2 Factorial Blame Regime (`v4_exp8_blame_regime`, Holm-Adjusted)

| Cell | Static P95 | NoBlame P95 | SSMQ P95 | Δ% [Holm p] | FailRootSz | Multi-Culprit % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `def8_k8` | 113.8 | 81.8 | **81.0** | -1.1% (`p=0.2242`) | 1.62 | 5.4% |
| `def16_k8` | 126.0 | 74.1 | **73.1** | -1.3% (`p=0.1184`) | 1.59 | 6.7% |
| `def8_k12` | 123.6 | 82.1 | **81.2** | -1.1% (`p=0.2242`) | 1.62 | 5.5% |
| `def16_k12` | 136.2 | 74.1 | **73.3** | -1.1% (`p=0.1210`) | 1.59 | 6.7% |
