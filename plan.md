# Research Plan & Frozen Evaluation Specification (`plan.md`)

- **Topic**: Semantic Speculative Merge Queues: Graph-Disjoint Batching and Agentic Fault Attribution for High-Velocity CI
- **Target Venues**: ICSE / FSE / ASE / EuroSys (IEEEtran two-column conference format, 10 pages + refs or 8-page workshop track)
- **Status**: Ready for Gate 1 (`G1_PLAN_FREEZE`)
- **Compliance**: 100% public open-source GitHub traces (`kubernetes/kubernetes`, `microsoft/TypeScript`, `vercel/next.js`), personal GitHub account (`jma49`), zero proprietary data.

---

## 1. One-Sentence Contribution Statement

We propose **SSMQ (Semantic Speculative Merge Queue)**, a graph-aware, flaky-tolerant, and causal-blame-accelerated CI merge queue scheduler that replaces blind FIFO batching and $O(\log K)$ binary bisection with (1) dependency-graph-disjoint risk-stratified lane batching, (2) Beta-Binomial flaky-noise-aware selective speculation, and (3) $O(1)$-amortized 1-step causal fault attribution—preventing speculative invalidation storms under $3\times$–$10\times$ AI-coding-agent PR arrival surges.

---

## 2. Formal Problem Formulation

Consider a continuous stream of approved Pull Requests $\{p_1, p_2, \dots, p_N\}$ arriving at times $t_i$ into a mainline merge queue with $W$ parallel CI runner slots and per-build verification duration $T_{\text{CI}}$:

- Each PR $p_i$ modifies a set of files/modules $F(p_i)$ and induces a **semantic impact cone** $C(p_i) = F(p_i) \cup N_G(F(p_i))$, where $G=(V,E)$ is the repository co-change / module-import dependency graph learned from historical commits and package paths.
- Each PR $p_i$ carries:
  - an individual defect indicator $d_i \in \{0, 1\}$ (true regression probability $q_i$, correlated with churn, file risk, and blast radius),
  - a pairwise **semantic conflict** indicator $c_{ij} \in \{0, 1\}$ when two individually passing PRs $p_i, p_j$ with overlapping impact cones ($C(p_i) \cap C(p_j) \neq \emptyset$) interact adversarially when combined in the same speculative train,
  - a test-suite flaky failure probability $\phi \in [0, 0.15]$ per CI execution (non-deterministic build failure unrelated to code defects).
- **Failure Mode (Speculative Invalidation Storm)**:
  In a traditional speculative merge queue of window/batch size $K$, the probability that a batch survives cleanly is:
  $$P(\text{batch pass}) = (1 - \phi) \prod_{i=1}^{K}(1 - q_i) \prod_{1 \le i < j \le K}(1 - P(c_{ij}))$$
  Under AI-agent PR surges ($5\times$–$10\times$ arrival rate $\lambda$), higher defect/conflict density and flaky rate $\phi$ cause $P(\text{batch pass})$ to collapse, triggering cascading speculative aborts and recursive $O(\log K)$ bisection storms that saturate $W$ runners and explode P95 merge latency.

---

## 3. Research Questions (RQs)

- **RQ1 (End-to-End Throughput, Latency & Compute Efficiency)**: How does SSMQ compare against industrial and academic merge queue baselines (`SerialQueue`, `StaticBatchBisect`, `SpeculativeLinear`, `UberSubmitQueue`, `FlakeAwareBatch`) across real OSS PR traces (`kubernetes/kubernetes`, `microsoft/TypeScript`, `vercel/next.js`) under $1\times$ to $10\times$ PR arrival velocity?
- **RQ2 (Ablation of SSMQ Components)**: What is the marginal contribution of each of SSMQ's three mechanisms—(a) Graph-Disjoint Risk-Aware Batching, (b) Flaky-Tolerant Adaptive Speculation, and (c) 1-Step Causal Fault Attribution—to throughput, P95 latency, CI compute savings, and blame rounds?
- **RQ3 (Robustness Under Flaky Noise & Imperfect Agentic Attribution)**: How sensitive is SSMQ to flaky test rates $\phi \in \{0\%, 2\%, 5\%, 10\%\}$ and imperfect 1-step causal blame Top-1 accuracy $p_{\text{blame}} \in [0.50, 0.95]$? Where is the break-even threshold where causal blame strictly dominates binary bisection?
- **RQ4 (A/A Noise Floor & Statistical Validity)**: What is the run-to-run simulation variance across seeds (A/A noise floor), and are all reported improvements statistically significant with 95% bootstrap confidence intervals and large Cliff's $\delta$ effect sizes?

---

## 4. Baselines & SSMQ Variants (Frozen for Gate 1)

| Strategy | Description | Reference |
| :--- | :--- | :--- |
| `SerialQueue` | Strict FIFO one-PR-at-a-time verification | Naive mainline protection |
| `StaticBatchBisect` | Fixed batch size $K$, binary bisection ($O(\log K)$) on failure | Bors / GitHub Merge Queue |
| `SpeculativeLinear` | Linear speculative merge train ($K$ concurrent builds) with full downstream invalidation on failure | GitLab Merge Trains / Mergify |
| `UberSubmitQueue` | Speculative tree/batch with failure-probability risk gating | Ananthanarayanan et al. (EuroSys '19) |
| `FlakeAwareBatch` | Batching + flaky-rerun deflaking + Bayesian/noisy bisection | Henderson et al. (ICST '23) / Fallahzadeh et al. (FSE '23) |
| **`SSMQ` (Ours)** | Graph-disjoint risk-stratified multi-lane batching + Beta-Binomial flaky-aware selective speculation + 1-step causal blame attribution | This work |
| `SSMQ-NoGraph` | Ablation: disables graph-disjoint risk batching (reverts to FIFO batching) | Ablation |
| `SSMQ-NoFlaky` | Ablation: disables flaky-tolerant selective rerun (treats every failure as deterministic regression) | Ablation |
| `SSMQ-NoBlame` | Ablation: disables 1-step causal blame (reverts to binary bisection) | Ablation |

---

## 5. Frozen Evaluation Metrics

1. **Throughput** ($\text{PRs merged / simulated hour}$)
2. **P50 & P95 Merge Latency** (minutes from PR enqueue to mainline merge for clean PRs)
3. **CI Compute Cost per Merged PR** (total CI runner-minutes consumed divided by successfully merged PRs — lower is better)
4. **Speculative Invalidation Rate** (fraction of started CI runs aborted/wasted due to upstream failure or flaky reset)
5. **Mean Blame Rounds per Faulty Batch** (CI verification rounds needed to evict culprit PR(s) and merge innocent PRs)
6. **Mainline Safety (Escaped Defect Rate)** (must remain 0.0% — zero broken PRs merged to mainline!)

---

## 6. ACM SIGSOFT Empirical Standards Compliance Checklist

- [x] **Real-world traces**: 600 real merged PRs across 3 diverse large-scale OSS repositories (Go/K8s, TypeScript compiler, Next.js full-stack framework).
- [x] **Statistical rigor**: Multi-seed Monte Carlo evaluation ($n=20$ seeds per configuration), 95% bootstrap confidence intervals, Cliff's $\delta$ effect sizes, and explicit A/A noise floor check.
- [x] **Reproducibility**: Zero-dependency Python 3.9+ discrete-event simulator, SHA-256 sealed manifests (`runs/<run_id>/manifest.json`), automated CI verification workflow.
- [x] **Threats to Validity**: Explicitly analyzed (construct, internal, external, conclusion validity) including calibration of synthetic defect/semantic-conflict injection over real file-touch traces.
