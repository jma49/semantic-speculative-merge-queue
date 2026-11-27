# Semantic Speculative Merge Queues (SSMQ)

> **Research Topic #1** instantiated by [`jma49/research-paper-factory`](https://github.com/jma49/research-paper-factory)  
> *Semantic Speculative Merge Queues: Graph-Disjoint Batching and Agentic Fault Attribution for High-Velocity CI*

## Overview

AI coding agents are increasing pull-request (PR) arrival velocity by $3\times$–$10\times$, causing conventional FIFO batching and linear speculative merge trains to suffer **speculative invalidation storms**. **SSMQ** introduces three mechanisms inside a strict zero-escaped-defect mainline safety envelope:

1. **Graph-Disjoint Risk-Stratified Batching** (`src/ssmq/graph.py`, `src/ssmq/simulator.py`): Learns file co-change and module-proximity dependency cones strictly from chronological warm-up history and packs semantically orthogonal low-risk PRs into fast-lane batches while isolating overlapping/high-risk changes.
2. **Beta-Binomial Flaky-Tolerant Selective Speculation**: Uses an online conjugate Beta-Binomial flakiness posterior to distinguish transient suite flakiness from deterministic regressions.
3. **Fail-Safe 1-Step Causal Blame Attribution**: Prioritizes Top-1 culprit eviction + parallel remainder verification before falling back deterministically to binary bisection.

## Verified Experimental Lineage (Zero Hand-Typed Numbers)

All empirical claims in `paper/main.tex` trace deterministically through SHA-256-sealed manifests:

```
data/traces/*.json (600 real merged PRs: kubernetes, TypeScript, next.js)
  -> eval/harness.py -> runs/<run_id>/{manifest.json, metrics.json} (6 runs, 3,480 sealed records)
  -> eval/audit.py (Zero LLM) -> results/results.json + paper/generated/numbers.tex (61 macros)
  -> paper/main.tex (27/27 Crossref/OpenAlex-verified peer-reviewed citations, 0% preprints)
```

### Headline Audited Results (5× AI-Agent Velocity, 5% Flaky Rate, 20 Seeds × 3 Real Repos)

| Strategy | Throughput (PR/h) | P50 Lat (m) | P95 Lat (m) | CI Cost (m/PR) | Invalid. (%) | Blame Rounds | Escaped Defects |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `SerialQueue` | 4.37 | 680.5 | 1426.4 | 13.74 | 0.0% | 0.00 | 0 |
| `StaticBatchBisect` | 22.08 | 34.3 | 78.4 | 12.56 | 0.0% | 1.80 | 0 |
| `SpeculativeLinear` | 7.57 | 343.1 | 746.5 | 21.36 | 31.6% | 0.89 | 0 |
| `UberSubmitQueue` | 6.27 | 474.2 | 984.0 | 12.59 | 0.1% | 0.72 | 0 |
| `FlakeAwareBatch` | 18.55 | 53.5 | 137.8 | 16.90 | 2.3% | 4.24 | 0 |
| **`SSMQ` (Ours)** | **23.81** | **22.0** | **49.7** | **11.35** | **1.0%** | **0.72** | **0** |

See [`reports/audit_report.md`](reports/audit_report.md), [`reports/citation_report.md`](reports/citation_report.md), [`reports/claim_trace_report.md`](reports/claim_trace_report.md), and [`reports/review_codex.md`](reports/review_codex.md) for complete verification and cross-model review reports.

## Reproducing All Results

```bash
# 1. Run unit tests (zero external dependencies, Python 3.9+)
python3 -m unittest discover -s tests -p "test_*.py" -v

# 2. Re-run all 6 sealed experiment suites (3,480 simulation records)
python3 eval/harness.py

# 3. Run zero-LLM deterministic auditor (recomputes results.json, numbers.tex, tables, SVGs)
python3 eval/audit.py

# 4. Verify sealed manifests only (also enforced in GitHub Actions CI)
python3 eval/audit.py --verify-only
```

## Multi-Model Orchestration & Claude Post-Reset Pass

Configured in `pipeline.config.json`:
- **Active Now**: Antigravity (`gemini-4-neon`), Codex (`gpt-6.2-sol` / `gpt-6.2-astra`), Cursor (`grok-4.7`).
- **Scheduled Post-Reset (`2026-11-28T15:00:00-08:00`, Saturday PM PST)**: Claude (`claude-opus-5-5`) automatically unlocks as primary academic `Writer` and secondary `Reviewer` for the Gate 3 prose polish and final adversarial pass.

All AI actions are audited in [`AI_USAGE.md`](AI_USAGE.md) and compiled automatically into [`paper/sections/ai_disclosure.tex`](paper/sections/ai_disclosure.tex).
