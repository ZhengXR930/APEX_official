from apex.defender.contract import (AcquireClause, DeriveClause, EffectClause,
                                    TaskContract)
from apex.defender.engine import Episode
from apex.defender.memory import CapabilitySurface
from apex.defender.state import GROUNDED_REF


def _numeric_surface():
    return CapabilitySurface.from_dict({
        "name": "update_rent",
        "effect": True,
        "arguments": ["amount"],
        "required_arguments": ["amount"],
        "inputSchema": {
            "type": "object",
            "properties": {"amount": {"anyOf": [
                {"type": "number"}, {"type": "null"}]}},
            "required": ["amount"],
        },
    })


def test_direct_derive_closes_semantically():
    contract = TaskContract("increase the current rent per the notice", [
        AcquireClause("", "rent notice", "read_notice", {}, "notice"),
        AcquireClause("", "current rent", "read_rent", {}, "rent"),
        DeriveClause(
            "", "apply the notice-stated change to the current rent",
            ("c0.notice", "c1.rent"), "adjusted_rent"),
        EffectClause("", "update rent", "update_rent", {
            "amount": {"from": "c2.adjusted_rent"}}),
    ])
    seen = []

    def place(**request):
        goal = request["goals"][0]
        seen.append(goal)
        selected = [
            row["candidate_id"] for row in goal["candidates"]
            if row["value"] in {"increase by 100", 1100}
        ]
        return {"placements": [{
            "goal_id": goal["goal_id"],
            "candidate_ids": selected,
            "compose": "scalar",
        }]}

    episode = Episode(
        contract, "n", capabilities={"update_rent": _numeric_surface()},
        binding_agent=place, approval_enabled=False,
        continuation_enabled=False, plant_enabled=False)
    episode.observe("read_notice", {}, "increase by 100")
    episode.observe("read_rent", {}, 1100)
    decision = episode.effect("update_rent", {"amount": 1200})

    assert decision.route == "pass"
    assert seen[0]["support_mode"] == "exact_or_semantic"
    assert decision.refs[:1] == (GROUNDED_REF,)

