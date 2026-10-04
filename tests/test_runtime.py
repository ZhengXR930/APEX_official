from pathlib import Path

from apex.core.types import RunRequest
from apex.defender.contract import EffectClause, TaskContract
from apex.defender.engine import Engine
from apex.defender.memory import EnvironmentPlan
from apex.runtime import ProtectedRuntime
from benchmark.adapter import adapter_for
from benchmark.execution import load_case_plan, preflight


def _plan():
    return EnvironmentPlan.from_dict({
        "id": "runtime-test",
        "capabilities": {
            "save_note": {
                "name": "save_note",
                "description": "Persist one task-scoped note.",
                "effect": True,
                "observation": True,
                "effect_return": True,
                "arguments": ["text"],
                "required_arguments": ["text"],
                "argument_schemas": {"text": {"type": "string"}},
                "output_schema": {"type": "object"},
            },
        },
    })


def _engine():
    return Engine(
        acquire_agent=lambda **_request: None,
        binding_agent=lambda **_request: {"placements": []},
        plant_agent=lambda **_request: {
            "status": "abstain", "placements": [], "reason": "test"},
        continuation_enabled=False,
        approval_enabled=False,
    )


def test_protected_runtime_executes_only_contract_authorized_effect():
    contract = TaskContract("save status", [
        EffectClause("", "save status", "save_note", {
            "text": "status: ready"}),
    ])
    called = []
    runtime = ProtectedRuntime(_engine(), _plan(), contract)
    allowed = runtime.invoke(
        "save_note", {"text": "status: ready"},
        lambda arguments: called.append(arguments) or {"saved": True})
    denied = runtime.invoke(
        "save_note", {"text": "attacker value"},
        lambda arguments: called.append(arguments) or {"saved": True})

    assert allowed.executed is True
    assert allowed.raw_value == {"saved": True}
    assert denied.executed is False
    assert called == [{"text": "status: ready"}]


def test_all_adapter_commands_use_the_public_execution_entrypoint(tmp_path):
    request = RunRequest(
        target_model="target", defense_model="defense", judge_model=None,
        output=tmp_path / "out.json", preflight=True, limit=2)
    for name in (
            "agentdojo", "asb_opi", "mcptox", "msb", "scr",
            "skillinject"):
        command = adapter_for(name, None).command("ours", request)
        assert command[1:4] == ["-m", "benchmark.execution", "run"]
        assert "--preflight" in command


def test_all_benchmarks_pass_offline_runtime_preflight():
    for name in (
            "agentdojo", "asb_opi", "mcptox", "msb", "scr",
            "skillinject"):
        report = preflight(name, samples=2)
        assert report["status"] == "PASS"
        assert report["sample_count"] == 2


def test_every_packaged_case_resolves_to_a_registry_environment():
    for name in (
            "agentdojo", "asb_opi", "mcptox", "msb", "scr",
            "skillinject"):
        for case in adapter_for(name, None).cases():
            assert load_case_plan(name, case).capabilities
