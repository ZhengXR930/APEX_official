# Repository structure

```text
apex_official/
├── src/apex/
│   ├── core/
│   │   ├── client.py                 OpenAI-compatible model boundary
│   │   ├── manifest.py               capability-manifest validation
│   │   └── nested_effects.py         legacy nested-call adapter
│   └── defender/
│       ├── contract/                  Contract schema and compiler
│       ├── taskcontractor.py          runtime Contract synthesis
│       ├── memory.py                  capability/source surfaces
│       ├── state.py                   bindings and Receipts
│       ├── receipt_binding.py         deterministic Receipt ownership
│       ├── conditional_operators.py   closed Conditional operator registry
│       ├── resolver.py                clause-output resolution
│       ├── proof.py                   Derive goals and deterministic proofs
│       ├── plant.py                   plan-level detection
│       ├── wrap.py                    effect authorization
│       ├── continuation.py            repair/replan outcomes
│       ├── broker.py                  capability invocation boundary
│       └── engine.py                  episode state machine
├── examples/quickstart.py
└── tests/
```

## Trust boundaries

```text
trusted user task ──> TaskContract Agent ──> validated TaskContract
operator manifest ──> Surveyor ───────────> EnvironmentPlan
runtime outputs ────> UnitBroker ─────────> Receipts

TaskContract + EnvironmentPlan + Receipts
                  │
                  v
             APEX Episode
             ├── PLANT
             ├── deterministic resolver
             ├── semantic binding (Derive only)
             ├── WRAP
             └── continuation
                  │
                  v
       allow | deny | repair | replan | approval
```

The capability manifest is operator-owned and exists before the episode.
Runtime text can supply data but cannot add capabilities, Effects, arguments,
operators, or authority.

## Conditional operators

All Conditional operand types and replay implementations are centralized in
`src/apex/defender/conditional_operators.py`. To add an operator:

1. add its arity, operand typing, and pure replay logic to
   `conditional_operators.py`;
2. add deterministic tests.

Conditional replay must not invoke a model or infer new authority.
