# APEX

Code accompanying the APEX paper. The repository contains the active-defense
implementation together with the benchmark adapters, normalized benchmark
inputs, trusted capability registries, baseline integrations, and evaluation
utilities needed to reproduce the comparisons. It does not include generated
TaskContracts or experiment run outputs.

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
| `src/apex/core/` | Model boundary, evaluation protocol, aggregation, runner, and integration helpers |
| `benchmark/adapter/` | Conversion of six benchmark releases into a common case interface |
| `benchmark/data/` | Packaged normalized benchmark inputs |
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

WRAP uses semantic binding only for a Contract-declared `Derive` value and
only when the capability manifest permits semantic support for that argument.
Literals, defaults, Acquire projections, Conditional clauses, delegation,
authority-bearing identities, and committed Effect returns use deterministic
code. Conditional arity, operand types, and replay logic are centralized in
`src/apex/defender/conditional_operators.py`.

## Benchmark and baseline handling

Packaged case descriptors can be loaded through `benchmark.adapter.adapter_for`.
Adapters own case IDs, splits, suite labels, eligibility, and runtime payload
translation. Trusted capability registration remains separate under
`benchmark/registry/`; benchmark text cannot register new authority.

`baseline/registry.py` lists the comparison methods. Each baseline keeps its
method-specific translation and policy inside its own directory so it cannot
silently change APEX behavior.

`src/apex/core/protocol.py` checks evaluation coverage,
`src/apex/core/aggregation.py` computes normalized metrics, and `scripts/`
rebuilds benchmark descriptors and registry manifests. These utilities are
included, but their generated results are not.

## Verification

```bash
python -m compileall -q src benchmark baseline examples tests
pytest
```

Generated Contracts, results, traces, caches, logs, checkpoints, and run
directories are ignored by Git and must remain outside the public source tree.
