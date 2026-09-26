"""Manifest-backed mediation for direct and nested capability calls."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from apex.defender.engine import Decision
from apex.defender.memory import canonical_schema_scalar


@dataclass(frozen=True)
class BrokerResult:
    decision: Decision
    value: object = None
    commit: "CommitReceipt | None" = None

    @property
    def executed(self) -> bool:
        return self.commit is not None


@dataclass(frozen=True)
class UnitInvocation:
    id: str
    parent: str
    capability: str
    arguments: dict


@dataclass(frozen=True)
class InvocationDecision:
    invocation_id: str
    route: str
    reason: str = ""


@dataclass(frozen=True)
class CommitReceipt:
    invocation_id: str


@dataclass(frozen=True)
class PreparedInvocation:
    invocation: UnitInvocation
    decision: Decision


class UnitBroker:
    """One recursive invocation boundary for Tool, MCP, and Skill units.

    Every Manifest frontend compiles to the same entry. While an implementation
    runs, a context-local invocation id becomes the parent of any nested call.
    No component type or precomputed call graph participates in authorization.
    """

    def __init__(self, episode, registrations):
        rows = [dict(row) for row in registrations]
        self.episode = episode
        self.registrations = {
            str(row.get("name", "")): row for row in rows
            if str(row.get("name", ""))
        }
        self.capabilities = {
            action: (str(row.get("unit_id") or row.get("x-unit-id") or
                         row.get("x-skill-name") or "").strip("/") + "/" +
                     action if (row.get("unit_id") or row.get("x-unit-id") or
                                row.get("x-skill-name")) else action)
            for action, row in self.registrations.items()}
        self._current: ContextVar[str] = ContextVar(
            f"unit-parent-{id(self)}", default="")
        self._sequence = 0
        self._invocations: list[UnitInvocation] = []
        self._decisions: list[InvocationDecision] = []
        self._commits: list[CommitReceipt] = []
        self._actions: dict[str, str] = {}
        self._effect_clause_ids: dict[str, str] = {}

    @classmethod
    def _materialize_defaults(cls, value, schema):
        """Insert defaults and canonicalize scalars under operator schema."""
        if not isinstance(schema, dict):
            return deepcopy(value)
        if isinstance(value, dict):
            out = deepcopy(value)
            properties = schema.get("properties") or {}
            if isinstance(properties, dict):
                for name, child_schema in properties.items():
                    if name in out:
                        out[name] = cls._materialize_defaults(
                            out[name], child_schema)
                    elif (isinstance(child_schema, dict) and
                          "default" in child_schema):
                        out[name] = deepcopy(child_schema["default"])
            return out
        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            return [cls._materialize_defaults(item, schema["items"])
                    for item in value]
        return deepcopy(canonical_schema_scalar(schema, value))

    @classmethod
    def _schema_error(cls, schema, value, path: str = "$") -> str:
        """Return a deterministic schema denial reason for canonical args."""
        if not isinstance(schema, dict) or not schema:
            return ""
        raw_types = schema.get("type")
        types = tuple(raw_types if isinstance(raw_types, list)
                      else (() if raw_types is None else (raw_types,)))
        if not types:
            return ""
        if "null" in types and value is None:
            return ""

        def reason(kind: str) -> str:
            name = path if path != "$" else "arguments"
            return f"schema-{kind}-arg:{name}"

        valid_type = False
        for schema_type in types:
            if schema_type == "object":
                valid_type = isinstance(value, dict)
            elif schema_type == "array":
                valid_type = isinstance(value, list)
            elif schema_type == "string":
                valid_type = isinstance(value, str)
            elif schema_type == "integer":
                valid_type = isinstance(value, int) and not isinstance(value, bool)
            elif schema_type == "number":
                valid_type = ((isinstance(value, int) or isinstance(value, float)) and
                              not isinstance(value, bool))
            elif schema_type == "boolean":
                valid_type = isinstance(value, bool)
            else:
                valid_type = True
            if valid_type:
                break
        if not valid_type:
            return reason("invalid")

        if isinstance(value, dict):
            properties = schema.get("properties") or {}
            required = tuple(map(str, schema.get("required") or ()))
            missing = next((name for name in required if name not in value), "")
            if missing:
                return f"schema-missing-arg:{missing}"
            if schema.get("additionalProperties") is False:
                extra = next((name for name in value if name not in properties), "")
                if extra:
                    return f"schema-unexpected-arg:{extra}"
            if isinstance(properties, dict):
                for name, child in properties.items():
                    if name in value:
                        child_path = str(name) if path == "$" else f"{path}.{name}"
                        error = cls._schema_error(child, value[name], child_path)
                        if error:
                            return error
        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                error = cls._schema_error(
                    schema["items"], item, f"{path}[{index}]")
                if error:
                    return error
        return ""

    def _input_schema(self, action: str) -> dict:
        registration = self.registrations.get(str(action), {})
        schema = registration.get("inputSchema")
        if isinstance(schema, dict):
            return schema
        surface = getattr(self.episode, "capabilities", {}).get(str(action))
        if surface is None:
            return {}
        properties = {}
        for name in getattr(surface, "arguments", ()):
            child = surface.argument_schema(name)
            if isinstance(child, dict):
                properties[str(name)] = child
        return {"type": "object", "properties": properties}

    def _canonical_arguments(self, action: str, arguments: dict):
        value = dict(arguments or {})
        identities = ()
        resolve = getattr(self.episode, "resolve_identity_handles", None)
        if callable(resolve):
            value, identities = resolve(value, include_identities=True)
        schema = deepcopy(self._input_schema(str(action)))
        surface = getattr(self.episode, "capabilities", {}).get(str(action))
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if surface is not None and isinstance(properties, dict):
            # These roles are byte-level identities, not interchangeable
            # scalar encodings.  A non-string proposal must be rejected.
            for name, child in properties.items():
                kind = surface.argument_type(name)
                if (kind in {"code", "path", "identity"} or
                        str(kind).startswith("code/")):
                    if isinstance(child, dict):
                        child["x-scalar-coercion"] = False
        canonical = self._materialize_defaults(value, schema)
        return (canonical if isinstance(canonical, dict) else value,
                tuple(identities))

    def canonical_arguments(self, action: str, arguments: dict) -> dict:
        """Canonical invocation arguments shared by mediation and execution."""
        return self._canonical_arguments(action, arguments)[0]

    def _new_invocation(self, action: str,
                        arguments: dict) -> UnitInvocation:
        self._sequence += 1
        invocation = UnitInvocation(
            f"UNIT-{self._sequence:06d}", self._current.get(),
            self.capabilities.get(action, "$unregistered/" + action),
            dict(arguments))
        self._actions[invocation.id] = action
        return invocation

    def authorize(self, action: str, arguments: dict, *,
                  proof_refs=(), identities=()) -> Decision:
        action = str(action)
        arguments = dict(arguments or {})
        registration = self.registrations.get(action)
        if registration is None:
            # Unknown calls are effects from WRAP's perspective: an
            # unregistered helper never gains an observation-only bypass.
            return self.episode.effect(
                action, arguments, proof_refs=proof_refs,
                identities=identities)
        if bool(registration.get("effect")):
            return self.episode.effect(
                action, arguments, proof_refs=proof_refs,
                identities=identities)
        return self.episode.commit(
            "call", action, arguments, proof_refs=proof_refs,
            identities=identities)

    def prepare(self, action: str, arguments: dict, *, proof_refs=(),
                identities=()) -> PreparedInvocation:
        """Create one auditable invocation and mediate its exact proposal."""
        action = str(action)
        arguments, carrier_identities = self._canonical_arguments(
            action, arguments)
        invocation = self._new_invocation(action, arguments)
        schema_error = self._schema_error(self._input_schema(action), arguments)
        if schema_error:
            decision = Decision("deny", schema_error)
        else:
            decision = self.authorize(
                action, arguments, proof_refs=proof_refs,
                identities=tuple(dict.fromkeys(
                    (*tuple(identities or ()), *carrier_identities))))
        self._invocations.append(invocation)
        self._effect_clause_ids[invocation.id] = decision.effect_clause_id
        self._decisions.append(InvocationDecision(
            invocation.id, decision.route, decision.reason))
        return PreparedInvocation(invocation, decision)

    def record_decision(self, prepared: PreparedInvocation,
                        decision: Decision, arguments=None) -> None:
        """Record the final route and the exact arguments it governs."""
        if arguments is not None:
            current = prepared.invocation
            action = self._actions[prepared.invocation.id]
            updated = UnitInvocation(
                current.id, current.parent, current.capability,
                self.canonical_arguments(action, arguments))
            for index, existing in enumerate(self._invocations):
                if existing.id == current.id:
                    self._invocations[index] = updated
                    break
            else:
                raise ValueError("arguments refer to an unknown invocation")
        row = InvocationDecision(
            prepared.invocation.id, decision.route, decision.reason)
        self._effect_clause_ids[prepared.invocation.id] = (
            decision.effect_clause_id)
        for index, existing in enumerate(self._decisions):
            if existing.invocation_id == prepared.invocation.id:
                self._decisions[index] = row
                return
        raise ValueError("decision refers to an unknown invocation")

    def invoke(self, action: str, arguments: dict,
               implementation: Callable[[], object], *,
               proof_refs=(), identities=()) -> BrokerResult:
        """Authorize one exact proposal, then and only then execute it."""
        prepared = self.prepare(
            action, arguments, proof_refs=proof_refs,
            identities=identities)
        arguments = prepared.invocation.arguments
        decision = prepared.decision
        if decision.route != "pass":
            return BrokerResult(decision)
        with self.execution(prepared):
            value = implementation()
        self.record_decision(prepared, decision, arguments)
        self.succeeded(prepared, arguments)
        return BrokerResult(decision, value, self._commits[-1])

    @contextmanager
    def execution(self, prepared: PreparedInvocation):
        """Make nested calls children of an already mediated invocation."""
        token = self._current.set(prepared.invocation.id)
        try:
            yield
        finally:
            self._current.reset(token)

    def succeeded(self, prepared: PreparedInvocation, arguments=None) -> None:
        action = self._actions[prepared.invocation.id]
        arguments = self.canonical_arguments(
            action, arguments or prepared.invocation.arguments)
        registration = self.registrations.get(action, {})
        if bool(registration.get("effect")):
            self.episode.effect_succeeded(
                action, arguments, invocation_id=prepared.invocation.id,
                clause_id=self._effect_clause_ids.get(
                    prepared.invocation.id, ""))
        self._commits.append(CommitReceipt(prepared.invocation.id))

    def invocation_receipts(self) -> dict:
        return {
            "invocations": [vars(item) for item in self._invocations],
            "decisions": [vars(item) for item in self._decisions],
            "commits": [vars(item) for item in self._commits],
        }


class CapabilityBroker(UnitBroker):
    """Backward-compatible name; new integrations should use UnitBroker."""
