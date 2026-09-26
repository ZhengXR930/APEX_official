# Registry coverage audit

The final artifacts use capability units, not one manifest per benchmark
case. A case binding selects a reusable environment and may select a different
environment for each benchmark condition.

| Benchmark | Capability units | Environments | Logical case bindings | Eligible logical cases |
|---|---:|---:|---:|---:|
| AgentDojo | 69 | 4 | 726 | 726 |
| ASB-OPI | 20 | 10 | 2,091 | 2,091 |
| MCPTox | 839 | 45 | 1,705 | 1,705 |
| MSB | 36 | 7 | 622 | 415 |
| SCR | 75 | 110 | 669 | 667 |
| SkillInject | 33 | 18 | 180 | 180 |

MSB retains all 622 aligned cases; 207 cases in benchmark-defined excluded
attack categories are marked ineligible by the adapter. SCR retains all 118
AuthBlur cases present in the pinned upstream tree; cases 26 and 76 are marked
ineligible because required advisor Skill files are absent, leaving the
protocol denominator of 116.

SCR's 110 environments are condition-specific reusable surfaces, not case
copies. In particular, AuthBlur uses one shared internal three-capability
environment while its target-visible two-tool control surface is recorded
separately. TrustLift clean exposes only `scan_skills`; attack exposes both
`scan_skills` and `install_skills`.

Run the executable coverage check with:

```bash
python scripts/audit_registry_coverage.py
```
