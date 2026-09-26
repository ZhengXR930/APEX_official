"""Closed operator types constrain proposal-local Binding placement."""
import unittest

from apex.defender.contract import (AcquireClause, ConditionalClause,
                                   DeriveClause, EffectClause, TaskContract)
from apex.defender.engine import Episode


class OperatorInputBindingTests(unittest.TestCase):
    def test_add_selects_exact_numeric_span_not_whole_text(self):
        contract = TaskContract("increase rent from 1100 by notice amount", [
            AcquireClause("", "read notice", "read_file", {}, "notice"),
            DeriveClause("", "current rent", ("task",), "current"),
            DeriveClause("", "rent increase", ("c0.notice",), "increase"),
            ConditionalClause("", "compute rent", "add",
                              ("c1.current", "c2.increase"), "rent"),
            EffectClause("", "update rent", "update", {
                "amount": {"from": "c3.rent"}})])
        observed = []

        def place(**request):
            observed.extend(request["goals"])
            rows = []
            for goal in request["goals"]:
                candidate = next(row for row in goal["candidates"]
                                 if row["value"] in {1100, 100})
                rows.append({
                    "goal_id": goal["goal_id"],
                    "candidate_ids": [candidate["candidate_id"]],
                    "compose": "scalar"})
            return {"placements": rows}

        episode = Episode(
            contract, "n", binding_agent=place,
            approval_enabled=False, continuation_enabled=False)
        episode.observe(
            "read_file", {}, "Your monthly rent is increased by 100.00.")
        decision = episode.effect("update", {"amount": 1200})
        self.assertEqual("pass", decision.route)
        self.assertEqual({"number"},
                         {goal["expected_type"] for goal in observed})
        self.assertTrue(any("@" in ref for ref in decision.refs))

    def test_percent_and_multiply_require_numeric_evidence(self):
        contract = TaskContract(
            "pay 19.5 percent of the 1000 purchase plus 5.29", [
                AcquireClause("", "purchase", "recent", {}, "rows"),
                DeriveClause("", "purchase amount", ("c0.rows",), "base"),
                ConditionalClause(
                    "", "percentage", "percent_of",
                    ("c1.base", {"literal": 19.5}), "tax"),
                ConditionalClause(
                    "", "total", "add",
                    ("c2.tax", {"literal": 5.29}), "total"),
                EffectClause("", "pay", "pay", {
                    "amount": {"from": "c3.total"}})])

        def place(**request):
            goal = request["goals"][0]
            selected = next(row for row in goal["candidates"]
                            if row["value"] == 1000)
            self.assertEqual("number", goal["expected_type"])
            return {"placements": [{
                "goal_id": goal["goal_id"],
                "candidate_ids": [selected["candidate_id"]],
                "compose": "scalar"}]}

        episode = Episode(
            contract, "n", binding_agent=place,
            approval_enabled=False, continuation_enabled=False)
        episode.observe("recent", {}, [{"amount": 1000}])
        self.assertEqual(
            "pass", episode.effect("pay", {"amount": 200.29}).route)


if __name__ == "__main__":
    unittest.main()
