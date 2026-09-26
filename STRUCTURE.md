# Repository structure

```text
apex_official/
├── src/apex/
│   ├── core/                 shared protocols, types, runner, model boundary
│   └── defender/
│       ├── contract/         task-contract schema and compiler
│       ├── taskcontractor.py typed contract construction
│       ├── memory.py         capability and source surfaces
│       ├── state.py          bindings, receipts, and runtime state
│       ├── resolver.py       lazy typed-value resolution
│       ├── proof.py          provenance and placement proof checks
│       ├── plant.py          plan-level authorization checks
│       ├── wrap.py           action-level authorization checks
│       ├── engine.py         episode state machine
│       └── continuation.py   repair and replan outcomes
├── benchmark/
│   ├── adapter/              external dataset → BenchmarkCase
│   ├── data/                 local dataset placement instructions
│   └── registry/
│       ├── data/             final trusted registry artifacts
│       ├── source/           audited inputs used to build the artifacts
│       └── *.py              loaders and benchmark-specific attestations
├── baseline/
│   ├── registry.py           canonical set of comparison methods
│   ├── common.py             shared guard boundary types
│   └── <BaselineName>/       isolated implementation entry point
└── tests/                    fast structural and unit tests
```

## Trust boundaries

```text
trusted user task ──> TaskContractor ──> TaskContract
operator manifest ──> capability registry ──> EnvironmentPlan
benchmark dataset ──> adapter ──> BenchmarkCase (untrusted episode input)

TaskContract + EnvironmentPlan + receipts
                  │
                  v
            APEX engine
             ├── PLANT: plan/provenance proof
             └── WRAP: proposed-effect proof
                  │
                  v
       allow | deny | repair | replan
```

The adapter and registry are deliberately separate. An adapter may expose
attacker-controlled text needed by the benchmark, but it cannot create a new
capability. Only an operator-owned registry manifest can do that.

Each final registry bundle uses the same layout:

```text
manifest.json
├── schema: apex-benchmark-registry-v2
├── benchmark
├── capability_units
│   └── <stable name@digest>
│       └── name, description, inputSchema, outputSchema,
│           effect, observation, effect_return, receipt_role,
│           argument_types, output_types
├── environments
    └── <reusable capability surface>
        ├── sources
        ├── capability_units
        ├── skills
        └── agent_visible_surface
└── case_bindings
    └── <benchmark case>
        ├── environment
        └── condition-specific environment/source/Skill overlays
```

`scripts/build_registry_manifests.py` regenerates all six final bundles from
trusted benchmark metadata. Identical capability units and environments are
stored once; case bindings preserve complete evaluation coverage.

## Core modules

- `contract/model.py` defines acquire, effect, and conditional clauses.
- `contract/compiler.py` converts typed proposals into validated contracts.
- `binding_agent.py` proposes typed bindings; deterministic validation decides
  whether a proposal is admissible.
- `receipt_binding.py` converts mediated observations and effect returns into
  episode-scoped receipts.
- `resolver.py` resolves exact values without treating untrusted prose as
  authority.
- `proof.py` assembles proof obligations from the contract and runtime state.
- `plant.py` checks whether a proposed plan has the required provenance.
- `wrap.py` checks the concrete effect immediately before commitment.
- `engine.py` coordinates the state transition and emits a decision.

## Adding a benchmark

1. Add `benchmark/adapter/<name>.py` with a `DatasetAdapter` subclass.
2. Register it in `benchmark/adapter/__init__.py`.
3. Add `benchmark/registry/<name>.py` and immutable manifest data under
   `benchmark/registry/data/<name>/`.
4. Register the manifest module in `benchmark/registry/__init__.py`.
5. Add fixture-based tests for IDs, splits, denominators, and eligibility.

## Adding a baseline

1. Create `baseline/<BaselineName>/implementation.py`.
2. Keep method-specific prompts, thresholds, and runtime translation in that
   folder; use `baseline/common.py` only for shared boundary types.
3. Add its canonical key to `baseline/registry.py`.
4. Import optional dependencies inside factories or constructors so unrelated
   methods remain usable without that environment.
