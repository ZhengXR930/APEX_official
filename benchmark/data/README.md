# Benchmark data

This directory contains the compact frozen inputs consumed by every adapter.
It does not vendor full upstream repositories or runtime sandboxes.

| Adapter | Included inputs | Logical cases |
|---|---|---:|
| AgentDojo | clean task IDs and attack task/injection pairs | 726 |
| ASB-OPI | clean and attack case descriptors | 2,091 |
| MCPTox | clean queries and attack instances | 1,705 |
| MSB | aligned attack case descriptors | 622 |
| SCR | suite index, Skill exposure, eligibility, and conditions | 669 |
| SkillInject | cases, tasks, and task-file index | 180 |

SCR has 669 available suite cases: CapFlow 150, AuthBlur 118, and TrustLift
401. AuthBlur cases 26 and 76 are retained but marked ineligible because the
pinned upstream checkout lacks required advisor Skill files. This yields the
116 runnable AuthBlur cases used by the evaluation protocol.

The final trusted capability artifacts live in `benchmark/registry/data/`.
Case text here is untrusted benchmark input and never creates capability
authority. `scripts/import_benchmark_data.py` refreshes these inputs from the
research checkout and the pinned SCR_Bench checkout.
