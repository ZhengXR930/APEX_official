# APEX

Code accompanying an anonymous paper submission. The repository separates the
APEX defender, benchmark data normalization, trusted capability manifests, and
comparison-method integrations so that each layer can be inspected independently.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export PYTHONPATH="$PWD/src:$PWD"
```

Model-backed components read `OPENAI_API_KEY` and the optional
`OPENAI_BASE_URL` from the environment. Copy `.env.example` only as a reference;
the code does not read local credential files.

## Repository map

| Path | Responsibility |
|---|---|
| `src/apex/defender/` | APEX task contracts, typed bindings, receipts, proof checks, PLANT, WRAP, and continuation handling |
| `src/apex/core/` | Shared protocol, result types, aggregation, and provider-neutral model boundary |
| `benchmark/adapter/` | Read-only conversion of benchmark releases into one `BenchmarkCase` interface |
| `benchmark/registry/` | Trusted capability registration and benchmark-specific manifests |
| `baseline/<name>/` | One implementation or runtime integration per comparison method |
| `tests/` | Repository invariants and fast unit checks |

See [STRUCTURE.md](STRUCTURE.md) for the component flow and extension points.

## Loading benchmark cases

Compact frozen case descriptors for all six benchmarks are included under
`benchmark/data/`. Full upstream repositories and runtime sandboxes are not
vendored. Passing `None` selects the packaged data; an explicit `data_root`
can still override it.

```python
from benchmark.adapter import adapter_for

adapter = adapter_for("scr", None)
attack_cases = list(adapter.cases("attack"))
```

The adapter owns case identifiers, split labels, suite labels, eligibility, and
the payload presented to the benchmark runtime. It does not alter benchmark
content or register tool authority.

## Loading trusted manifests

```python
from benchmark.registry import module_for

registry = module_for("mcptox")
environment_plan = registry.load("12306-mcp")
```

Capability manifests are kept separate from dataset adapters because benchmark
text is untrusted episode input, while a manifest describes the operator-owned
execution boundary available before the episode.

Every `benchmark/registry/data/<benchmark>/manifest.json` is a final registry
artifact using `apex-benchmark-registry-v2`. A bundle stores deduplicated
`capability_units`, reusable environments, and explicit case bindings. Thus a
benchmark with hundreds of cases does not duplicate an identical Tool manifest
hundreds of times. Each unit retains the exact input/output schema, `effect`,
`observation`, `effect_return`, receipt role, and typed annotations. Each
environment separately records sources, Skills, and `agent_visible_surface`.

The exact audited source inputs from the experiment implementation are kept
under `benchmark/registry/source/`. The build step normalizes and deduplicates
those existing registrations; it does not infer new capability semantics from
benchmark prompts. Regenerate all final artifacts with:

```bash
pip install -e '.[registry]'
python scripts/build_registry_manifests.py
python scripts/audit_registry_coverage.py
```

To refresh packaged case descriptors from local upstream checkouts, run:

```bash
python scripts/import_benchmark_data.py \
  --research-root <research-checkout> \
  --scr-root <SCR_Bench-checkout>
```

The SCR importer verifies the pinned commit before deriving its compact
case/Skill exposure index.

## APEX execution flow

1. `TaskContractor` compiles the trusted user request into a task contract.
2. The benchmark registry supplies the clean capability surface.
3. The engine resolves typed values and records observations as receipts.
4. PLANT checks task-level provenance and commitment structure.
5. WRAP checks the complete proposed effect against the contract and receipts.
6. The engine returns allow, deny, repair, or replan continuation information.

The deterministic checks remain independent of the target model. Model-backed
roles submit typed candidates that must pass the same validation boundary.

## Baselines

`baseline/registry.py` defines the 13 comparison methods. Every method has a
dedicated folder named after the method and an `implementation.py` entry point.
Dependencies with their own runtime are imported lazily so the APEX core and
data adapters can be tested without installing all benchmark environments.

## Verification

```bash
python -m compileall -q src benchmark baseline tests
pytest
```

Before release, also run the anonymity checks in
[ANONYMITY.md](ANONYMITY.md). No credentials, result files, machine-specific
paths, Git remotes, or author metadata should be added to the submission.
