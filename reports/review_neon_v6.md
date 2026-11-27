# Blind IEEE/ACM SE Program Committee Review (Round 1)

**Manuscript:** *Semantic Speculative Merge Queues: When to Separate Changes and When to Retest* ([ssmq-paper.pdf](file:///Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/ssmq-paper.pdf))  
**Supplement Reviewed:** [ssmq-review.zip](file:///Users/jonson/.t3/scratch/2026-11-27-https-ieee-uy2z-pages-grok-70aa9ca6/ssmq-review.zip) (extracted to an isolated temporary directory; no repository files modified)

---

## Overall Score & Scoped Readiness

- **Overall Recommendation / Score:** **2 / 5 — Reject (Substantial Redesign Required)** (at a top-tier IEEE/ACM SE conference such as ICSE, FSE, or ASE)
- **Reviewer Confidence:** **5 / 5** (full manuscript audit, offline verifier execution, source inspection, and targeted counter-experiments run against the extracted supplement)
- **Scoped Readiness:**
  - **Artifact & Reproducibility Readiness:** **Strong.** `python3 eval/verify_v6.py --verify-only` passes offline (verifying `checksums.json`, 600 pinned GraphQL/REST PR trace records, all 7 sealed run manifests, and exact byte-for-byte regeneration of all 8 paper/results exports), and `python3 -m unittest discover -s tests` passes all 28 unit tests (13.5s).
  - **Scientific / Claims Readiness:** **Not ready for acceptance.** While the paper's writing is unusually disciplined and transparent about many limitations, direct inspection and stress-testing of the simulator and executable experiments reveal **two foundational validity/feasibility failures** (one in the simulator's commit-safety model and one in the closest baseline / contribution attribution) plus **major confounds in the Git replay and baseline comparisons**. Addressing these requires redesigning the scheduler's concurrency/recheck mechanism and re-running the core evaluations—beyond a minor revision.

---

## Brief Summary & Strengths

The paper investigates two decisions in speculative merge queues—how to pack PRs using historical co-change/directory cones plus risk stratification, and how to recover from failures via a Beta-Binomial flaky gate and 1-step AST/traceback suspect ranking—evaluated across three separated layers: (1) 600 public merged PRs from Kubernetes, TypeScript, and Next.js, (2) a paired discrete-event simulator, and (3) small executable Git/Python/QuixBugs fixtures.

**Genuine strengths:**
1. **Epistemic discipline:** The authors explicitly separate observational trace statistics, simulation under declared fault assumptions, and small executable fixtures, and commendably report negative sensitivity results (notably the +8.3 min directory-proxy penalty in Table III).
2. **Auditability:** The supplement is cleanly structured, self-contained offline, and strictly verifies trace membership (including full file-list pagination and REST/GraphQL cross-checking) and deterministic table regeneration.
3. **Clear problem framing:** Distinguishing *pre-execution separation* from *post-execution verification authority* (stale-pass invalidation) is a crisp conceptual contribution to merge-queue literature.

---

## Prioritized Actionable Criticisms (with Evidence)

### [P0 — Critical Blocker] 1. Central Feasibility Contradiction: The Simulator's Main Results Rely on an Impossible Commit-Time Oracle That Violates the Paper's Own Safety Thesis on 94.5% of Commits

- **Claim challenged:** The paper argues throughout (Abstract, Sec. I p. 1, Sec. V-C p. 6, Sec. VII p. 7) that *"Authority to commit comes from verification of the resulting state"* and *"A deployable queue needs a check on the combined state and an atomic branch update conditioned on the tested base."* Yet Table II reports SSMQ achieving 66.5 min P95 and 19.2 runner-min/merge across $W=8$ parallel workers—far faster than speculative tree/DAG policies (311.3–628.1 min).
- **Evidence from the artifact:**
  - In Algorithm 2 line 2 and `src/ssmq/simulator.py:841`, SSMQ (along with `DisjointBatchRetry` and `StaticBatchBisect`) dispatches up to 8 **unstacked** independent batches concurrently against snapshot $M_0$. When a batch passes, it checks:
    `if self.truth.persistent_culprits(job.members, self.merged_mainline - run.mainline):`
    and **only** forces a stale-pass recheck if this **omniscient ground-truth oracle** reports a persistent conflict.
  - Instrumenting `_resolve_batch` on the main simulation workload (5 seeds $\times$ 3 repos): out of **1,445 SSMQ commits, 1,365 (94.5%) commit onto a stale mainline ($M \neq M_0$) without re-testing**, while the oracle triggers only **23 rechecks** (~1.5/run).
  - Replacing that impossible oracle with the exact-tree verification rule that the authors themselves implement in `src/ssmq/git_replay.py:478` (`merged_mainline != run.mainline`, i.e., re-merge and re-test whenever mainline moved) causes SSMQ to **collapse completely** (3 seeds $\times$ 3 repos, `v6_sim_main` config):
    - **SSMQ:** P95 explodes **10.9$\times$** (**66.5 $\to$ 725.6 min**), CI cost explodes **3.9$\times$** (**19.2 $\to$ 75.7 runner-min/merge**, **459.1 stale rechecks/run**).
    - **DisjointBatchRetry:** P95 = **763.6 min**, CI/merge = **77.0 min** (464.8 stale rechecks/run).
    - **StaticBatchBisect:** P95 = **515.7 min**, CI/merge = **62.0 min** (311.4 stale rechecks/run).
  - Under deployable exact-tree verification, **SSMQ is 210 min *slower* than `StaticBatchBisect`**, and *slower* than `DepAwareOutcomeTree` (311.3 min) and `DepTreeRetry` (477.8 min), because SSMQ's tiny batches (~1.18 PRs) advance mainline more frequently, maximizing stale-base invalidations across concurrent workers.
  - Worse, Table II is **structurally asymmetric**: `DepAwareOutcomeTree`, `DepTreeRetry`, and `PerPRDAGRetry` actually build speculative stacked prefixes (`job.prefix`) and pay massive cancellation costs (`2,195.6` and `2,793.1` canceled CI minutes in `results/sim_v6.json`) to preserve exact-tree/prefix safety, whereas SSMQ pays `0.0` canceled CI minutes only because it uses an unrealizable oracle to skip rechecks.
- **Actionable fix:** Remove the omniscient `HasPersistentConflict` commit skip. Either (a) give SSMQ an actual deployable speculative stacking / merge-train mechanism over disjoint or serialized batches so concurrent workers test the true combined state (and pay genuine invalidation costs when earlier batches fail), or (b) evaluate all policies under the *same* realistic commit-authority rule (exact-tree recheck when `main != M_0`, or conservative cone-overlap recheck with explicit escape-rate accounting for off-graph persistent conflicts).

---

### [P0 — Critical Blocker] 2. Closest Baseline & Contribution Collapse: Trivial `ParallelSingleton+Retry` Beats SSMQ on Every Repo, and SSMQ's −3.6 min Gain Comes from Unintended Batch-Size Collapse, Not Risk Lanes or Blame

- **Claim challenged:** SSMQ's scheduling contribution over `DisjointBatchRetry` (70.1 $\to$ 66.5 min, $\Delta = -3.6$ min in Table II) is attributed jointly to protected lanes and suspect-guided recovery (Sec. IV-A, V-A), and SSMQ is framed as a batching scheduler with $K_{\max}=8$.
- **Evidence from the artifact:**
  1. **Unfiltered co-change cones collapse batching to near-singletons:** `DependencyGraph` (`src/ssmq/graph.py:27-63`) builds 1-hop co-change + 2-level directory cones with *no file-count filter* (warm-up PRs touch up to 95, 279, and 281 files in Kubernetes, TypeScript, and Next.js). This inflates mean cone sizes to **148.3, 356.1, and 176.4 files** (max **1,605 files** in TypeScript) and pairwise graph edge densities to **68.5% (Kubernetes), 92.4% (TypeScript), and 59.1% (Next.js)** (mean **73.3%**). Consequently, in `v6_sim_main`, SSMQ's `mean_root_batch_size` is only **1.24** (Kubernetes), **1.04** (TypeScript), and **1.25** (Next.js)—overall **1.18 PRs/batch** (`DisjointBatchRetry`: **1.22**).
  2. **The main workload is underloaded, so a trivial singleton baseline beats SSMQ everywhere:** At `velocity_multiplier=5.0` (30 PRs/hr) with $W=8$ workers and 12-min CI, singleton capacity is **40 PRs/hr** (> 30 PRs/hr; runner utilization is 78.6%). Running a trivial **`ParallelSingleton+Retry`** baseline (`max_batch_size=1` with the same adaptive retry rule, **zero graph, zero cones, zero risk lanes, zero blame**) across all 20 seeds $\times$ 3 repos yields:
     - **ParallelSingleton+Retry:** P95 = **63.8 min** (k8s **96.4**, ts **51.7**, next **43.2**), Accepted = **108.3**, Merges/hr = **19.9**, CI-min/merge = **19.6**.
     - **SSMQ:** P95 = **66.5 min** (k8s **100.0**, ts **56.3**, next **43.4**), Accepted = **108.6**, Merges/hr = **19.9**, CI-min/merge = **19.2**.
     SSMQ loses to naive parallel singletons by **+2.7 min P95 overall** and on **all three repositories**.
  3. **Omitted component ablations show risk stratification and blame add essentially nothing in simulation:** `src/ssmq/simulator.py:34-51` already implements `SSMQ-NoBlame` and `SSMQ-NoRisk`, yet `eval/harness_v6.py` omits them from all sealed suites. Running them on the exact main suite (20 seeds $\times$ 3 repos) yields:
     - `DisjointBatchRetry`: **70.1 min** (`root_bs = 1.22`)
     - `SSMQ-NoBlame` (no blame ranker): **66.8 min** (`root_bs = 1.18`) — blame adds only **−0.3 min**!
     - `SSMQ-NoRisk` (no $\theta_{\text{risk}}=0.55$ risk lane, keeping only overlap promotion + blame): **66.3 min** (`root_bs = 1.19`) — *outperforms* full SSMQ (**66.5 min**)!
     - Even in a truly saturated high-load regime (`vm=15.0`, 90 PRs/hr, 10 seeds $\times$ 3 repos), `SSMQ` (**163.7 min**) barely differs from `SSMQ-NoBlame` (**164.1 min**) and `SSMQ-NoRisk` (**164.2 min**), while `root_bs` remains stuck at **1.37**.
     Thus, the −3.6 min gain over `DisjointBatchRetry` is almost entirely an incidental side effect of Algorithm 1's overlap-promotion rule shrinking batch size from 1.22 to 1.18—not risk stratification or causal attribution.
- **Actionable fix:**
  1. Include `ParallelSingleton+Retry`, `SSMQ-NoBlame`, `SSMQ-NoRisk`, and `SSMQ-NoGraph` in the main evaluation, and test **genuinely saturated arrival regimes** ($\rho > 1$ for singletons, where batching is actually needed for stability).
  2. Fix the cone-explosion problem (e.g., filter mega-refactor PRs by file count, use weighted/thresholded co-change edges or finer directory/file granularity, and tune edge density) so disjoint batching actually forms multi-PR batches ($K \ge 3\text{–}4$) where blame recovery and risk stratification have meaningful room to operate.

---

### [P1 — Major] 3. Oracle Parameter Leakage in Scheduler Priors and Pathological Tree/DAG Baselines

- **Claim challenged:** The paper claims *"Leak-Free Warm-Up Dependency Graph and Risk Scoring"* (Sec. III-A) and presents Table II comparing SSMQ against `Lane outcome tree`, `Lane tree + retry`, and `Per-PR DAG + retry` (with gaps of −244.8 to −561.6 min).
- **Evidence from the artifact:**
  1. **Oracle parameter leakage:** While warm-up normalization avoids peeking at evaluation PR features, `src/ssmq/simulator.py:311` and `:559` directly pass the ground-truth simulation parameters `config.base_defect_rate` (`0.08`) and `config.semantic_conflict_prob` (`0.18`) into SSMQ's flaky gate (`_warmup_scheduler_priors` and `_failure_risk`). Real queue operators do not know the true defect rate or pair conflict probability a priori. Furthermore, in `v6_sim_main`, ground-truth unary defects are sampled linearly from SSMQ's exact risk formula (`risk_correlation = 1.0`). When `risk_correlation = 0` and labels are derived from evaluation directories (`directory_proxy`), SSMQ loses to `DisjointBatchRetry` (50.0 vs 49.7 min) and loses by **+8.3 min** to `SSMQ-NoCone` (41.7 min); under `directory_no_persistence`, SSMQ (47.6 min) even loses to `StaticBatchBisect` (46.0 min).
  2. **Pathological baseline implementations in Table II:**
     - `DepTreeRetry` (477.8 min, **166.5 min slower** than `DepAwareOutcomeTree` 311.3 min) discards *both* `head.on_pass` and `head.on_fail` speculative subtrees on every retry (`src/ssmq/simulator.py:783-786`), even though `on_pass` remains valid if the retried run passes—inflating canceled CI work to `2,793.1` min/run.
     - `PerPRDAGRetry` (628.1 min; **773.0 min** in TypeScript) never batches (`root_bs = 1.00`) and blocks dispatch whenever `len(ancestors) >= max_spec_depth` (`simulator.py:475`), where `ancestors` is the *transitive closure* over the 73%–92%-dense cone graph—artificially starving workers far below $W=8$.
- **Actionable fix:** Estimate $\hat{q}$ and $\hat{p}_c$ online (or test sensitivity to miscalibrated priors, e.g., $2\times$ off). Fix `DepTreeRetry` to preserve `on_pass` during transient retries, bound `PerPRDAGRetry` by dependency *depth* rather than transitive ancestor count (and allow batching or fairer graph density), and report baseline mechanics transparently.

---

### [P1 — Major] 4. Construct Validity & Confounds in the Executable Git Replay (Table IV) and Attribution Ablation

- **Claim challenged:** Section V-C and Table IV claim that at fixed file-disjoint packing, AST ranking reduces test invocations from 126 to 112 (and splits from 39 to 32) compared to a strict nested file-level ablation.
- **Evidence from the artifact:**
  1. **Uncounterbalanced lexicographical PR-ID bias in Table IV:** Whereas `src/ssmq/blame_bench.py:625,640` carefully counterbalances PR IDs (`pr_a*`/`pr_z*`) in Strata 2–3, `src/ssmq/git_replay.py` reuses raw PR IDs in which **all clean PRs start with `pr_c*`, all defects with `pr_d*`, and all semantic conflicts with `pr_s*`** (`"pr_c" < "pr_d" < "pr_s"`). Whenever `FileTrivia` ties (e.g., on `test_checkout_end_to_end` in Seeds 0, 1, 4, 5, 7), ascending PR-ID tie-breaking systematically picks a clean `pr_c*` PR first (`6/20` blame events in `Disjoint+FileTrivia`, `8/19` in `FIFO+FileTrivia`). Reversing the tie-breaker to descending PR ID changes Table IV substantially:
     - `Disjoint+FileTrivia`: **126 $\to$ 118 tests** (hits 7/20 $\to$ 9/20); `Disjoint+ASTStack`: **112 $\to$ 108 tests** (hits 12/20 $\to$ 13/20) — **~29% of the reported 14-test gap disappears**.
     - `FIFO+FileTrivia`: **133 $\to$ 119 tests** (−14 tests!); `FIFO+ASTStack`: **121 $\to$ 107 tests** (−14 tests!).
  2. **Asymmetric weighting in the "nested" file ablation (`blame_bench.py:466-475`):** Under `Disjoint` packing, no two PRs in a batch even touch the same file (`git_replay.py:256`), so intra-file function disambiguation cannot occur! Inspection of the per-seed traces shows `Disjoint+ASTStack` beats `Disjoint+FileTrivia` largely because `func_spec` gives a **1.5$\times$ direct-callee bonus** (`bonus = 1.5 if fn in direct else 1.0` at line 474, favoring `pkg/checkout.py:process_order` when `test_checkout_end_to_end` fails), whereas `file_spec` (`fw = 1.0 / len(rfiles)`, line 468) omits any direct-file bonus even though `D(t)` already identifies the directly called file. This confounds *file vs. function granularity* with *direct vs. indirect call weighting*.
  3. **Prototype–algorithm mismatch and toy scale:** `git_replay.py` executes strictly serially (1 worker, no stale rechecks inside the factorial replay), uses exact file overlap instead of Algorithm 1's cones/protected lanes, omits Algorithm 2's flaky gate, ignores Algorithm 3's `s2` bisection hint (`git_replay.py:331-354`), and evaluates only 10 seeds of 12-PR streams on a single 13-function hand-authored Python toy repository (`BASE_REPO`). Similarly, `eval/quixbugs_v6.py` tests only 6 single-file algorithm functions with artificial `pass` distractors.
- **Actionable fix:** Counterbalance or randomize PR IDs in `git_replay.py` (paired over both polarities, as done in `blame_bench.py` and `quixbugs_v6.py`). Give `file_spec` the same `1.5x` direct-callee file weight so the nested ablation truly isolates function-level granularity. Implement a multi-worker Git replay that exercises the actual SSMQ policy (protected lanes, stale recheck, `s2` hint, injected flakiness) on at least one realistic open-source Python repository with real test suites and commit history (or multi-module benchmarks beyond a 13-function synthetic repo).

---

### [P2 — Moderate] 5. Unreported False-Rejection Penalty of Cone Exclusion and Flaky-Gate / AST Limitations

- **Evidence from the artifact:**
  1. **Hidden false-rejection regression:** Across *every* flaky simulation condition (`v6_sim_main`, `recall_0.0`–`recall_1.0`, `directory_proxy`, `directory_no_persistence`, `uniform_pairs`), `SSMQ` suffers a **1.4$\times$–2.0$\times$ higher `false_rejection_rate` than `SSMQ-NoCone`** (e.g., **1.53% vs. 0.95%** in main; **1.99% vs. 1.12%** in `directory_proxy`; **1.89% vs. 0.92%** in `recall_0.0`), and rejects more clean PRs (`128.9` vs `130.0` accepted in `directory_proxy`). Why? Shrinking batches to singletons causes high-risk clean PRs to fail the singleton gate (`P(transient | fail_1) < 0.25`) on a single flake and get immediately rejected, whereas larger batches split first. Tables II and III omit `false_rejection_rate` entirely.
  2. **Flaky-gate censoring bias:** Algorithm 2 updates `Beta(alpha, beta)` only on confirmed passing jobs and censors all split or rejected runs (`simulator.py:830-835`). When flakes occur in multi-PR batches (where `tau_B = 0.50` forces immediate splitting rather than retry) or cause singleton rejections, those flakes never increment `alpha`, systematically underestimating the suite's true flake rate over time.
  3. **Language/AST mismatch:** All 600 workload traces come from Go and TypeScript repositories, yet Algorithm 3 is restricted to Python synchronous bare-name `FunctionDef` nodes—excluding methods (`obj.method()`), `async def`, decorators/class bodies, module-level code, imports/aliases, and dynamic dispatch—making its 0.85/0.95 simulator accuracy assumption (`SimConfig.blame_top1_acc/top2_acc`) ungrounded for the trace repositories.
- **Actionable fix:** Report `false_rejection_rate` and `mean_root_batch_size` in Tables II and III; analyze the singleton-rejection interaction of cone exclusion + $\tau_1$; include a sensitivity sweep over `blame_top1_acc` (e.g., 0.3–0.9) and miscalibrated flake priors; and clearly scope or extend the AST ranker beyond bare-name Python functions (e.g., method calls and class-qualified names via `ast.ClassDef` + `ast.Attribute`).

---

### [P3 — Minor] 6. Reporting, Instrumentation, and Editorial Issues

1. **Unreported sealed experiment:** Section IV-C describes *"The authored Python benchmark"* (`v6_exp0_blame_microbench`, 86 records in the supplement with `ast_top1=0.9884`, `file_trivia_top1=0.7558`), and Section V-D alludes to *"pooled Top-1 accuracy on an authored fixture,"* yet Section V never reports any results from this benchmark. Either present its stratified results concisely (with appropriate caveats) or remove it from Section IV-C.
2. **Per-repository transparency:** Table II pools all three repositories with equal weights, masking that SSMQ's gain over `DisjointBatchRetry` is −8.3 min on Kubernetes, −2.4 min on Next.js, and **+0.01 min (zero)** on TypeScript (`results/sim_v6.json:118-122`), and that `directory_proxy`'s +8.3 min penalty is +21.5 min on Kubernetes vs. +1.2/+2.2 min on Next.js/TypeScript. Include a per-repository breakdown (or compact summary row/footnote).
3. **Minor simulator accounting inconsistencies:**
   - In `src/ssmq/simulator.py:90-92`, `GroundTruth.culprits` adds only `second` (not both endpoints) for an intra-batch conflict `(first, second)`, contradicting `blame_bench.py` (where either endpoint counts) and Section VI's text (*"either endpoint may clear the failure"*).
   - In `src/ssmq/simulator.py:845-846`, `blame_one_step_hits` increments whenever a `blame_remainder` passes—including after a pure flaky failure with zero culprits.
4. **LaTeX copy-paste typo in Data Availability (p. 7):** `eval/verify_v6.py –verify-only` renders an en-dash (`–`) instead of double hyphen (`--verify-only`); wrap the command in `\texttt{...}` with `{-}{-}` so readers can execute it verbatim.

---

## Summary of Required Actions for Resubmission

1. **Eliminate the commit-time oracle** in the simulator and design/evaluate a genuinely deployable concurrency + exact-tree verification mechanism across all policies on equal footing.
2. **Fix cone explosion** (filter mega-commits / tune graph construction), **evaluate in a saturated regime** where batching is actually needed, and **beat `ParallelSingleton+Retry`** alongside deconfounded ablations (`SSMQ-NoBlame`, `SSMQ-NoRisk`, `SSMQ-NoCone`).
3. **Deconfound the baselines and executable evaluation:** fix `DepTreeRetry`'s `on_pass` discard bug and `PerPRDAGRetry`'s transitive-closure throttling; remove oracle defect/conflict rates from the scheduler; counterbalance PR IDs and equalize direct-callee weighting in `git_replay.py`/`blame_bench.py`; and report `false_rejection_rate` and per-repository outcomes.
