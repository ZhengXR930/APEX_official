"""Offline end-to-end checks for adapters, registries, and APEX mediation."""
from __future__ import annotations

from apex.core.manifest import validate_plan
from apex.defender.contract import TaskContract
from apex.defender.engine import Engine
from apex.runtime import ProtectedRuntime
from benchmark.adapter import adapter_for

from .driver import load_case_plan


def _sample(schema):
    if not isinstance(schema, dict):
        return None
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    choices = schema.get("anyOf") or schema.get("oneOf")
    if choices:
        return _sample(choices[0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), "null")
    if kind == "object":
        properties = schema.get("properties") or {}
        return {name: _sample(properties[name])
                for name in schema.get("required") or ()}
    if kind == "array":
        count = max(0, int(schema.get("minItems", 0)))
        return [_sample(schema.get("items") or {}) for _ in range(count)]
    if kind == "integer":
        return int(schema.get("minimum", 0))
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return "preflight"


def _arguments(surface) -> dict:
    schemas = dict(surface.argument_schemas)
    return {name: _sample(schemas[name]) for name in surface.required_arguments}


def _engine() -> Engine:
    return Engine(
        acquire_agent=lambda **_request: None,
        binding_agent=lambda **_request: {"placements": []},
        plant_agent=lambda **_request: {
            "status": "abstain", "placements": [], "reason": "preflight"},
        continuation_explanation_agent=lambda _context: "preflight",
        continuation_enabled=False,
    )


def preflight(benchmark: str, *, data_root=None, samples: int = 2) -> dict:
    """Exercise registry loading and a real deny-side WRAP boundary offline."""
    adapter = adapter_for(benchmark, data_root)
    cases = list(adapter.cases())[:max(1, samples)]
    reports = []
    for case in cases:
        plan = load_case_plan(benchmark, case)
        validate_plan(plan, f"{benchmark}/{case.case_id}")
        effects = [surface for surface in plan.capabilities.values()
                   if surface.effect]
        route = "not-applicable"
        reason = "environment has no effect capability"
        if effects:
            surface = effects[0]
            runtime = ProtectedRuntime(
                _engine(), plan, TaskContract(task="preflight", clauses=[]),
                task_id=f"preflight:{case.case_id}")
            result = runtime.invoke(
                surface.name, _arguments(surface),
                lambda _arguments: (_ for _ in ()).throw(
                    AssertionError("denied effect was executed")))
            route, reason = result.decision.route, result.decision.reason
            if result.executed or route == "pass":
                raise AssertionError(
                    f"empty Contract authorized {surface.name!r}")
            runtime.close()
        reports.append({
            "case_id": case.case_id,
            "condition": case.condition,
            "environment": plan.id,
            "capabilities": len(plan.capabilities),
            "effect_probe": {"route": route, "reason": reason},
        })
    return {
        "benchmark": adapter.benchmark,
        "protocol": adapter.protocol.raw["version"],
        "case_count": len(list(adapter.cases())),
        "sample_count": len(reports),
        "status": "PASS",
        "samples": reports,
    }
