# APEX

Code accompanying the APEX paper. The repository contains the active-defense
implementation together with the benchmark adapters, normalized benchmark
inputs, trusted capability registries, baseline integrations, and evaluation
utilities that define the comparisons. It does not include generated
TaskContracts, experiment run outputs, or third-party benchmark checkouts.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export OPENAI_API_KEY='<your-key>'
export APEX_MODEL='<openai-compatible-model>'
```

`OPENAI_BASE_URL` is optional for OpenAI-compatible providers. Registry
regeneration additionally uses `pip install -e '.[registry]'`.

## Repository map

| Path | Responsibility |
|---|---|
| `src/apex/defender/` | APEX Contract, Receipt, PLANT, WRAP, broker, and continuation implementation |
| `src/apex/runtime.py` | Framework-neutral Tool/MCP/Skill execution bridge |
| `src/apex/core/` | Model boundary, evaluation protocol, aggregation, runner, and integration helpers |
| `benchmark/adapter/` | Conversion of six benchmark releases into a common case interface |
| `benchmark/execution/` | Common native-driver API and offline integration preflight |
| `benchmark/data/` | Packaged normalized benchmark inputs |
| `benchmark/protocol/` | Fixed denominators, method applicability, and input hashes |
| `benchmark/registry/` | Trusted capability manifests, loaders, and audited registry sources |
| `baseline/<name>/` | Isolated integration for each comparison method |
| `scripts/` | Benchmark-data import and registry build/audit utilities |
| `examples/quickstart.py` | Minimal standalone APEX integration |
| `tests/` | Core and repository-invariant tests |

## Minimal APEX example

```bash
python examples/quickstart.py
```

The example registers a capability manifest, synthesizes a TaskContract from
the current user task, starts one protected episode, and routes an effect
through `UnitBroker`. Contracts are generated at runtime; no precomputed
Contract bundle is included.

## APEX execution flow

1. `Engine.perceive(...)` validates the operator-owned capability manifest.
2. `Engine.contract(...)` generates and validates the current task's Contract.
3. `UnitBroker` mediates each direct or nested capability invocation.
4. PLANT checks model-visible carriers and commitment boundaries.
5. WRAP closes the proposed Effect against the Contract and runtime Receipts.
6. Continuation returns a bounded repair, replan, approval, or abort decision.

WRAP resolves ordinary cases deterministically. If capability and literal
constraints leave multiple compatible `Acquire` roles, the validated Binding
Agent selects one code-issued clause ID. For a Contract-declared semantic
argument role, it can select only code-issued evidence IDs and permitted
compositions, and only when the capability manifest permits semantic support
for that argument. Literals, defaults, Conditional operators, delegation,
authority-bearing identities, and committed Effect returns remain
deterministic. Conditional arity, operand types, and replay logic are
centralized in `src/apex/defender/conditional_operators.py`.

## Benchmark and baseline handling

Packaged case descriptors can be loaded through `benchmark.adapter.adapter_for`.
Adapters own case IDs, splits, suite labels, eligibility, and runtime payload
translation. Trusted capability registration remains separate under
`benchmark/registry/`; benchmark text cannot register new authority.

`baseline/registry.py` lists the comparison methods. Each baseline keeps its
method-specific translation and policy inside its own directory so it cannot
silently change APEX behavior.

The repository vendors the APEX-side handling, normalized inputs, and registry
surfaces for every reported benchmark. A full native rerun additionally needs
the corresponding upstream benchmark/baseline package and its service
credentials; those third-party repositories are intentionally not copied here.

Every native benchmark driver receives a `CaseContext`. Calling
`context.protected_runtime(task)` generates a fresh Contract for that task and
returns the same protected Tool/MCP/Skill state machine used by the benchmark
integrations: `invoke(...)` mediates calls and records Receipts, while
`response(...)` checks the final commitment. A driver must return one
`EpisodeResult` (or its dictionary form); the common runner validates canonical
case identity, checkpoints atomically, supports resume, and never reads a
generated Contract bundle.

The complete public execution path can be checked without credentials or an
upstream checkout. This validates every packaged protocol and registry, starts
real APEX episodes, and verifies that an effect absent from the Contract is
denied before its implementation runs:

```bash
apex-benchmark inspect --benchmark all --samples 2
```

Run native benchmark episodes by supplying the benchmark checkout's thin
driver callable:

```bash
apex-benchmark run \
  --benchmark agentdojo \
  --method ours \
  --target-model "$APEX_MODEL" \
  --defense-model "$APEX_MODEL" \
  --driver my_agentdojo_bridge:run_case \
  --split attack --limit 2 \
  --output outputs/agentdojo-sample.json
```

The driver is the only benchmark-owned layer: it constructs the native
environment, performs agent turns, executes native tools through
`ProtectedRuntime.invoke`, and calls the official scorer. This keeps upstream
code and credentials outside APEX without replacing benchmark execution with a
simulator.

`src/apex/core/protocol.py` checks evaluation coverage,
`src/apex/core/aggregation.py` computes normalized metrics, and
`scripts/merge_results.py` rejects conflicting shards before producing one
canonical summary. The remaining scripts rebuild benchmark descriptors and
registry manifests. These utilities and fixed protocols are included, but
their generated results are not.

## Verification

```bash
python -m compileall -q src benchmark baseline examples tests
pytest
```

Generated Contracts, results, traces, caches, logs, checkpoints, and run
directories are ignored by Git and must remain outside the public source tree.
