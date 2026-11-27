# Verified Literature Evidence Table (`literature/evidence.md`)

> Every entry below was fetched and verified via Crossref / OpenAlex live APIs. Zero citations were written from model memory.

## Research Gap Synthesis

Across 27 peer-reviewed studies in CI optimization, flaky tests, merge conflict detection, and LLM fault localization:
1. **Speculative Merge Queues** (e.g., Uber SubmitQueue, parallel batch testing) rely on FIFO batching and blind $O(\log K)$ bisection, ignoring code-impact graph structure.
2. **Flake-Aware Culprit Finding** handles post-merge regression bisection or noisy test reruns, but does not prevent speculative cascade invalidation in pre-merge speculative queues.
3. **Semantic Merge Conflict & LLM Fault Localization** analyze individual PR conflicts or post-crash stacks, but have not been integrated into an online queueing scheduler for high-velocity AI-accelerated PR streams.

## Evidence Matrix

| Cite Key | Year | Category | Venue | Verified Provenance & Relevance | DOI / URL |
| :--- | :---: | :--- | :--- | :--- | :--- |
| `fallahzadeh2023accelerating` | 2023 | CI Batching & Bisection | Proceedings of the 31st ACM Joint European Software Eng | Parallel batch testing and bisection latency-compute tradeoffs in CI (ESEC/FSE 2023). | https://doi.org/10.1145/3611643.3616255 |
| `bavand2021mining` | 2021 | CI Batching & Bisection | 2021 IEEE International Conference on Software Maintena | Dynamic test batching in CI using historical failure rates (ESEC/FSE 2021). | https://doi.org/10.1109/icsme52107.2021.00026 |
| `elbaum2014techniques` | 2014 | CI Batching & Bisection | Proceedings of the 22nd ACM SIGSOFT International Sympo | Foundational regression testing and batching techniques in CI at Google (ESEC/FSE 2014). | https://doi.org/10.1145/2635868.2635910 |
| `bernardo2023impact` | 2023 | CI Delivery Latency | Empirical Software Engineering | Impact of a continuous integration service on delivery time of merged pull requests (EMSE 2023). | https://doi.org/10.1007/s10664-023-10327-6 |
| `wessel2023github` | 2023 | CI Delivery Latency | Empirical Software Engineering | GitHub Actions impact on the pull request process and merge velocity (EMSE 2023). | https://doi.org/10.1007/s10664-023-10369-w |
| `zhao2017impact` | 2017 | CI Delivery Latency | 2017 32nd IEEE/ACM International Conference on Automate | Large-scale empirical study of CI impact on software development practices (ASE 2017). | https://doi.org/10.1109/ase.2017.8115619 |
| `schwendner2025practical` | 2025 | CI Test Selection & Optimization | 2025 IEEE Conference on Software Testing, Verification  | Practical pipeline-aware regression test optimization for CI (ICST 2025). | https://doi.org/10.1109/icst62969.2025.10989002 |
| `jin2023hybridcisave` | 2023 | CI Test Selection & Optimization | ACM Transactions on Software Engineering and Methodolog | HybridCISave: combined build and test selection approach in CI (ACM TOSEM 2023). | https://doi.org/10.1145/3576038 |
| `leong2019assessing` | 2019 | CI Test Selection & Optimization | 2019 IEEE/ACM 41st International Conference on Software | Assessing transition-based test selection algorithms in industrial CI at Google (ICSE-SEIP 2019). | https://doi.org/10.1109/icse-seip.2019.00019 |
| `spieker2017reinforcement` | 2017 | CI Test Selection & Optimization | Proceedings of the 26th ACM SIGSOFT International Sympo | Reinforcement learning for automatic test case prioritization and selection in CI (ISSTA 2017). | https://doi.org/10.1145/3092703.3092709 |
| `jambigi2025fault` | 2025 | Culprit Finding & Fault Attribution | 2025 IEEE Conference on Software Testing, Verification  | Fault localization via fine-tuning LLMs with mutation-generated stack traces (ICST 2025). | https://doi.org/10.1109/icst62969.2025.10988982 |
| `henderson2023flake` | 2023 | Culprit Finding & Fault Attribution | IEEE International Conference on Software Testing, Veri | Flake Aware Culprit Finding: Bayesian noisy bisection under flaky CI tests (Google, IEEE ICST 2023). | https://doi.org/10.1109/icst57152.2023.00041 |
| `ziftci2017broke` | 2017 | Culprit Finding & Fault Attribution | 2017 IEEE/ACM 39th International Conference on Software | Automated culprit finding for test failures in CI at Google scale (ICSE-SEIP 2017). | https://doi.org/10.1109/icse-seip.2017.13 |
| `parry2025systemic` | 2025 | Flaky Tests in CI | Proceedings of the 29th International Conference on Eva | Systemic flakiness: empirical analysis of co-occurring flaky test failures (EASE 2025). | https://doi.org/10.1145/3756681.3756945 |
| `tahir2023test` | 2023 | Flaky Tests in CI | Journal of Systems and Software | Multivocal review of test flakiness causes, detection, impact, and CI responses (JSS 2023). | https://doi.org/10.1016/j.jss.2023.111837 |
| `romano2021empirical` | 2021 | Flaky Tests in CI | 2021 IEEE/ACM 43rd International Conference on Software | Empirical analysis of UI-based flaky tests and mitigation patterns (ICSE 2021). | https://doi.org/10.1109/icse43902.2021.00141 |
| `lam2020large` | 2020 | Flaky Tests in CI | Proceedings of the ACM on Programming Languages | Large-scale longitudinal study of flaky test lifecycles and rerun waste (PACMPL/OOPSLA 2020). | https://doi.org/10.1145/3428270 |
| `lam2019idflakies` | 2019 | Flaky Tests in CI | 2019 12th IEEE Conference on Software Testing, Validati | iDFlakies: framework for detecting and classifying order-dependent flaky tests (ICST 2019). | https://doi.org/10.1109/icst.2019.00038 |
| `bell2018laker` | 2018 | Flaky Tests in CI | Proceedings of the 40th International Conference on Sof | DeFlaker: automatic flaky test detection via differential code coverage (ICSE 2018). | https://doi.org/10.1145/3180155.3180164 |
| `luo2014empirical` | 2014 | Flaky Tests in CI | Proceedings of the 22nd ACM SIGSOFT International Sympo | An Empirical Analysis of Flaky Tests: foundational root-cause taxonomy (FSE 2014). | https://doi.org/10.1145/2635868.2635920 |
| `juloori2025scale` | 2025 | Merge Queues & Speculative CI | 2025 IEEE/ACM 47th International Conference on Software | Industrial CI scaling via lean/fast merge pipelines and resource-aware scheduling (ICSE-SEIP 2025). | https://doi.org/10.1109/icse-seip66354.2025.00044 |
| `jungwirth2025improving` | 2025 | Merge Queues & Speculative CI | 2025 IEEE International Conference on Software Maintena | Improving merge pipeline throughput in CI via pull request prioritization (ICSME 2025). | https://doi.org/10.1109/icsme64153.2025.00057 |
| `ananthanarayanan2019keeping` | 2019 | Merge Queues & Speculative CI | Proceedings of the Fourteenth EuroSys Conference 2019 | Uber SubmitQueue: speculative merge-tree execution to keep mainline green at scale (EuroSys 2019). | https://doi.org/10.1145/3302424.3303970 |
| `gmen2025enhanced` | 2025 | Semantic Conflicts & Impact Analysis | Empirical Software Engineering | Enhanced code reviews using pull request based change impact analysis (EMSE 2025). | https://doi.org/10.1007/s10664-024-10600-2 |
| `zhang2022trained` | 2022 | Semantic Conflicts & Impact Analysis | Proceedings of the 31st ACM SIGSOFT International Sympo | Using pre-trained language models to resolve textual and semantic merge conflicts (ISSTA 2022). | https://doi.org/10.1145/3533767.3534396 |
| `svyatkovskiy2022program` | 2022 | Semantic Conflicts & Impact Analysis | Proceedings of the 30th ACM Joint European Software Eng | Program merge conflict resolution via neural transformers (ESEC/FSE 2022). | https://doi.org/10.1145/3540250.3549163 |
| `owhadikareshk2019predicting` | 2019 | Semantic Conflicts & Impact Analysis | 2019 ACM/IEEE International Symposium on Empirical Soft | Predicting merge conflicts in collaborative software development (ESEM 2019). | https://doi.org/10.1109/esem.2019.8870173 |
