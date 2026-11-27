# AI Usage Audit Log

> Single source of truth for IEEE/ACM AI-generated content disclosure.

| Date (UTC) | Provider / Model | Pipeline Role | Purpose | Touched Artifacts |
| :--- | :--- | :--- | :--- | :--- |
| 2026-11-27 00:50 | `antigravity/gemini-4-neon` | Coordinator | Scaffolded topic repository 'semantic-speculative-merge-queue' from research-paper-factory | CLAUDE.md, AGENTS.md, pipeline.config.json, state/pipeline_state.json |
| 2026-11-27 00:51 | `antigravity/gemini-4-neon` | Scout | Queried OpenAlex/Crossref APIs and verified 25 citations for SSMQ | literature/refs.bib, literature/evidence.md, literature/survey_matrix.json, reports/citation_report.md |
| 2026-11-27 01:01 | `codex/gpt-6.2-sol` | Architect | Implemented pure-Python SSMQ discrete-event simulator, 9 strategies, and 12 unit tests | src/ssmq/*.py, tests/test_ssmq.py |
| 2026-11-27 01:01 | `antigravity/gemini-4-neon` | Runner | Executed 6 sealed multi-seed experiment suites (20 seeds x 3 real OSS repos) with SHA-256 manifests | eval/harness.py, runs/*/manifest.json, runs/*/metrics.json |
| 2026-11-27 01:04 | `antigravity/gemini-4-neon` | Writer (Pre-Claude-Reset Fallback) | Drafted IEEEtran manuscript strictly referencing deterministic numbers.tex macros and API-verified refs.bib | paper/main.tex, paper/sections/*.tex |
| 2026-11-27 01:10 | `codex/gpt-6.2-sol` | Reviewer (v1 PC Audit) | Conducted independent ACM SIGSOFT Empirical Standards peer review (score 3/10) separating text-only fixes from Gate 3 proposals | reports/review_codex.md |
| 2026-11-27 01:10 | `antigravity/gemini-4-neon` | Writer (Gate 3 Text Revision) | Applied text-only scoping fixes from Codex v1 PC review | paper/main.tex, paper/sections/*.tex, eval/analyze_traces.py, eval/audit.py |
| 2026-11-27 01:44 | `codex/gpt-6.2-sol` | Architect (v2 Upgrade) | Upgraded simulator to v2 with decoupled off-graph conflict oracle, cumulative mainline safety, outcome-tree baseline, and false-rejection metrics | src/ssmq/models.py, src/ssmq/simulator.py, tests/test_ssmq.py |
| 2026-11-27 01:44 | `antigravity/gemini-4-neon` | Architect + Writer (v2 Bench & Revision) | Built 48-batch real executed Python blame micro-benchmark, ran 8 sealed v2 experiments, paired Wilcoxon+Holm auditor, and revised manuscript | src/ssmq/blame_bench.py, tests/test_blame_bench.py, eval/harness.py, eval/audit.py, paper/*.tex |
| 2026-11-27 02:55 | `antigravity/gemini-4-neon` | Architect + Writer (v3 Polish) | Expanded micro-benchmark to 64 batches, added 6 Crossref-verified seminal citations (33 total), built 6 TikZ/PGFPlots vector figures, and expanded formal methods | src/ssmq/blame_bench.py, eval/harness.py, eval/audit.py, paper/figures/*.tex, paper/refs.bib, paper/sections/*.tex |
| 2026-11-27 03:18 | `antigravity/gemini-4-carbon` | Writer (v3 Copy-Editing) | Copy-edited abstract and Sections I--VIII of v3 manuscript and added formal Proof Sketch to Proposition 3 while preserving 120 macros and 33 citations | paper/main.tex, paper/sections/01..08.tex |
| 2026-11-27 03:36 | `codex/gpt-6.2-sol` | Reviewer (v3 Blind PC Audit) | Conducted independent ICSE/FSE/EuroSys/TSE blind review of v3 (4/10 ICSE/TSE, 3/10 EuroSys), identifying micro-bench sibling-function gap, PGFPlots error-bar bug, fixed-density recall, and dependency-aware SubmitQueue baseline | reports/review_codex_v3.md |
