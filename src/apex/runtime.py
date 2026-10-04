"""Framework-neutral runtime bridge for Tool, MCP, and Skill integrations.

The benchmark runtimes in the research repository share one state machine:
load an operator-owned capability plan, synthesize a Contract for the current
task, mediate every invocation, record successful operations, and route the
final response through PLANT.  This module exposes that common path without
importing benchmark packages or experiment artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from apex.defender.broker import CommitReceipt, UnitBroker
from apex.defender.contract import TaskContract
from apex.defender.engine import Decision, Engine
from apex.defender.memory import EnvironmentPlan


def registrations_from_plan(plan: EnvironmentPlan) -> list[dict]:
    """Materialize broker registrations from a trusted environment plan."""
    rows = []
    for name, surface in plan.capabilities.items():
        rows.append({
            "name": name,
            "description": surface.description,
            "inputSchema": {
                "type": "object",
                "properties": dict(surface.argument_schemas),
                "required": list(surface.required_arguments),
                "additionalProperties": False,
            },
            "outputSchema": dict(surface.output_schema or {"type": "null"}),
            "effect": surface.effect,
            "observation": surface.observation,
            "effect_return": surface.effect_return,
            "receipt_role": surface.receipt_role,
            "argument_types": dict(surface.argument_types),
            "output_types": dict(surface.output_types),
        })
    return rows


@dataclass(frozen=True)
class RuntimeResult:
    """One mediated native invocation and its model-visible return value."""

    decision: Decision
    value: object = None
    raw_value: object = None
    commit: CommitReceipt | None = None

    @property
    def executed(self) -> bool:
        return self.commit is not None


class ProtectedRuntime:
    """Run one task through the APEX boundary.

    Native adapters retain ownership of their environment and implementations.
    They call :meth:`invoke` for every direct or nested capability invocation
    and :meth:`response` for the final agent response. The runtime never
    fabricates a Tool result or silently grants approval.
    """

    def __init__(self, engine: Engine, plan: EnvironmentPlan,
                 contract: TaskContract, *, task_id: str | None = None,
                 approver: Callable[[dict], bool] | None = None):
        self.engine = engine
        self.plan = engine.attach_plan(plan)
        self.contract = contract
        self.episode = engine.start(contract, task_id=task_id)
        self.registrations = registrations_from_plan(plan)
        self.broker = UnitBroker(self.episode, self.registrations)
        self.approver = approver

    @classmethod
    def for_task(cls, engine: Engine, plan: EnvironmentPlan, task: str, *,
                 task_id: str | None = None, effect_entries=None,
                 approver: Callable[[dict], bool] | None = None):
        """Generate the current task's Contract and start its episode."""
        engine.attach_plan(plan)
        contract = engine.contract(task, effect_entries=effect_entries)
        return cls(engine, plan, contract, task_id=task_id, approver=approver)

    def _settle(self, capability: str, prepared, *, proof_refs=(), identities=()):
        decision = prepared.decision
        if decision.route == "approval" and self.approver is not None:
            approved = bool(self.approver(dict(decision.approval)))
            self.episode.decide_approval(decision.approval_id, approved)
            prepared = self.broker.prepare(
                capability, prepared.invocation.arguments,
                proof_refs=proof_refs, identities=identities)
            decision = prepared.decision
        if decision.continuation_id:
            decision = self.episode.continue_decision(decision)
        return prepared, decision

    def invoke(self, capability: str, arguments: dict,
               implementation: Callable[[dict], object], *,
               proof_refs=(), identities=(), consumer: str | None = None,
               placement_schema=None) -> RuntimeResult:
        """Mediate and, only on ``pass``, execute one native capability.

        ``implementation`` receives the canonical arguments authorized by
        APEX. Observation returns become Receipts and pass through PLANT before
        they are returned to the target agent.
        """
        prepared = self.broker.prepare(
            capability, arguments, proof_refs=proof_refs,
            identities=identities)
        prepared, decision = self._settle(
            capability, prepared, proof_refs=proof_refs,
            identities=identities)
        authorized = (dict(decision.authorized_arguments)
                      if decision.authorized_arguments else
                      dict(prepared.invocation.arguments))
        self.broker.record_decision(prepared, decision, authorized)
        if decision.route != "pass":
            return RuntimeResult(decision)

        with self.broker.execution(prepared):
            raw = implementation(authorized)
        self.broker.succeeded(prepared, authorized)
        if decision.approval_id:
            self.episode.approval_succeeded(decision.approval_id)

        surface = self.plan.capabilities.get(str(capability))
        value = raw
        if surface is not None and surface.observation:
            value = self.episode.observe(
                capability, authorized, raw, consumer=consumer,
                placement_schema=placement_schema)
        return RuntimeResult(
            decision, value=value, raw_value=raw,
            commit=self.broker._commits[-1])

    def response(self, value, *, proof_refs=()) -> Decision:
        """Mediate the final response/artifact commitment."""
        decision = self.episode.response(value, proof_refs=proof_refs)
        if decision.continuation_id:
            decision = self.episode.continue_decision(decision)
        return decision

    def receipts(self) -> dict:
        """Return the code-owned invocation audit trail."""
        return self.broker.invocation_receipts()

    def close(self) -> dict:
        return self.episode.close()

    def __enter__(self):
        self.episode.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self.episode.__exit__(exc_type, exc, traceback)
