"""Explicit task-time Contract program with exactly four Clause variants."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
import json


_CLAUSE_REF = re.compile(r"^c[0-9]+\.[^.]+$")


def is_clause_ref(value) -> bool:
    return isinstance(value, str) and _CLAUSE_REF.fullmatch(value) is not None


def spec_sources(spec) -> tuple[str, ...]:
    if (not isinstance(spec, dict) or
            set(spec) not in ({"from"}, {"from", "delegated"})):
        return ()
    value = spec["from"]
    values = (value,) if isinstance(value, str) else tuple(value or ())
    return tuple(str(item) for item in values if isinstance(item, str))


def argument_sources(arguments: dict) -> tuple[str, ...]:
    refs = [source for spec in dict(arguments or {}).values()
            for source in spec_sources(spec)]
    if any(not spec_sources(spec) for spec in dict(arguments or {}).values()):
        refs.insert(0, "task")
    return tuple(dict.fromkeys(refs))


class ClauseKind(str, Enum):
    ACQUIRE = "acquire"
    DERIVE = "derive"
    CONDITIONAL = "conditional"
    EFFECT = "effect"


@dataclass(frozen=True)
class Clause:
    """Base type for exactly four explicit Clause variants."""
    id: str
    instruction: str

    @property
    def kind(self) -> ClauseKind:
        raise NotImplementedError

    @property
    def output(self) -> str | None:
        return None

    @property
    def output_ref(self) -> str | None:
        return f"{self.id}.{self.output}" if self.output else None

    @property
    def sources(self) -> list[str]:
        return []

    @property
    def arguments(self) -> dict:
        return {}

    @property
    def relation(self) -> str | None:
        return None

    def with_id(self, clause_id: str) -> "Clause":
        raise NotImplementedError

    def to_dict(self) -> dict:
        raise NotImplementedError


@dataclass(frozen=True)
class AcquireClause(Clause):
    capability: str
    call_arguments: dict
    output_name: str
    quantified: bool = False

    @property
    def kind(self): return ClauseKind.ACQUIRE
    @property
    def output(self): return self.output_name
    @property
    def arguments(self): return self.call_arguments
    @property
    def sources(self):
        return list(dict.fromkeys((self.capability,) + argument_sources(self.call_arguments)))

    def with_id(self, clause_id):
        return AcquireClause(str(clause_id), self.instruction, self.capability,
                             dict(self.call_arguments), self.output_name,
                             self.quantified)

    def to_dict(self):
        value = {"id": self.id, "type": self.kind.value,
                 "instruction": self.instruction,
                 "capability": self.capability,
                 "arguments": dict(self.call_arguments),
                 "output": self.output_name}
        if self.quantified:
            value["quantified"] = True
        return value


@dataclass(frozen=True)
class DeriveClause(Clause):
    input_refs: tuple[str, ...]
    output_name: str
    quantified: bool = False

    @property
    def kind(self): return ClauseKind.DERIVE
    @property
    def output(self): return self.output_name
    @property
    def sources(self): return list(self.input_refs)

    def with_id(self, clause_id):
        return DeriveClause(str(clause_id), self.instruction,
                            tuple(self.input_refs), self.output_name,
                            self.quantified)

    def to_dict(self):
        value = {"id": self.id, "type": self.kind.value,
                 "instruction": self.instruction, "from": list(self.input_refs),
                 "output": self.output_name}
        if self.quantified:
            value["quantified"] = True
        return value


@dataclass(frozen=True)
class ConditionalClause(Clause):
    operator: str
    operands: tuple
    output_name: str

    @property
    def kind(self): return ClauseKind.CONDITIONAL
    @property
    def output(self): return self.output_name
    @property
    def operand_refs(self):
        return tuple(item for item in self.operands if is_clause_ref(item))
    @property
    def sources(self): return list(self.operand_refs)
    @property
    def relation(self):
        encoded = [item if isinstance(item, str) else
                   json.dumps(item, ensure_ascii=False, sort_keys=True)
                   for item in self.operands]
        return f"{self.operator}({','.join(encoded)})"

    def with_id(self, clause_id):
        return ConditionalClause(str(clause_id), self.instruction, self.operator,
                                 tuple(self.operands), self.output_name)

    def to_dict(self):
        return {"id": self.id, "type": self.kind.value,
                "instruction": self.instruction, "operator": self.operator,
                "operands": list(self.operands), "output": self.output_name}


@dataclass(frozen=True)
class EffectClause(Clause):
    action: str
    effect_arguments: dict

    @property
    def kind(self): return ClauseKind.EFFECT
    @property
    def arguments(self): return self.effect_arguments
    @property
    def sources(self): return list(argument_sources(self.effect_arguments))
    def with_id(self, clause_id):
        return EffectClause(str(clause_id), self.instruction, self.action,
                            dict(self.effect_arguments))

    def to_dict(self):
        return {"id": self.id, "type": self.kind.value,
                "instruction": self.instruction, "action": self.action,
                "arguments": dict(self.effect_arguments)}


@dataclass
class TaskContract:
    task: str = ""
    clauses: list[Clause] = field(default_factory=list)

    def __post_init__(self):
        self.clauses = [clause.with_id(f"c{index}")
                        for index, clause in enumerate(self.clauses)]

    def to_dict(self) -> dict:
        return {"task": self.task,
                "clauses": [clause.to_dict() for clause in self.clauses]}

    @classmethod
    def from_dict(cls, data: dict) -> "TaskContract":
        if not isinstance(data, dict) or set(data) != {"task", "clauses"}:
            return cls(str((data or {}).get("task", ""))
                       if isinstance(data, dict) else "", [])
        rows = (data or {}).get("clauses") or []
        explicit_fields = {
            ClauseKind.ACQUIRE.value: {
                "id", "type", "instruction", "capability", "arguments", "output"},
            ClauseKind.DERIVE.value: {
                "id", "type", "instruction", "from", "output"},
            ClauseKind.CONDITIONAL.value: {
                "id", "type", "instruction", "operator", "operands", "output"},
            ClauseKind.EFFECT.value: {
                "id", "type", "instruction", "action", "arguments"},
        }
        if rows and any(
                not isinstance(row, dict) or
                row.get("type") not in explicit_fields or
                set(row) not in (
                    explicit_fields[row["type"]],
                    (explicit_fields[row["type"]] | {"quantified"})
                    if row["type"] in {
                        ClauseKind.ACQUIRE.value,
                        ClauseKind.DERIVE.value,
                    } else set())
                for row in rows):
            return cls(str((data or {}).get("task", "")), [])
        clauses = []
        for index, raw in enumerate(rows):
            if not isinstance(raw, dict):
                continue
            clause_id = f"c{index}"
            instruction = str(raw.get("instruction", ""))
            kind = raw.get("type")
            if kind == ClauseKind.ACQUIRE.value:
                clauses.append(AcquireClause(
                    clause_id, instruction, str(raw.get("capability", "")),
                    dict(raw.get("arguments") or {}), str(raw.get("output", "")),
                    raw.get("quantified") is True))
            elif kind == ClauseKind.DERIVE.value:
                clauses.append(DeriveClause(
                    clause_id, instruction, tuple(map(str, raw.get("from") or ())),
                    str(raw.get("output", "")),
                    raw.get("quantified") is True))
            elif kind == ClauseKind.CONDITIONAL.value:
                clauses.append(ConditionalClause(
                    clause_id, instruction, str(raw.get("operator", "")),
                    tuple(raw.get("operands") or ()),
                    str(raw.get("output", ""))))
            elif kind == ClauseKind.EFFECT.value:
                clauses.append(EffectClause(
                    clause_id, instruction, str(raw.get("action", "")),
                    dict(raw.get("arguments") or {})))
        return cls(str((data or {}).get("task", "")), clauses)
