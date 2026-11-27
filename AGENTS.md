# Research Paper Factory — Hard Policy Rules (`CLAUDE.md` / `AGENTS.md`)

> Adopted from industry best practices: `data-to-paper` (data chaining), `Agentic Scientist` (sealed final evaluation), `PaperOrchestra` (no fabricated experiments in revision), `ARIS` (cross-model peer review), `latex-paper-skills` (planned/placeholder/verified execution contracts), Anthropic multi-agent research (file-based handoff), and IEEE/ACM author ethics policies.

## Non-Negotiable Redlines (Any Agent Violating These Must Stop Immediately)

1. **Single Data Lineage (Zero Hand-Typed Numbers)**:
   Every empirical number in `paper/main.tex` and `paper/sections/*.tex` MUST be referenced via a LaTeX macro defined in `paper/generated/numbers.tex`, which is generated deterministically by `auditor.py` from `results/results.json`.
2. **Sealed Runs Only**:
   `results/results.json` may ONLY aggregate runs under `runs/<run_id>/` that contain a valid `manifest.json` with: `run_id`, `timestamp_utc`, `git_commit`, `harness_sha256`, `dataset_sha256`, `command`, `seeds`, and `artifact_sha256`. Runs without a valid manifest do not exist.
3. **Zero Memory Citations**:
   Every `\cite{...}` key MUST exist in `literature/refs.bib`, and every entry in `literature/refs.bib` MUST carry provenance from a live scholarly API (Crossref DOI, OpenAlex ID, Semantic Scholar ID, DBLP key, or arXiv ID) verified by `verifier.py`. Preprint ratio is tracked and flagged if >25%.
4. **No Fabricated or Sneaked Experiments During Revision**:
   Reviewers and Writers may NEVER invent numbers or silently run unapproved experiments to appease a simulated reviewer score. Additional experiments require opening a new tracked `run_id` approved at Gate 3.
5. **Frozen Evaluation Harness**:
   After Gate 1 (`G1_PLAN_FREEZE`), neither `eval/harness.py` nor metric definitions may be edited without explicit human approval and a new version bump.
6. **Mandatory AI Provenance Audit (`AI_USAGE.md`)**:
   Every AI-assisted literature query, code generation, analysis script, drafting, or review step MUST append a timestamped row to `AI_USAGE.md` (`date | provider/model | role | purpose | touched_files`). The paper's IEEE/ACM AI Disclosure statement is generated directly from `AI_USAGE.md`.
7. **Cross-Model Review Independence**:
   The `Reviewer` agent MUST use a different model family than the `Writer` agent.
8. **Compliance & IP Isolation**:
   Use ONLY public open-source repositories, public benchmarks, and personal accounts/hardware. Never include proprietary employer code, internal metrics, or confidential data.

## Four Human Decision Gates

| Gate | Trigger | Verification Criteria |
| :--- | :--- | :--- |
| **G1: Plan & Eval Freeze** | After `Scout` + `Coordinator` produce `plan.md`, `refs.bib`, `evidence.md` | Contribution statement, RQs, baseline list, target venue, frozen harness spec |
| **G2: Sealed Results Audit** | After `Runner` + `Auditor` produce `runs/`, `results.json`, `numbers.tex`, `audit_report.md` | 0 auditor blocking errors, A/A noise check, 95% CIs, statistical significance tests |
| **G3: Cross-Model Review** | After `Writer` + `Verifier` + `Reviewer` produce draft, `citation_report.md`, `review_*.md` | Human decides accepted critiques; max 3 text-only revision rounds |
| **G4: Submission Compliance** | After `Typesetter` compiles IEEEtran PDF + generates disclosure from `AI_USAGE.md` | Clean compile, 0 undefined refs, IEEE PDF eXpress check, human submits |
