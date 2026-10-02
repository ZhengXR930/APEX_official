"""Public Engine behavior."""
from unittest.mock import patch

from apex.defender.engine import Engine
from apex.defender.plant_cache import ConcurrentPersistentCache
from apex.defender.taskcontractor import TaskContractor


def _engine():
    return Engine(
        "test-model",
        acquire_agent=lambda **_request: None,
        binding_agent=lambda **_request: {"placements": []},
        plant_agent=lambda **_request: {
            "status": "abstain",
            "placements": [],
            "reason": "test",
        },
        continuation_explanation_agent=lambda _context: "test",
    )


def test_engine_uses_per_key_concurrent_plant_cache():
    assert isinstance(_engine()._plant_cache, ConcurrentPersistentCache)


def test_engine_exposes_defense_ablation_switches():
    engine = Engine(
        acquire_agent=lambda **_request: None,
        binding_agent=lambda **_request: {"placements": []},
        plant_agent=lambda **_request: {
            "status": "abstain", "placements": [], "reason": "test"},
        wrap_enabled=False,
        plant_enabled=False,
    )
    assert engine.wrap_enabled is False
    assert engine.plant_enabled is False


def test_contract_is_synthesized_for_each_call():
    engine = _engine()
    engine.plan = object()
    first, second = object(), object()

    with patch.object(
            TaskContractor, "extract", side_effect=(first, second)) as extract:
        assert engine.contract("same task") is first
        assert engine.contract("same task") is second

    assert extract.call_count == 2
