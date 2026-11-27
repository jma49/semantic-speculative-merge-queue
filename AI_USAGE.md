# AI Usage Audit Log

> Single source of truth for IEEE/ACM AI-generated content disclosure.

| Date (UTC) | Provider / Model | Pipeline Role | Purpose | Touched Artifacts |
| :--- | :--- | :--- | :--- | :--- |
| 2026-11-27 00:50 | `antigravity/gemini-4-neon` | Coordinator | Scaffolded topic repository 'semantic-speculative-merge-queue' from research-paper-factory | CLAUDE.md, AGENTS.md, pipeline.config.json, state/pipeline_state.json |
| 2026-11-27 00:51 | `antigravity/gemini-4-neon` | Scout | Queried OpenAlex/Crossref APIs and verified 25 citations for SSMQ | literature/refs.bib, literature/evidence.md, literature/survey_matrix.json, reports/citation_report.md |
| 2026-11-27 01:01 | `codex/gpt-6.2-sol` | Architect | Implemented pure-Python SSMQ discrete-event simulator, 9 strategies, and 12 unit tests | src/ssmq/*.py, tests/test_ssmq.py |
| 2026-11-27 01:01 | `antigravity/gemini-4-neon` | Runner | Executed 6 sealed multi-seed experiment suites (20 seeds x 3 real OSS repos) with SHA-256 manifests | eval/harness.py, runs/*/manifest.json, runs/*/metrics.json |
| 2026-11-27 01:04 | `antigravity/gemini-4-neon` | Writer (Pre-Claude-Reset Fallback) | Drafted IEEEtran manuscript (paper/main.tex + sections/*.tex) strictly referencing deterministic generated/numbers.tex macros and API-verified refs.bib | paper/main.tex, paper/sections/*.tex |
| 2026-11-27 01:10 | `codex/gpt-6.2-sol` | Reviewer (Cross-Model PC Audit) | Conducted independent ACM SIGSOFT Empirical Standards peer review (score 3/10) separating text-only fixes from Gate 3 new-run proposals | reports/review_codex.md |
| 2026-11-27 01:10 | `antigravity/gemini-4-neon` | Writer (Gate 3 Text-Only Revision Round 1) | Applied text-only scoping fixes from Codex PC review (safety/attribution scoping, SubmitQueue proxy disclosure, flaky acceptance trade-off, Table 1 caption, citation report reconciliation) | paper/main.tex, paper/sections/03_evaluation_setup.tex, paper/sections/06_discussion_threats.tex, eval/analyze_traces.py, eval/audit.py, reports/citation_report.md |
