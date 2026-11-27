# SSMQ 外部评审意见逐项核查、一手文献核验与 Batch 2 实证升级方案（对标 v7）

- **核查基准版本**：`semantic-speculative-merge-queue` **v7**（commit `a0b108d`，`Require tested-state equality and report counterbalanced queue experiments`）
- **同步只读核查仓库**：`agentic-canary-verification`、`hostile-pr-gate-bench`（仅静态读取源码、数据目录与论文稿，未执行任何恶意载荷或构造攻击工作流）
- **核查日期**：2026-11-27
- **性质说明**：本报告为 staged response **Batch 1**（证据核查与实验设计），**未修改论文手稿**，亦未修改任何仓库隐私设置或对外联系第三方；对外部评审中的主观工作量/时间估算仅标注为“未实测的主观判断”，不作为事实依据。

---

## 一、执行摘要（可直接同步课题组）

1. **核心版本错位判断（最关键发现）**：外部评审意见实际上是针对 **历史版本 v4/v5**（如 `e2e9674` / `70af492`）所写，**而非当前 v7（`a0b108d`）**。多项硬证据证实该评审未看到 v6/v7 改动：
   - 评审称“25 个测试通过”→ 实为 **v5** 状态（`reports/review_codex_v5.md:33`）；**v7 已有 30 个测试**（`tests/test_ssmq.py` 25 个 + `tests/test_blame_bench.py` 5 个）。
   - 评审称“现有独立评审只覆盖到 v4，v5 还没人审过”→ 仓库内已有 `reports/review_codex_v5.md`（v5）、`reports/review_neon_v6.md`（v6 盲审，给出 2/5 Reject 并触发 v7 彻底重构）及 `reports/review_codex_v7.md`（v7 独立复审）。
   - 评审指出的两项“事实错误”（对比表将 GitHub Merge Queue 写成二分定位；以 `fallahzadeh2023` 作为 Bors/GitHub 出处）存在于 **v4/v5 的 Table VIII**（`git show e2e9674:paper/sections/06_related_work.tex`、`70af492:paper/sections/06_related_work.tex`），**v7 已整表删除并移除相关误引**。
   - 评审称“需补引 Henderson 2023、Ziftci 2017、Jungwirth ICSME 2025，且有 PRediBatch ICST 2026 撞车风险”→ 这 **4 篇均已收录于 v7 `paper/refs.bib` 并在 `06_related_work.tex` 正文引用**（其中 `jungwirth2026predictive` 即 PRediBatch）。
   - 评审建议“收窄主线、降级 AST 归因、报告负面结果”→ **v7 已将全文叙事重构为严格 exact-tree 约束下的负面系统实验研究**（SSMQ 相比 `SSMQ-NoCone` 慢 `+105.2 min`、误拒率升至 `5.62%`，且文件互斥打包下 AST 相对权重匹配文件级排序仅差 2 次测试：`220` vs `222`）。

2. **去伪存真后的真问题（对 v7 依然成立的硬伤）**：
   - **P0 — 缺乏真实队列入队时间、真实 CI 失败/重跑与未合入 PR 历史**：v7 虽然引入了 600 条真实 GitHub 合并 PR（Kubernetes/TypeScript/Next.js 各 200 条，经 REST/GraphQL 交叉核对零遗漏、零截断），但**仅含合入后文件路径与合并时间戳**，缺少真实入队时刻、真实 CI 耗时/失败结论、被踢出队列的 PR 及真实多 PR 失败堆栈；`98.8%` Top-1/LOO 仍出自 86 批自写 Python 夹具（`paper/generated/tables/git_v7.tex:21`）。
   - **P0 — 缺少关键可复现基线对照**：v7 已引用 `PRediBatch (ICST 2026)`，但**尚未实证对比** PRediBatch 风格风险分批、`Beheshtian (TSE 2022)` 批处理（如 `BatchStop4`/`RiskBatch`）、`Aviator/Trunk` 风格目录受影响目标分组、忠实的 `GitHub Merge Queue` 分组剔除重建策略及 `Bors` 风格批二分策略。
   - **P1 — 仍需补全 8 篇高质量一手文献引用**：`Beheshtian 2022 (TSE)`、`Kamath 2025 (EMSE)`、`Fallahzadeh 2025 (EMSE, 2024 accepted)`、`SafeRevert 2024 (ICST)`、`FlakeFlagger 2021 (ICSE)`、`Apple Kowalczyk 2020 (ICSE-SEIP)`、`Meta PFS (2020)`、`Zuul` 官方文档，以及 BIC/LLM 归因相关工作（`Fonte 2023`、`SemBIC 2025`、`Marini 2024`、`arXiv:2511.18854`）与 2026 年最新直接相关预印本（`arXiv:2610.04779` MERGEGYM）。
   - **P1 — 外部评审提供的两组“AI 动机数据”存在严重分母/口径陷阱，不可原样照搬**：
     - **Xu 2026 (`arXiv:2607.04697`)**：`40.2%` 和 `41.7%` 数字本身存在，但 `41.7%` 是**仅占共同活跃 PR 对 0.5% 的跨 Agent 子集**上的 **Git 文本冲突率**（非语义 CI 冲突；占绝大多数的同 Agent 对文本冲突率为 `19.8%`）。
     - **MSR 2026 (`doi:10.1145/3793302.3793585`)**：`79.15%` 数字本身存在，但该研究样本的 **11,771 条 PR 中本身就有 7,619 条（64.73%）是 Agent PR**（并非全网 GitHub 随机抽样）。脱离 `64.73%` 基准率笼统宣称“CI 失败里 79% 来自 Agent PR”构成典型基准率谬误。
   - **P1 — 另外两个旁支仓库（`agentic-canary-verification`、`hostile-pr-gate-bench`）**：外部评审核心判断**成立**——两仓库 `data/traces/` 均为空，代码里均**无任何真实 LLM API 调用**，核心检测/注入防御信号为参数化硬编码或哈希伯努利抽样（详见第五节精细区分）。
   - **P2 — 双盲匿名与人类独立评审**：v7 已有通过离线校验的匿名复现包 `ssmq-review.zip` 且正文为 `Anonymous Author(s)`，但公开 GitHub 仓库（`jma49/semantic-speculative-merge-queue`）若与目标会议双盲规则冲突，需由课题组在投稿前按具体 CFP 规则自行决定是否设私或撤除关联，且当前所有内部审稿均为 AI 模型完成，正式投稿前建议安排真人同行复核。

3. **Batch 2 立即可行的实证突破（已通过 `gh` API 实测验证）**：
   - 我们在本机已实测确认：**SSMQ 现有三库之一的 `microsoft/TypeScript`（`main`）与 `rust-lang/cargo`（`master`）均已开启原生 GitHub Merge Queue**，公开可抓取数百至上千条真实 `merge_group` Actions 运行记录（TypeScript `690` 条、Cargo `1,033` 条）以及 PR 时间线中的 **`AddedToMergeQueueEvent`（真实入队时刻）与 `RemovedFromMergeQueueEvent`（含 `merged` / `failed_checks` 真实踢出原因）**！结合 `kubernetes/kubernetes`（Prow/Tide `StatusContext` + `lgtm/approved` + `/retest`）和 `python/cpython` / `vercel/next.js`（真实 `CheckRun` 耗时与 `run_attempt` 重跑），**无需等待数月，Batch 2 现在即可执行“真实观察层 + 显式反事实层”分离的历史 PR/CI 队列回放与完整基线对比**。

---

## 二、外部评审逐条裁决总表（对标 SSMQ v7）

裁决分级说明：`已过时/v7已修复`、`部分成立/需限定口径`、`成立（v7仍存在）`、`不实/未核实`、`主观估算（非实测）`。

| 编号 | 外部评审原话/核心主张 | 对标 v7 裁决 | 关键证据路径 / 一手来源 | Batch 2/3 处理动作 |
| :---: | :--- | :--- | :--- | :--- |
| C1 | “方法纪律很好：脚本生成、SHA-256 校验、A/A 校准、Wilcoxon+Holm，25 个测试通过” | **部分过时（测试数与统计口径已更新）** | v7 已升级为 **30 个测试**（`tests/test_ssmq.py` 25 + `tests/test_blame_bench.py` 5），离线验真入口为 `eval/verify_v7.py --verify-only`；v7 主表改用 10,000 次配对种子区块 bootstrap 区间（`paper/sections/04_evaluation_setup.tex:24`），不再依赖旧 v4/v5 的 `eval/audit.py` A/A+Wilcoxon 叙事 | 保持 v7 封存纪律；Batch 2 新实验沿用同样 SHA-256 manifest + 配对 bootstrap 协议 |
| C2 | “没有真实数据，核心结论全部建立在自写的约 30 个 PR 的夹具上” | **以偏概全 / 部分成立** | **反驳部分**：v7 包含 **600 条真实 GitHub 合并 PR**（k8s/TS/next.js 各 200 条，`data/traces/fetch_manifest.json` 零遗漏零截断）+ 6 个上游 QuixBugs 缺陷（36 组配对用例，`eval/quixbugs_v7.py`）+ 120 次真实 Git 回放（`eval/git_v7.py`）+ 86 批 Python 归因夹具。<br>**成立部分**：600 条公开 PR 仅有合并后的文件列表与合并时间戳，**无真实入队时刻、无真实 CI 失败/时长、无被拒 PR**，主队列实验仍靠合成故障+高斯 12 min CI 驱动；Git 因子实验仍在 13 函数自写 Python 仓库上运行（`paper/sections/07_discussion_threats.tex:7-9`） | **Batch 2 最高优先级**：抓取真实 `AddedToMergeQueueEvent`/`RemovedFromMergeQueueEvent`/`merge_group` 与 `CheckRun` 历史做真实回放 |
| C3 | “离 ICST/ISSRE/QRS 差 1–2 个月，离 ICSE 差 3–5 个月” | **主观估算（非实测）** | 外部评审者的经验判断，无实测基准支撑 | **不作为事实引用**；按“缺什么证据补什么证据”分批推进 |
| C4 | “98.8% Top-1 归因准确率来自自写夹具，没有说服力；建议用 Kubernetes/Rust/CPython 回放” | **成立（数字仍在 v7 Table V；目标仓库选择需技术校准）** | `98.8%` 确仍在 v7 Table V（`paper/generated/tables/git_v7.tex:21`：86 批自写 Python 夹具 pooled LOO），尽管 v7 已明确标为 `Authored` 并补充 6 个 QuixBugs 缺陷（`12/12` vs `6/12`）。另据实测：`rust-lang/rust` 主库使用自研 `rust-lang/bors`（PR head 无 CheckRun），直接抓 GitHub CI 应优先选开启原生 Merge Queue 的 `rust-lang/cargo` 与 `microsoft/TypeScript`，辅以 `kubernetes/kubernetes`（Prow/Tide）和 `python/cpython`（GitHub Actions CheckRun） | Batch 2 区分“队列调度真实 CI 回放”与“归因实验真实性”；不在摘要/结论宣传 `98.8%` |
| C5 | “和 SubmitQueue 对比不忠实（v4 指出），需补 GitHub merge queue 和 Bors 真实行为基线” | **部分已修、基线仍缺** | v7 已删除不公平的 `DepAwareOutcomeTree`/`PerPRDAGRetry` 对比，仅保留单时间线 exact-tree 的 `FIFOOutcomeTree`（并明确声明不能代表 Uber 生产服务，`06_related_work.tex:3`）。但 v7 仍缺 **GitHub Merge Queue 风格（staged merge group + 失败剔除重建）**与 **Bors 风格（批处理 + 递归二分，且需区分历史 `bors-ng` 与现行 `rust-lang/bors` rollup）**基线 | Batch 2 增补清晰标注、参数公开的 `GitHubMQ-StagedGroup` 与 `Bors-BatchBisect` 基线 |
| C6 | “撞车风险：PRediBatch（ICST 2026，BMW 真实数据）与本文风险分层正面重叠；须补 PRediBatch 或 Beheshtian 2022、Aviator 目录受影响目标基线” | **引用已补，但基线撞车风险依然成立（P0/P1）** | v7 已在 `refs.bib:383` 与 `06_related_work.tex:5` 引用 PRediBatch（`jungwirth2026predictive`，`10.1109/icst69053.2026.00087`，确为 BMW 真实流水线数据仿真，降周转 8.3%、省算力 13.3%）。但 v7 **未实证对比** PRediBatch、Beheshtian 2022（TSE）或 Aviator/Trunk 目录目标基线，且未引用 Beheshtian 2022 | Batch 2 增补 `PRediBatch-RiskGroup`、`Beheshtian-BatchStop4`、`Aviator-DirAffected` 三个可复现建模基线，并显式区分“是否满足 exact-tree 验证” |
| C7 | “消融显示 p=1.0 时语法树归因贡献 +0.0%，建议降级为附带模块并如实报告负面结果，主线放依赖锥打包和不稳定测试门控” | **已过时（v7 更彻底地重构为主线负面结果）** | `+0.0% / p=1.0` 出自 v5 `Exp8`（`review_codex_v5.md:51`）。**v7 已经完成更彻底的科学转向**：在统一 exact-tree 规则下，**不仅是 AST 归因收益微弱（文件互斥下 220 vs 222），连“未堆叠依赖锥打包”本身也被证明是显著负收益（`+105.2 min` P95，误拒率 `5.62%` vs `4.01%`）**！外部评审建议把主线改写成正面宣传“Learned Dependency-Cone Batching”反而会**与 v7 的严格负结果矛盾**（除非 Batch 2 在过滤 mega-PR 或前缀堆叠结合下得到新证据） | **严禁回退 v7 的科学诚实性**；Batch 2 测试“过滤 mega-PR 的稀疏锥”及“锥/风险重排 + 单时间线前缀堆叠”，由数据决定正面或负向边界结论 |
| C8 | “现有独立评审只到 v4，v5 没人审；同套 AI 流水线写作评审需真人复核；公开 GitHub 仓库破坏双盲” | **前半过时，后半成立（合规/治理提醒）** | 仓库已有 v5（`review_codex_v5.md`）、v6（`review_neon_v6.md`）、v7（`review_codex_v7.md`）三轮独立复审；且已有离线匿名复现包 `ssmq-review.zip`、正文 `Anonymous Author(s)` 及 `ai_disclosure.tex`。但所有历史评审确为 AI 代理执行（无真人同行审稿记录），且公网存在 `jma49/semantic-speculative-merge-queue` 仓库 | Batch 1 不改隐私、不联系外部人员；列为投稿前人类决策清单（按目标会议双盲政策处理） |
| C9 | “创新点定位：SubmitQueue/Aviator/Trunk 做过依赖锥打包；Google/Apple/Meta PFS 做过概率不稳定模型；Fonte/SemBIC/2406.07084/2511.18854 覆盖 AST 归因” | **主体成立，局部需精确界定** | 核实 Aviator/Trunk 确实支持**非 Bazel 项目的目录级自定义受影响目标**；Apple ICSE-SEIP 2020 与 Meta PFS 确有成熟每测试概率模型；但 Fonte/SemBIC/2511.18854 面向**合入后历史提交链（BIC）**而非合入前无序批次，2406.07084/2511.18854 依赖 LLM 推理。同时 v7 自身定位已是“exact-tree 约束下打包粒度与重验失效权衡的实证系统研究”，并非宣称发明新概率模型或新 BIC 算法 | Batch 3 修订 Related Work 时精确界定与 Aviator/Trunk、Apple/Meta、Fonte/SemBIC/Marini 的边界，不夸大任何单点新意 |
| C10 | “补引清单：Zuul、Beheshtian 2022、Kamath 2025、Fallahzadeh 2024、Henderson 2023、SafeRevert 2024、FlakeFlagger、Ziftci 2017、Jungwirth ICSME 2025” | **9 篇中 3 篇已在 v7，6 篇确实缺失** | **v7 已引用**：`henderson2023flake`、`ziftci2017broke`、`jungwirth2025improving`（且已用 IEEE ICSME 2025 正式 DOI，而非评审所说的 arXiv）。<br>**v7 缺失**：Zuul、Beheshtian 2022（TSE）、Kamath 2025（EMSE）、Fallahzadeh 2025（EMSE，2024 录用）、SafeRevert 2024（ICST）、FlakeFlagger 2021（ICSE）——加上 Apple 2020、Meta PFS、Fonte 2023、SemBIC 2025、Marini 2024、MERGEGYM 2026 | Batch 3 统一补齐经 DOI/一手源核验的缺失文献（详见第三节） |
| C11 | “AI 动机数据：Xu 2026（arXiv 2607.04697）40.2%/41.7%；MSR 2026 79% CI 失败来自 Agent PR” | **数字存在但口径严重失真（需严格校正分母）** | 详见第三节第 1–2 项逐字核验：Xu 的 `41.7%` 仅限占比 `0.5%` 的跨 Agent 对且是**文本冲突**（同 Agent 对 `19.8%`）；MSR 2026 的 `79.15%` 是在 **64.73% 为 Agent PR** 的非均衡样本（7,619 Agent vs 4,152 Human）内统计 | 若在动机段引用，**必须同时披露完整分母、样本构成与“文本冲突 vs 语义失败”区别** |
| C12 | “事实错误：对比表把 GitHub merge queue 写成二分定位；用 fallahzadeh2023 作为 Bors/GitHub 出处” | **已过时（v7 已删除该表及误引）** | 该两处错误确实存在于 v4（`e2e9674:paper/sections/06_related_work.tex`）和部分残留在 v5（`70af492`），但在 **v7 中已完全删除**（全文 grep `Bors` / `GitHub Merge Queue` 零命中）。GitHub 官方文档证实其真实机制为“移除失败 PR 并重建后续 merge group” | v7 当前无此事实错误；Batch 2/3 若新增系统对照表，必须直接引用 GitHub 官方文档与 `bors-ng` / `rust-lang/bors` 准确机制 |
| C13 | “检索缺口：DBLP 报错、Semantic Scholar 限流；ISSTA/EuroSys/OSDI 近两年未逐篇翻” | **客观提醒（成立）** | 本轮通过 OpenAlex、arXiv API 与限定 Web 检索补扫了 2024–2026 文献，发现了外部评审未提及的 `arXiv:2610.04779`（MERGEGYM）、`arXiv:2610.28683`、`arXiv:2604.11977`（Uber GitFarm）及 2026 批测试能耗研究；但本轮同样遇到 OpenAlex/Crossref 局部 429 限流，详见第四节 | 投稿前由课题组对目标会议近三年目录做最终人工复扫 |
| C14 | “另外两篇问题更根本：AgenticCanary 信号写死无 LLM；HostilePR 用伯努利抽样代替 LLM” | **核心判断成立（HostilePR 需补充确定性契约细节）** | 经只读源码核查：ACV 确为 100% 合成参数模拟器、零真实数据、零 LLM；HPG 确定性契约（AST 扫描、SARIF 校验、YAML 策略锁、Exit 3、实体哈希）真实执行，但 LLM 检测与 Prompt 注入防御（`min(0.035, 0.05*chance)`）确为硬编码规则 + SHA-256 伯努利抽样、零 LLM API 调用 | 详见第五节；两篇另行立项接入真实系统/API，不与 SSMQ Batch 2 混排 |

---

## 三、关键一手文献、数字分母与系统事实核验

### 1. Xu et al. 2026（`arXiv:2607.04697`）—— `40.2%` 与 `41.7%` 逐字核验
- **一手出处**：George Xu, Arjun Subramanian, Nithilan Karthik, *"AI Agent Pull Requests on GitHub: Frequency, Structure, and Merge Conflict Rates"*, `arXiv:2607.04697v2`（2026-07-06 提交，v2 2026-07-07；7 页预印本，复现包 `10.5281/zenodo.21186464`）。
- **原文数据与精确分母**：
  - 数据集：`AIDev-pop`（**33,596 条 Agent PR，分布于 2,807 个仓库**）。
  - **`40.2%`**：在“精确时间重叠（exact temporal overlap）”口径下，**2,807 个含 Agent PR 的仓库中有 40.2%** 存在同时活跃的 Agent PR 对（这些同时活跃对覆盖了 AIDev-pop 中 **79.4%** 的 Agent PR；放宽至 1 周窗口则升至 `53.4%` 仓库、`95.0%` PR）。
  - **`41.7%` vs `19.8%`**：作者对 **747 对** 抽样共同活跃 PR（每库 1 对）重放真实 Git 三方合并，测量的是 **Git 文本合并冲突率（textual conflict）**，其中**跨 Agent 对（cross-agent）为 `41.7%`**，**同 Agent 对（intra-agent）为 `19.8%`**（95% 置信区间不重叠）。
  - **关键限定（外部评审遗漏）**：在全部共同活跃 PR 对中，**跨 Agent 对仅占 `0.5%`**（仅出现在 2,807 个仓库中的 **122 个，约 `4.3%`**）！且其测量的是**文本合并冲突**（其中 84.4% 发生在源码文件、近 42% 为 modify/delete 或 add/add 结构冲突），**不是 SSMQ 所聚焦的“文本合并通过但 CI 测试失败”的语义冲突**。
- **使用建议**：可作 AI 并发 PR 趋势背景，但**只能写成**“在 AIDev-pop 的 2,807 个仓库中，40.2% 出现精确时间重叠的 Agent PR；在 747 对抽样三方合并重放中，同 Agent 对文本冲突率为 19.8%，仅占共同活跃对 0.5% 的跨 Agent 子集文本冲突率升至 41.7%（预印本）”。

### 2. MSR 2026 `79.15%` 统计 —— 精确出处与分母核验
- **一手出处**：Moataz Chouchen, Jasem Khelifi, Mahi Begoug, Ali Ouni, Mohammed Sayagh, Mohamed Aymen Saied, *"On the Reliability of Agentic AI in Continuous Integration Pipelines"*, **MSR 2026 Mining Challenge**, pp. 837–841, ACM, DOI: `10.1145/3793302.3793585`。
- **原文数据与精确分母**（来自 OpenAlex 索引摘要逐字核对）：
  - 研究样本：**11,771 条 GitHub PR**，由 **7,619 条 Agent PR（占比 `64.73%`）** 与 **4,152 条人工 PR（占比 `35.27%`）** 构成（基于 MSR 2026 Mining Challenge 的 AIDev 数据集构建）。
  - 在该样本的 CI 失败-修复映射中：**AI Agent 引入了 `79.15%` 的 CI 失败**，但仅完成了 **`60.63%` 的对应修复**；不过 Agent 修复中位耗时为 `17.23 min`，快于人工的 `71.70 min`。
- **关键风险**：外部评审简述为“MSR 2026 发现 CI 失败里 79% 来自 Agent PR”，**遗漏了样本中 Agent PR 本身就占 64.73% 这一决定性分母前提**。若直接写入论文，审稿人极易指出基准率谬误（base-rate neglect）。正确写法必须同时给出 `7,619 / 11,771 (64.73%)` 样本占比，说明的是“在包含 64.7% Agent PR 的 MSR 2026 样本中，Agent 贡献了 79.15% 的 CI 失败引入但仅承担 60.63% 的修复”。

### 3. 故障归因 / BIC 相关工作范围核验（Fonte、SemBIC、`2406.07084`、`2511.18854`）
| 文献 | 精确出处 / DOI | 核心方法与适用场景 | 与 SSMQ 1-step AST/Traceback 排序的本质差异 |
| :--- | :--- | :--- | :--- |
| **Fonte** | Gabin An et al., ICSE 2023, `10.1109/icse48619.2023.00059` | **合入后（post-merge）历史提交链** BIC 定位：依赖 SBFL 覆盖频谱 + 历史提交投票 + 时间衰减 + 加权二分（130 个真实 bug，含 Defects4J） | 面向线性合入历史、需测试覆盖率/SBFL；SSMQ 面向**合入前无序 PR 批次**、零插桩纯静态 AST/堆栈，且 v7 已诚实界定其增量收益仅限于同文件不同函数冲突 |
| **SemBIC** | Xiao Chen et al., PACMSE Vol. 2 (FSE 2025), Art. FSE062, `10.1145/3715781` | **合入后**静态语义 BIC 排序：沿失败测试执行路径追踪连续历史版本间的细粒度语义变更（12 个开源项目 199 个 bug 中 Top-1 88/199 = 44.2%） | 同样面向历史版本演化链，非合并队列实时批拆分；其 44.2% 真实基准 Top-1 也反衬出 SSMQ 自写夹具 98.8% 不可外推至真实工业仓库 |
| **arXiv:2406.07084** | Leonardo Marini, Linus Gisslén, Alessandro Sestini (EA), CoG 2024 | **CI 批次失败归因**：用 LLM 关联错误日志与代码变更（EA 一年内部数据集 71% 准确率，排查省时最高 60%） | 场景最贴近“批次失败归因”，但依赖 LLM 推理成本与延迟；SSMQ 尝试无 LLM 轻量规则并发现其真实边界很窄 |
| **arXiv:2511.18854** | Yujing Wang, Weize Hong, Nov 2025（投稿 SCALCOM 2025） | **LLM 辅助 `git bisect`**（QLoRA 微调 DeepSeekCoderV2 + CoT），应对 flaky/非单调历史回归（成功率 `74.2% -> 80.6%`） | 面向历史 `git bisect` 且需微调 LLM，非合入前静态排序 |

### 4. 核心系统与批处理/不稳定测试文献核查表

| 对象/文献 | 一手核验结果（精确标题 / 会议期刊 / DOI 或官方源） | 在 SSMQ v7 中的当前状态 | 结论与后续动作 |
| :--- | :--- | :--- | :--- |
| **PRediBatch (ICST 2026)** | M. Jungwirth, M. Gruber, G. Fraser (BMW & Univ. Passau), *"Predictive Pull Request Batching to Accelerate Merge Pipelines in Continuous Integration at Scale"*, ICST 2026 Industry Track, pp. 659–670, `10.1109/icst69053.2026.00087`（复现包 Figshare `10.6084/m9.figshare.31271773`）。纯语言无关 PR 元数据 + CI 历史 ML 预测低风险 PR 分批，BMW 真实数据仿真降平均周转 **8.3%**、省算力 **13.3%** | **已在 `paper/refs.bib:383` 及 `06_related_work.tex:5` 引用**，但**未做实验对比** | **高优先级**：Batch 2 必须构建可复现的 `PRediBatch-RiskGroup` 基线做对照 |
| **Beheshtian 2022 (TSE)** | M. J. Beheshtian, A. H. Bavand, P. C. Rigby, *"Software Batch Testing to Save Build Test Resources and to Reduce Feedback Time"*, IEEE TSE 48(8):2784–2801, 2022（early access 2021）, `10.1109/TSE.2021.3070269`。在 Travis CI 开源项目评估 `Batch4`、`BatchBisect`、`BatchStop4` 及风险批处理 `RiskBatch` | **v7 缺失** | **必补引用 + Batch 2 基线**（`BatchStop4` / `RiskBatch`） |
| **Aviator / Trunk** | 官方文档确认：二者不仅支持 Bazel/Nx，亦支持通过目录规则或 `git diff --name-only` 自定义脚本上传 **affected targets**，在不重叠目标间并行合并队列、重叠目标间堆叠串行化 | **v7 未讨论、未对比** | **必补定位说明 + Batch 2 基线**（`Aviator-DirAffected`，显式区分 exact-tree 约束与假设受影响目标完备的非 exact-tree 并行） |
| **Zuul** | OpenStack / OpenInfra 官方文档（`zuul-ci.org/docs/zuul/latest/project-gating.html`, `pipeline.html`, `merger.html`）：首创依赖管线（dependent pipeline）**投机合并（speculative merging）**与跨仓库共享队列，前序失败即踢出并重建后序投机提交 | **v7 缺失** | **必补引用**（作为线性投机栈/合并列车的工业先驱） |
| **Bors（历史 vs 现行）** | ① 第三方托管服务 **`bors-ng`**（`bors.tech`，经典 FIFO 批处理 + 递归二分）已于 **2023 年 5 月正式弃用并下线公有托管**，转向 GitHub 原生 Merge Queue；② Rust 官方内部机器人 **`rust-lang/bors`**（Homu 继任者）**仍在积极维护并已用 Rust 重写**，主打人工/自动 rollup 与专属 CI 队列（而 `rust-lang/cargo` 已采用 GitHub 原生 Merge Queue） | v4/v5 曾误用 `fallahzadeh2023` 引用；**v7 已移除** | Batch 2 增加标注清晰的 `Bors-BatchBisect`（指明对应历史 `bors-ng` 批二分语义，区别于 `rust-lang/bors` rollup） |
| **GitHub Merge Queue (`merge_group`)** | GitHub 官方文档（*Managing a merge queue*）及本轮 `gh` API 实测确认：将待合并 PR 按顺序打包为临时 `gh-readonly-queue/...` merge group 运行必需检查；遇检查失败/超时/冲突时**自动移除该失败 PR 并重建其后方分组**，**不执行二分查找** | v4/v5 Table VIII 误写为二分并误引 `fallahzadeh2023`；**v7 已完全移除该错误** | Batch 2 增加忠实的 `GitHubMQ-StagedGroup` 基线，并用官方 docs 作参考 |
| **Apple (ICSE-SEIP 2020)** | E. Kowalczyk et al., *"Modeling and Ranking Flaky Tests at Apple"*, ICSE-SEIP 2020, `10.1145/3377813.3381370`（基于 flip rate 与 entropy 量化单测不稳定度并排序，在 Apple 两大型服务中降 44% flakiness、<1% 检出损失） | **v7 缺失** | **必补引用**（明确区分单测级长期概率模型与 SSMQ 队列级轻量在线启发式） |
| **Meta PFS (2020)** | M. Machalica, W. Chmiel, S. Swierc, R. Sakevych, *"Probabilistic flakiness: How do you test your tests?"*, Engineering at Meta, Dec 2020（基于 Stan 的贝叶斯后验分布 Probabilistic Flakiness Score，回答“有多不稳定”而非二元标签） | **v7 缺失** | **必补引用**（同上） |
| **Kamath 2025 (EMSE)** | D. M. Kamath, B. Adams, A. E. Hassan, *"Lightweight dynamic build batching algorithms for continuous integration"*, EMSE 30, Art. 51 (2025), `10.1007/s10664-024-10555-4`（28.7 万提交 / 50 个 Travis CI 项目，纯在线按上一批成败动态调批大小，中位省 4.75% 构建） | **v7 缺失** | **必补引用**（可作轻量在线动态批大小对照） |
| **Fallahzadeh 2025 (EMSE, 2024 acc.)** | E. Fallahzadeh, P. C. Rigby, B. Adams, *"Contrasting test selection, prioritization, and batch testing at scale: Large-scale empirical study on 285 million test results"*, EMSE 30, Art. 33 (2025; 2024-11 录用), `10.1007/s10664-024-10589-8` | **v7 缺失**（v7 仅引了 FSE 2023 版） | **必补引用** |
| **SafeRevert 2024** | T. A. D. Henderson, A. Kondareddy, S. Azad, E. Nickell (Google), *"SafeRevert: When Can Breaking Changes be Automatically Reverted?"*, ICST 2024 Industry Track, `10.1109/icst60714.2024.00043` | **v7 缺失** | **必补引用**（补充故障归因/安全自动回滚线） |
| **FlakeFlagger 2021** | A. Alshammari, C. Morris, M. L. Hilton, J. Bell, *"FlakeFlagger: Predicting Flakiness Without Rerunning Tests"*, ICSE 2021, `10.1109/icse43902.2021.00140` | **v7 缺失** | **必补引用** |
| **Henderson 2023 (ICST)** | T. A. D. Henderson et al., *"Flake Aware Culprit Finding"*, ICST 2023, `10.1109/icst57152.2023.00041` | **v7 已引用**（`refs.bib:295`, `06_related_work.tex:7`） | 无需补引（外部评审漏看） |
| **Ziftci 2017** | C. Ziftci & J. Reardon, *"Who broke the build? ..."*, ICSE-SEIP 2017, `10.1109/icse-seip.2017.13` | **v7 已引用**（`refs.bib:56`, `06_related_work.tex:7`） | 无需补引（外部评审漏看） |
| **Jungwirth ICSME 2025** | M. Jungwirth, M. Gruber, G. Fraser, *"Improving Merge Pipeline Throughput in Continuous Integration via Pull Request Prioritization"*, ICSME 2025, `10.1109/icsme64153.2025.00057` | **v7 已引用正式出版 DOI**（`refs.bib:23`, `06_related_work.tex:5`） | 无需补引（外部评审误称为 arXiv） |

---

## 四、2024–2026 顶会/预印本限定补扫与检索边界说明

### 1. 本轮检索范围与新发现（外部评审未覆盖的重要近作）
我们以 **2024–2026** 为窗口，组合 **OpenAlex API**、**arXiv API** 与 **限定 Web 检索**，覆盖 `merge queue`、`merge train`、`speculative merge`、`pull request batching`、`batch testing continuous integration`、`flaky culprit finding` 及顶会关键词（ICSE、FSE、ASE、ISSTA、ICST、ISSRE、MSR、EuroSys、OSDI），补充发现以下高度相关文献：

1. **MERGEGYM（2026-10，`arXiv:2610.04779`）**：Arjun Subramanian, George Xu, Nithilan Karthik, *"Asymmetric Repository Lineage Modeling and Verifier-Guided Coordination in Concurrent AI Coding Agents"*。
   - **极高相关性**：在 715 对分层血缘集（167 个文本冲突）中发现 **`47.3%`（79/167）的文本冲突发生在作者修改文件集合完全不相交的情况下**（源于三方合并相对共同 merge-base 的传递差异），且决策时门控在消除 91.7% 标注范围碰撞的同时带来 **65.0% makespan 膨胀**！这与 SSMQ v7 的核心洞察（“廉价文件/锥代理不能取代可执行树验证，且去重排会显著拉长队列延迟”）高度呼应，极具讨论价值。
2. **Manji（2026-10，`arXiv:2610.28683`）**：Adam Manji, *"Who Approved This? Coding Agents, Human Accounts, and Merge-Gate Evidence at a Fintech Startup"*（786 条合并 PR 的精确提交版本 merge-gate 审计，说明 exact-revision 验证在 Agent 时代的合规与安全必要性）。
3. **GitFarm（2026-04，`arXiv:2604.11977`，Uber）**：Preetam Dwivedi et al., *"GitFarm: Git as a Service for Large-Scale Monorepos"*（Uber 继 SubmitQueue 之后针对超大规模 monorepo Git 检出/合并瓶颈的底层系统工作）。
4. **批测试能耗实证（2026，FSE 相关 / Zenodo `10.5281/zenodo.18507065`）**：*"The Energy Impact of Batch Testing in Continuous Integration: An Empirical Study of Static and Dynamic Batching Strategies"*（评估 `BatchStop4` 与 `linear-4 lwd` 相对全量运行的 57%–94% 能耗与时间节省，说明时间节省与能耗高度相关 $r>0.99$）。
5. **ICSME 2026（`10.1109/icsme67971.2026.00138`）**：*"Predictive Test Optimization Without Historical Failure Data"*（与无历史失败数据场景的冷启动优化相关）。
6. **ISSTA / EuroSys / OSDI 2024–2026 情况**：在限定检索中，未在 ISSTA、EuroSys、OSDI 2024–2026 检索到以“merge queue / merge train / speculative PR batching”为核心主题的撞车全文；相关顶会论文主要集中在 ICSE-SEIP 2025（Meta `juloori2025scale`，v7 已引）、ICSME 2025（`jungwirth2025improving`，v7 已引）、ICST 2026（PRediBatch `jungwirth2026predictive`，v7 已引）、EMSE 2025（`Kamath 2025`、`Fallahzadeh 2025`）及 MSR 2026（Agent CI 可靠性）。

### 2. 本轮检索的显式局限（不隐瞒缺口）
- OpenAlex 与 Crossref 公共 API 在批量查询时对部分条目触发了 `HTTP 429 Too Many Requests`（我们已通过 Web 检索与分段重试补全全部核心目标文献的精确 DOI，但未能对所有 2024–2026 会议做全量枚举）。
- 本轮属于**关键词驱动的限定检索**，未逐篇人工翻阅 ISSTA、EuroSys、OSDI、ISSRE、ICSE、FSE 2024–2026 的全部论文目录（ToC）；且 2026 年下半年出版的部分会议论文可能尚未完成第三方元数据索引。
- **建议**：在正式投稿前，由课题组针对**拟投具体会议（如 ICST / ISSRE / FSE / ICSE）近三年官方目录**执行一次 30 分钟的人工复扫。

---

## 五、另两篇旁支仓库的“真实 vs 仿真”只读源码审计

> **安全边界声明**：以下结论完全通过静态读取源代码、数据目录与论文 `.tex` 得出；**未运行任何测试脚本、未触发任何恶意载荷字符串、未创建或执行任何攻击性工作流**。

### 1. `agentic-canary-verification`（ACV）
- **外部评审说法**：“模拟器把信号强度写死了，代码里没有调用任何 LLM；结果可以说是设计进去的。”
- **源码逐行核查**：
  - **真实数据目录为空**：`agentic-canary-verification/data/traces/` 为空目录，无任何真实生产监控或微服务轨迹。
  - **零 LLM 调用**：`src/acv/` 下仅 4 个纯 Python 文件（`models.py`, `topology.py`, `scenarios.py`, `simulator.py`），无任何外部模型 API、网络请求或推理调用。
  - **信号与阈值硬编码耦合**：
    - `src/acv/simulator.py:31` 写死检测阈值 `_UNIT_THRESHOLDS = (0.018, 37.0, 0.07, 0.05)`。
    - `src/acv/simulator.py:133-147` 写死 4 类故障的确定性信号幅度（如语义故障 `0.21 * magnitude`、配置漂移 `0.010*mag / 13*mag / 0.24*mag`、尾延迟 `112*mag*load`、降级 `0.064*mag*load`），其中语义校验对象 `_Quote`（`simulator.py:84-91, 139`）仅检查写死的 `2200 != 2200 + 185`。
    - `src/acv/scenarios.py:10-98` 为 12 服务 × 6 区域纯合成拓扑随机生成器。
  - **论文自述**：`paper/sections/05_discussion_conclusion.tex:4` 亦坦承真实 Kubernetes + OpenTelemetry Astronomy Shop 部署仍为下一步计划。
- **裁决**：**外部评审判断完全成立（True）**。ACV 目前属于概念性仿真原型，接入真实微服务测试床（如 OpenTelemetry Demo / 真实生产脱敏指标）之前不宜投稿。

### 2. `hostile-pr-gate-bench`（HPG）
- **外部评审说法**：“HostilePR 用伯努利随机抽样代替 LLM，结果可以说是设计进去的。”
- **源码逐行核查（需区分“真实执行层”与“硬编码仿真层”）**：
  - **真实数据目录为空**：`hostile-pr-gate-bench/data/traces/` 为空目录；180 个测试 PR（`src/hpg/benchmark.py:20-207`）全部由 5 类 Python handler 模板 × 6 个变体名（`orders`..`events`）程序化拼接生成。
  - **确实真实执行的确定性软件契约**：
    - `src/hpg/evidence.py:89-170` 真实执行 Python AST 扫描（`scan_python`）、代码 token 实体哈希与 SARIF 2.1.0 JSON 片段校验；
    - `src/hpg/gates.py:41-113, 190-226, 264-290` 真实执行基线策略锁（`.gate-policy.yml` / `AGENTS.md` 解析）、预算截断 `Exit 3` fail-closed、输出清洗（`sanitize_output`）以及跨 Push 行移实体哈希追踪。
  - **硬编码仿真与伯努利抽样（外部评审击中要害之处）**：
    - 全库**零真实 LLM API 调用**。
    - `src/hpg/gates.py:124-127` 用 SHA-256 哈希实现确定性伪随机 `_sample()`，随后在 `gates.py:230, 241, 245, 255` 分别对**注入抑制、LLM 召回率（`llm_base_recall`）、SARIF 召回率、良性误报率（`llm_fp_rate`）**执行伯努利抽样。
    - 最关键的是 `src/hpg/gates.py:157-162`：`_susceptibility()` 直接写死 **`return min(0.035, 0.05 * chance) if self.fence else chance`**——即“XML/JSON 数据围栏将 Prompt 注入成功率压低 20 倍至 ≤3.5%”这一核心安全结论**直接由代码公式写死**，并非在真实 LLM 上测得！
  - **论文自述**：`paper/sections/05_discussion_conclusion.tex:4` 亦承认 LLM 语义检测与注入易感性由随机抽样建模、多模型真实 API 评测留待后续。
- **裁决**：**外部评审核心结论成立（True，但应肯定其确定性契约组件确为真实代码而非空壳）**。在接入至少 2–3 个真实模型 API（并在隔离沙箱内测得真实注入/检测曲线）之前，不应作为安全基准或防护论文投稿。

---

## 六、Batch 2 立即可行的实证升级与基线实验设计（SSMQ）

本设计严格遵循三项原则：**① 现在即可落地**（依托已验证可用的公开 GitHub API 权限，无需等 2 个月）；**② 严格分离“真实观测结果（Observed）”与“反事实策略仿真（Counterfactual）”**；**③ 所有基线清晰命名并公开建模保真度边界**，不冒充商业闭源生产服务。

### 1. 真实开源历史 PR/CI 与合并队列数据采集（已实测可行）
本轮通过本机已鉴权 `gh` CLI（当前剩余配额：REST 4,995 / GraphQL 5,000）对 15 个主流开源仓库做了只读探针，得到可直接支撑 Batch 2 的三层真实语料：

| 层级 | 目标仓库与分支 | 本轮实测可观测真实信号 | 解决 v7 哪项外效度硬伤 |
| :---: | :--- | :--- | :--- |
| **Tier A（原生 GitHub Merge Queue 真实队列日志）** | **`microsoft/TypeScript` (`main`)**（SSMQ 现有仓库！）<br>**`rust-lang/cargo` (`master`)**（Rust 生态） | • 原生 `mergeQueue` 已开启<br>• 真实 `merge_group` Actions 运行记录：**TypeScript 690 条**（近百条失败率 4%）、**Cargo 1,033 条**（近百条失败率 7%）<br>• PR 时间线含 **`AddedToMergeQueueEvent`**（**真实入队时刻**）与 **`RemovedFromMergeQueueEvent`**（含 **`merged` / `failed_checks`** 等真实出队/剔除原因；Cargo 近 15 条 PR 中即观察到 3 次 `failed_checks` 踢出）<br>• 每 PR 含 22–34 个真实 `CheckRun`（精确 `startedAt`、`completedAt`、`conclusion`） | **彻底消除“无真实入队时间、无真实被踢出队列 PR、无真实队列失败率”的致命短板**；且覆盖已合入 + 未合入/被踢出队列 PR |
| **Tier B（大规模开源 CI 历史 + 重跑/Flake 信号）** | **`kubernetes/kubernetes` (`master`)**（Prow/Tide）<br>**`python/cpython` (`main`)** 或 **`vercel/next.js` (`canary`)** | • Kubernetes：每 PR 13–22 条 `StatusContext` + `lgtm`/`approved` 标签时刻（Tide 入池近似时刻）+ `/retest` 评论（**真实重跑/疑似 flake 观测下界**）+ 已合入与已关闭未合入 PR<br>• CPython / Next.js：每 PR 53–126 条 `CheckRun`（真实 CI 耗时分布与 `run_attempt > 1` 重跑率） | 替换 v7 的“12 min 合成高斯耗时 + 拍脑袋 5% flake 率”，提供**经验分布校准的 CI 耗时与重跑下界**；覆盖多语言（TS/Rust/Go/Python） |
| **说明（为何不首推 `rust-lang/rust`）** | `rust-lang/rust` (`master`) | 本轮实测其最新 PR head 的 `CheckRun` 数为 `[0,0,0,0,0]`（因为 `rust-lang/bors` 在专用 `auto`/`try` 分支及 rollup PR 上运行测试） | 可留作后续扩展（需专门解析 bors 机器人评论与 rollup 链接），Batch 2 优先用 `rust-lang/cargo` 覆盖 Rust 生态原生队列 |

- **采样窗口建议**：每个 Tier A/B 仓库采集**连续时间窗内全部进入队列/触发必需 CI 的 PR（含 merged、evicted、closed-unmerged）**，目标 **300–500 条 PR/库**（前 25% 严格按入队时间作冷启动 warm-up，后 75% 作评估窗），完整分页拉取全部文件路径、`CheckRun`/`StatusContext` 与 `merge_group` 元数据，保留 SHA-256 采集清单（延续 `verify_v7.py` 纪律）。

### 2. 严格区分“纯观测指标”与“反事实重放指标”
为彻底杜绝学术评审最敏感的“把仿真当实测”问题，Batch 2 将输出严格拆为两张独立证据层：

#### Layer 1：纯历史观测层（Observed Only — 零合成故障注入）
直接统计真实开源队列与 CI 日志，不引入任何反事实假设：
1. **真实入队并发度与到达过程**：基于 `AddedToMergeQueueEvent`（TypeScript/Cargo）与 `lgtm+approved`（Kubernetes）统计真实入队间隔分布、峰值并发排队长度与突发倍数（对比 v7 仅用合并间隔缩放的偏差）。
2. **真实队列失败率与剔除分布**：统计 `merge_group` 失败率、`RemovedFromMergeQueueEvent(reason=failed_checks/merge_conflict/...)` 比例、同 PR 多次入队重跑比例，以及由 `run_attempt > 1` / 同提交“先败后过”/ `/retest` 得到的**经验 flakiness 下界**。
3. **真实同时在队 PR 的结构重叠与锥密度诊断**：
   - 在**真实同时在队窗口**（而非仅合并顺序滑动窗口）计算：文件直接重叠率、二级目录重叠率、**未过滤 co-change 锥密度** vs **过滤 mega-PR（如排除 $>20 / >50$ 文件提交 + 边权/Jaccard 阈值）后的稀疏锥密度**；
   - 检验历史特征（变更行数、文件数、目录中心度、作者/文件近期 CI 失败率）对**真实观测到的单 PR CI 失败 / 队列踢出**的预测能力（AUROC / PR-AUC），**用真实标签而非合成风险公式评估风险打分器**！

#### Layer 2：观测驱动的反事实策略重放层（Observed-Driven Counterfactual Replay — 显式标注假设）
- **保留为真实观测（Observed Inputs）**：真实入队时间戳、真实每 PR CI 耗时（由该 PR 实际 CheckRun 关键路径时长驱动）、真实每 PR 观测到的单 PR CI 失败标签（含重跑后通过的经验 flake 事件）、真实变更文件与热身历史图。
- **显式标注为反事实参数（Declared Counterfactuals）**：由于历史上未曾一起打包的两个 PR 无法观测其联合测试结果，**必须将跨 PR 交互冲突率 $p_c$ 显式声明为反事实灵敏度网格**（例如 $p_c \in \{0.00,\; 0.02,\; 0.05,\; 0.15\}$，外加“零交互纯观测单 PR 失败模式” $p_c=0$ 这一**最干净的基线点**！）。
  - 注意：**$p_c = 0$（仅回放真实单 PR 失败、真实入队时刻、真实 CI 耗时与经验 flake）是一个零交互造假成分的硬锚点**——如果某策略连在真实单 PR 失败流下都无法建立清晰优势或清晰权衡，就绝对不能靠调高合成 $p_c$ 制造假象。

### 3. 清晰标注、可复现的基线矩阵（Batch 2 实施清单）
所有基线均在统一 discrete-event 引擎下实现、共享相同随机种子与资源预算 $W$，并**显式区分“是否满足严格 exact-tree 提交安全”**：

| 基线代号（论文标注名） | 对标系统/论文 | 核心策略机制（可复现规范） | Exact-tree 合规性 | 建模边界（论文必须披露） |
| :--- | :--- | :--- | :---: | :--- |
| `GitHubMQ-StagedGroup` | GitHub Merge Queue（官方文档） | 按入队序构建最多 $K_{\text{group}}$ 个 PR 的 staged merge group 并行构建（上限 `max_entries_to_build`）；失败时**剔除失败 PR 并取消/重建其后方所有分组**（不做二分） | **是**（天然按前缀分组验证） | 抽象 GitHub 托管 runner 弹性排队，固定并发槽位 $W$ |
| `Bors-BatchBisect` | 历史 `bors-ng`（及通用静态批二分） | FIFO 批量合并至多 $K$ 个 PR 上主干快照测试；失败则递归二分隔离（不做跨批投机堆叠） | **是**（串行或带 exact-tree stale 重验） | 对应历史 `bors-ng` 批二分语义，区别于现行 `rust-lang/bors` 人工 rollup |
| `Zuul-LinearSpeculative` | Zuul Dependent Pipeline / GitLab Merge Trains | 单时间线线性投机前缀 $P_1, P_1\cup P_2, \dots, P_1\cup\cdots\cup P_D$ 并行测试；上游失败即中止并重建下游前缀（无分支结果树） | **是** | 单仓库共享队列模型，不含跨仓库 `Depends-On` |
| `SubmitQueue-OutcomeTree` | Uber SubmitQueue（v7 `FIFOOutcomeTree`） | 单时间线投机结果树（同时投机 pass/fail 分支），使用与环境解耦的通用先验 | **是** | 单时间线精确树版本，不代表 Uber 闭源分布式构建目标分析器 |
| `PRediBatch-RiskGroup` | Jungwirth et al., ICST 2026 (`10.1109/icst69053.2026.00087`) | 基于仅用热身历史训练的轻量语言无关分类器（PR 元数据 + 历史失败率，如逻辑回归/GBDT）估计单 PR 失败概率 $\hat{p}_i$，将低风险 PR 分组打包、高风险 PR 隔离运行（在单时间线前缀栈内对比） | **是** | 复现其“语言无关元数据风险分批”核心思想，非 BMW 内部特征集原样复刻 |
| `Beheshtian-BatchStop4` | Beheshtian et al., TSE 2022 (`10.1109/TSE.2021.3070269`) | 固定/动态批处理结合 `BatchStop4` 规则：失败批二分至规模 $\le 4$ 即停止二分转单测（并增补其风险感知批处理变体 `Beheshtian-RiskBatch`） | **是** | 适配至 exact-tree 多 worker 合并队列语境 |
| `Aviator-DirAffected` | Aviator / Trunk 目录级受影响目标并行队列 | 按预设/目录级受影响目标（top-level / 2-level dir）划分子队列并行运行；提供两档对照：① **Strict exact-tree 版**（跨子队列合入触发 stale 重验）；② **Trust-Target-Isolation 版**（假设目录隔离完备允许独立合入，**显式计量跨目录潜在逃逸率**） | ① 是 / ② 否（记录逃逸率） | 对应非 Bazel 目录规则模式，非 Bazel 精准依赖图 |
| `SSMQ-v7 (Unstacked)` | 当前 v7 完整策略 | 未过滤 co-change 锥 + 风险双车道 + Beta-Binomial 门控（对照基准，复现 v7 负面现象） | **是** | 保持与 v7 字节级兼容 |
| `SSMQ-FilteredCone` | **Batch 2 机制诊断变体 1** | 过滤 mega-PR（$|F(p)| > F_{\max}$）并按共变权重/PMI 剪枝 $\hat{G}$，检验缓解“锥爆炸”后未堆叠打包是否仍败于 stale-base 失效 | **是** | 回答 v7 `Sec. VII` 遗留问题 |
| `SSMQ-StackedOrdered` | **Batch 2 机制诊断变体 2** | **在单时间线 exact-tree 投机前缀栈内部**结合“风险/稀疏依赖排序 + Beta-Binomial 门控”（消除 stale-base 失效的同时检验结构/风险排序增益） | **是** | 直接回应 `review_codex_v7` 与 `07_discussion_threats.tex:3` 指出的开放对比 |

### 4. Batch 2 执行顺序与产出验收标准（供 Root 调度）
建议将 Batch 2 拆为 **2 个紧凑子步**（每步均独立可验真、不改手稿，待结果封存后再进入 Batch 3 论文修订）：
1. **Batch 2A（真实数据采集与 Layer 1 观测诊断）**：
   - 新增 `scripts/fetch_mq_history.py`（采集 `microsoft/TypeScript`、`rust-lang/cargo`、`kubernetes/kubernetes`、`python/cpython` 四个公开仓库的真实 PR 时间线、入队/踢出事件、`merge_group` 与 `CheckRun` 耗时/结论/重跑，保存至 `data/mq_traces/` 并生成 SHA-256 `fetch_manifest.json`）。
   - 新增 `eval/analyze_mq_traces.py`，输出 Layer 1 纯观测报告（真实入队并发、真实失败/踢出率、经验 flake 下界、真实同在队窗口的锥密度与 mega-PR 剪枝曲线、真实单 PR 失败风险预测 AUROC）。
2. **Batch 2B（可复现基线实现与 Layer 2 反事实重放）**：
   - 在 `src/ssmq/` 扩展上述 7 组外部基线与 2 组 SSMQ 诊断变体（配齐单元测试：含 `GitHubMQ-StagedGroup` 剔除重建不变式、`Bors-BatchBisect`、`Beheshtian-BatchStop4`、`PRediBatch-RiskGroup` 防泄漏训练检验、`Aviator-DirAffected` 逃逸计量、及 `SSMQ-StackedOrdered` exact-tree 断言）。
   - 在 `eval/harness_v8.py`（新版本号，不覆盖 `runs/v7_*`）跑通 **4 仓库 × 真实入队/耗时/单 PR 失败流 × $p_c \in \{0.00, 0.02, 0.05, 0.15\}$**，封存 `runs/v8_*` 并输出完整对比表（含 P95、完成率/FRR、CI runner-min/merge、失效占比、逃逸率与配对 bootstrap 区间）。
3. **归因模块（AST 排序）的务实定位**：
   - 不建议在 Batch 2 强行大修 AST 归因去追赶 `Fonte`/`SemBIC`——因为历史仓库缺少成批多 PR 合并失败的真实 Python 堆栈，强行大规模扩写仍受限于数据可得性；应维持 v7 将其定位为**已验证适用边界的补充模块（仅在允许同文件并批的 FIFO 场景下对同文件异函数冲突有小幅收益）**，把 90% 的 Batch 2 工程预算投入上述**真实历史队列回放与可信基线对比**。

---

## 七、关键证据路径索引

- SSMQ v7 手稿与生成数据：
  - `semantic-speculative-merge-queue/paper/main.tex`
  - `semantic-speculative-merge-queue/paper/sections/01_introduction.tex` ～ `08_conclusion.tex`
  - `semantic-speculative-merge-queue/paper/refs.bib`
  - `semantic-speculative-merge-queue/paper/generated/v7.tex`、`paper/generated/sim_v7.tex`、`paper/generated/tables/git_v7.tex`、`paper/generated/tables/trace_summary.tex`
  - `semantic-speculative-merge-queue/data/traces/fetch_manifest.json`
  - `semantic-speculative-merge-queue/eval/harness_v7.py`、`eval/verify_v7.py`、`src/ssmq/simulator.py`
- SSMQ 历史内部独立评审与版本证据：
  - `semantic-speculative-merge-queue/reports/review_codex_v4.md`
  - `semantic-speculative-merge-queue/reports/review_codex_v5.md`
  - `semantic-speculative-merge-queue/reports/review_neon_v6.md`
  - `semantic-speculative-merge-queue/reports/review_codex_v7.md`
  - Git 历史对照：`e2e9674`（v4）、`70af492`（v5）、`a0b108d`（v7）
- 另两篇仓库只读核查路径：
  - `agentic-canary-verification/src/acv/simulator.py`、`src/acv/scenarios.py`、`paper/sections/03_evaluation.tex`、`paper/sections/05_discussion_conclusion.tex`、`data/traces/`
  - `hostile-pr-gate-bench/src/hpg/gates.py`、`src/hpg/benchmark.py`、`src/hpg/evidence.py`、`paper/sections/03_evaluation.tex`、`paper/sections/05_discussion_conclusion.tex`、`data/traces/`
