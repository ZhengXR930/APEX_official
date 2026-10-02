# Repository structure

```text
apex_official/
├── src/apex/
│   ├── core/                         shared model/evaluation utilities
│   └── defender/
│       ├── contract/                  Contract schema and compiler
│       ├── taskcontractor.py          runtime Contract synthesis
│       ├── memory.py                  capability/source surfaces
│       ├── state.py                   bindings and Receipts
│       ├── receipt_binding.py         deterministic Receipt ownership
│       ├── conditional_operators.py   Conditional operator registry
│       ├── resolver.py                clause-output resolution
│       ├── proof.py                   Derive goals and deterministic proofs
│       ├── plant.py                   plan-level detection
│       ├── wrap.py                    effect authorization
│       ├── continuation.py            repair/replan outcomes
│       ├── broker.py                  capability invocation boundary
│       └── engine.py                  episode state machine
├── benchmark/
│   ├── adapter/                       benchmark-specific case translation
│   ├── data/                          normalized benchmark inputs
│   ├── protocol/                      denominators, applicability, data hashes
│   └── registry/
│       ├── data/                      generated trusted manifests
│       ├── source/                    audited registry inputs
│       └── *.py                       loaders and attestations
├── baseline/<name>/                   comparison-method integrations
├── scripts/                           import/build/audit utilities
├── examples/quickstart.py
└── tests/
```

## Trust boundaries

```text
trusted user task ──> TaskContract Agent ──> validated TaskContract
operator manifest ──> capability registry ─> EnvironmentPlan
benchmark dataset ──> adapter ─────────────> untrusted episode input
runtime outputs ────> UnitBroker ──────────> Receipts

TaskContract + EnvironmentPlan + Receipts
                  │
                  v
             APEX Episode
             ├── PLANT
             ├── deterministic resolver
             ├── bounded semantic binding
             ├── WRAP
             └── continuation
                  │
                  v
       allow | deny | repair | replan | approval
```

The adapter and registry are deliberately separate. An adapter may expose
attacker-controlled benchmark text, but only an operator-owned registry can
create a capability or authority-bearing surface.

## Conditional operators

All Conditional arities, operand types, and replay implementations live in
`src/apex/defender/conditional_operators.py`. To add an operator:

1. add its arity, operand typing, and pure replay logic there;
2. add deterministic tests.

Conditional replay must not invoke a model or infer new authority.

## Adding integrations

For a benchmark, add an adapter, registry loader and manifest, then test IDs,
splits, denominators, eligibility, and capability coverage. For a baseline,
keep its prompt, policy, and runtime translation inside a dedicated
`baseline/<name>/` package and register only its canonical entry point.
