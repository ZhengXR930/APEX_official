# APEX

This repository contains the standalone APEX active-defense implementation.
It intentionally excludes benchmark adapters, baseline implementations, frozen
TaskContracts, evaluation outputs, traces, and result-merging utilities.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export OPENAI_API_KEY='<your-key>'
export APEX_MODEL='<openai-compatible-model>'
```

`OPENAI_BASE_URL` is optional for OpenAI-compatible providers. The code reads
credentials only from environment variables.

## Run the minimal example

```bash
python examples/quickstart.py
```

The example registers an operator-trusted capability manifest, synthesizes a
TaskContract from the current user task, starts one protected episode, and
routes an effect through `UnitBroker`. Contracts are generated at runtime; no
precomputed contract bundle is included.

## Core flow

1. `Engine.perceive(...)` validates the operator-owned capability manifest.
2. `Engine.contract(...)` asks the TaskContract Agent for the current task's
   four-clause Contract and validates it deterministically.
3. `UnitBroker` mediates each direct or nested capability invocation.
4. PLANT checks model-visible carriers and commitment boundaries.
5. WRAP closes the proposed Effect against the Contract and runtime Receipts.
6. Continuation returns a bounded repair, replan, approval, or abort decision.

WRAP uses semantic binding only for a Contract-declared `Derive` value and
only when the capability manifest permits semantic support for that argument.
Literals, defaults, Acquire projections, Conditional clauses, delegation,
authority-bearing identities, and committed Effect returns are resolved by
deterministic code. Conditional operator definitions live in
`conditional_operators.py`.

## Integrating an agent

Treat every tool, MCP operation, or Skill operation as a capability unit:

- register its exact input schema and whether it is an observation or effect;
- call `UnitBroker.invoke(...)` instead of invoking the implementation
  directly;
- expose returned observations through the broker so APEX can issue Receipts;
- if a decision requests continuation, give the returned continuation payload
  to a fresh recovery turn before retrying.

See `examples/quickstart.py` for the smallest complete integration and
`STRUCTURE.md` for the trust boundaries.

## Verify

```bash
python -m compileall -q src examples tests
pytest
```

Generated contracts, traces, caches, logs, runs, and results are ignored by
Git and must remain outside the public source tree.
