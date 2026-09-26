"""Deterministic clause-output resolution from bound receipts.

Acquire outputs are copied from their receipt; Conditional outputs are computed
by replaying the closed operator over already-resolved operands.  Both are pure
functions of runtime state and need no model — this is the fast common path.

Derive is the only genuinely semantic role.  A Derive is resolved by one
validated binding-agent call: the agent proposes which receipt refs the value
derives from, deterministic code checks those refs exist and that the value
introduces no entity absent from them, and binds it.  A Derive that cannot be
grounded is left UNRESOLVED — never guessed, no fallback.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
import re

from dataclasses import dataclass
from itertools import product

from apex.defender.contract import (AcquireClause, ConditionalClause,
                                   DeriveClause)
from apex.defender.state import (Binding, CONTEXT_REF, QUERY_REF, SEMANTIC_REF,
                                Receipt, RuntimeState, UNRESOLVED, stable)

_TRUSTED_INPUT_REFS = {"task": QUERY_REF, "runtime-context": CONTEXT_REF}


def operator_operand_type(operator: str, index: int) -> str:
    """Return the closed operator's structural requirement for one operand.

    This is part of the operator algebra, not a benchmark policy.  ``any``
    means the operator imposes no useful precondition at that position.
    """
    if operator in {"add", "multiply", "percent_of"}:
        return "number"
    if operator in {"gt", "lt"}:
        return "number" if index in {1, 2} else "any"
    if operator in {"argmax", "argmin"}:
        return "number-list" if index == 1 else "collection"
    if operator in {"count", "frequency"}:
        return "collection"
    if operator in {"map_count", "flatten"}:
        return "collection-list"
    if operator == "union":
        return "collection-list"
    if operator == "difference":
        return "collection"
    if operator == "keys":
        return "object"
    if operator == "field":
        return "string" if index == 1 else "object"
    if operator == "project":
        return "string" if index == 1 else "collection"
    if operator == "object_set":
        return "object" if index == 0 else "string" if index == 1 else "any"
    if operator == "sort_by":
        return "collection"
    if operator == "select_eq":
        return ("collection" if index == 0 else
                "string" if index == 1 else "any")
    if operator == "interval_free":
        return "collection" if index == 0 else "datetime"
    if operator in {"normalize_date", "datetime_combine", "add_duration", "basename",
                    "path_join"}:
        return "string"
    if operator == "aligned_lookup":
        return "collection" if index in {0, 1} else "any"
    return "any"


def _is_number(value) -> bool:
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple)):
        return False
    try:
        Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return True


def operator_value_matches(kind: str, value) -> bool:
    """Validate a materialized value against an operator operand type."""
    if kind == "any":
        return True
    if kind == "number":
        return _is_number(value)
    if kind == "number-list":
        return (isinstance(value, (list, tuple)) and bool(value) and
                all(_is_number(item) for item in value))
    if kind == "collection":
        return isinstance(value, (list, tuple, dict))
    if kind == "collection-list":
        return (isinstance(value, (list, tuple)) and
                all(isinstance(item, (list, tuple, dict)) for item in value))
    if kind == "object":
        return isinstance(value, dict)
    if kind == "string":
        return isinstance(value, str)
    if kind == "datetime":
        if not isinstance(value, str):
            return False
        try:
            datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError:
            return False
        return True
    raise ValueError(f"unknown operator operand type: {kind}")


def _exact_task_span(task: str, value) -> bool:
    """Return true only for a complete scalar token literally present in task."""
    if not isinstance(value, str) or not value:
        return False
    return re.search(
        r"(?<![\w])" + re.escape(value) + r"(?![\w])", str(task)) is not None


def _operand_refs(state: RuntimeState, refs) -> tuple[str, ...]:
    out: list[str] = []
    for ref in refs:
        binding = state.bindings.get(str(ref).partition(".")[0])
        if binding is not None:
            out.extend(binding.refs)
    return tuple(dict.fromkeys(out))


def replay_operator(operator: str, operands: list):
    """Replay one closed Conditional operator; raise on an unknown operator."""
    if operator == "identity":
        return operands[0]
    if operator == "coalesce":
        return next((value for value in operands if value is not UNRESOLVED),
                    UNRESOLVED)
    if operator == "singleton":
        return [operands[0]]
    if operator == "count":
        return len(operands[0])
    if operator == "map_count":
        groups = operands[0]
        if not isinstance(groups, (list, tuple)):
            raise ValueError("map_count needs a collection of collections")
        if any(not isinstance(group, (list, tuple, dict))
               for group in groups):
            raise ValueError("map_count items must be collections")
        return [len(group) for group in groups]
    if operator == "flatten":
        groups = operands[0]
        if not isinstance(groups, (list, tuple)) or any(
                not isinstance(group, (list, tuple)) for group in groups):
            raise ValueError("flatten needs a collection of collections")
        return [item for group in groups for item in group]
    if operator == "union":
        groups = operands[0]
        if not isinstance(groups, (list, tuple)) or any(
                not isinstance(group, (list, tuple)) for group in groups):
            raise ValueError("union needs a collection of collections")
        merged: list = []
        for group in groups:
            for item in group:
                if item not in merged:
                    merged.append(item)
        return merged
    if operator == "difference":
        removed = operands[1]
        return [item for item in operands[0] if item not in removed]
    if operator == "field":
        value, field = operands
        if not isinstance(field, str):
            raise ValueError("field name must be a string")
        if isinstance(value, dict) and field in value:
            return value[field]
        if hasattr(value, field):
            return getattr(value, field)
        raise ValueError("field is absent")
    if operator == "keys":
        value = operands[0]
        if not isinstance(value, dict):
            raise ValueError("keys needs an object")
        return list(value)
    if operator == "project":
        items, field = operands
        if not isinstance(items, (list, tuple)) or not isinstance(field, str):
            raise ValueError("project needs a collection and field")
        projected = []
        for item in items:
            if isinstance(item, dict) and field in item:
                projected.append(item[field])
            elif hasattr(item, field):
                projected.append(getattr(item, field))
            else:
                raise ValueError("project field is absent")
        return projected
    if operator == "object_set":
        value, field, child = operands
        if not isinstance(value, dict) or not isinstance(field, str):
            raise ValueError("object_set needs an object and field")
        return {**value, field: child}
    if operator == "frequency":
        items = operands[0]
        if not isinstance(items, (list, tuple)):
            raise ValueError("frequency needs a collection")
        records, positions = [], {}
        for item in items:
            key = stable(item)
            if key not in positions:
                positions[key] = len(records)
                records.append({"value": item, "count": 0})
            records[positions[key]]["count"] += 1
        return records
    if operator == "sort_by":
        items, fields, directions = operands
        if (not isinstance(items, (list, tuple)) or
                not isinstance(fields, (list, tuple)) or not fields or
                not isinstance(directions, (list, tuple)) or
                len(fields) != len(directions) or
                any(direction not in {"asc", "desc"}
                    for direction in directions)):
            raise ValueError("sort_by needs aligned fields and directions")
        result = list(items)
        for field, direction in reversed(list(zip(fields, directions))):
            if not isinstance(field, str):
                raise ValueError("sort_by fields must be strings")
            try:
                result.sort(
                    key=lambda item: (item[field] if isinstance(item, dict)
                                      else getattr(item, field)),
                    reverse=direction == "desc")
            except (KeyError, AttributeError, TypeError):
                raise ValueError("sort_by field is absent or incomparable")
        return result
    if operator == "select_eq":
        items, field, expected = operands
        if not isinstance(items, (list, tuple)) or not isinstance(field, str):
            raise ValueError("select_eq needs a collection and field")

        def equal(left, right):
            if isinstance(left, str) and isinstance(right, str):
                return left.casefold() == right.casefold()
            if (_is_number(left) and _is_number(right)):
                return Decimal(str(left)) == Decimal(str(right))
            return type(left) is type(right) and left == right

        matches = []
        for item in items:
            actual = (item.get(field, UNRESOLVED)
                      if isinstance(item, dict)
                      else getattr(item, field, UNRESOLVED))
            if actual is not UNRESOLVED and equal(actual, expected):
                matches.append(item)
        if len(matches) != 1:
            raise ValueError("select_eq requires one unique match")
        return matches[0]
    if operator == "add":
        if any(isinstance(value, bool) for value in operands):
            raise ValueError("add operands must be numeric")
        try:
            total = sum(Decimal(str(value)) for value in operands)
        except (InvalidOperation, ValueError):
            raise ValueError("add operands must be numeric")
        return int(total) if total == total.to_integral() else float(total)
    if operator in {"multiply", "percent_of"}:
        if any(isinstance(value, bool) for value in operands):
            raise ValueError(f"{operator} operands must be numeric")
        try:
            left, right = (Decimal(str(value)) for value in operands)
            result = left * right
            if operator == "percent_of":
                result /= Decimal(100)
        except (InvalidOperation, ValueError):
            raise ValueError(f"{operator} operands must be numeric")
        return (int(result) if result == result.to_integral()
                else float(result))
    if operator == "normalize_date":
        if len(operands) != 1 or not isinstance(operands[0], str):
            raise ValueError("normalize_date needs one string")
        raw = re.sub(r"(?i)(?<=\d)(?:st|nd|rd|th)\b", "", operands[0])
        raw = re.sub(r"\s+", " ", raw.strip().replace(",", ""))
        formats = (
            "%Y-%m-%d", "%Y/%m/%d",
            "%B %d %Y", "%b %d %Y",
            "%d %B %Y", "%d %b %Y",
        )
        for fmt in formats:
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        raise ValueError("unsupported date")
    if operator == "datetime_combine":
        date, time = map(str, operands)
        return datetime.fromisoformat(date + "T" + time).strftime(
            "%Y-%m-%d %H:%M")
    if operator == "add_duration":
        start, duration = operands
        match = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)?\s*(?:-\s*)?"
            r"(minute|minutes|hour|hours)\s*",
            str(duration), re.I)
        if not match:
            words = {"one": 1, "two": 2, "three": 3, "four": 4}
            match = re.fullmatch(
                r"\s*(one|two|three|four)\s*(?:-\s*)?"
                r"(minute|minutes|hour|hours)\s*",
                str(duration), re.I)
            if not match:
                raise ValueError("unsupported duration")
            amount = Decimal(words[match.group(1).casefold()])
        else:
            amount = Decimal(match.group(1) or "1")
        unit = match.group(2).casefold()
        minutes = float(amount * (60 if unit.startswith("hour") else 1))
        value = datetime.fromisoformat(str(start).replace(" ", "T"))
        return (value + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
    if operator == "interval_free":
        events, start, end = operands
        if not isinstance(events, (list, tuple)):
            raise ValueError("interval_free events must be a collection")
        lower = datetime.fromisoformat(str(start).replace(" ", "T"))
        upper = datetime.fromisoformat(str(end).replace(" ", "T"))
        if lower >= upper:
            raise ValueError("interval_free requires a positive interval")
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("interval_free event must be an object")
            event_start = datetime.fromisoformat(
                str(event["start_time"]).replace(" ", "T"))
            event_end = datetime.fromisoformat(
                str(event["end_time"]).replace(" ", "T"))
            if lower < event_end and event_start < upper:
                return UNRESOLVED
        return start
    if operator in ("gt", "lt"):
        candidate, score, threshold = operands
        if isinstance(score, bool) or isinstance(threshold, bool):
            raise ValueError("gt/lt operands must be numeric")
        try:
            left, right = Decimal(str(score)), Decimal(str(threshold))
        except (InvalidOperation, ValueError):
            raise ValueError("gt/lt operands must be numeric")
        passed = left > right if operator == "gt" else left < right
        return candidate if passed else UNRESOLVED
    if operator in ("argmax", "argmin"):
        items, scores = operands[0], operands[1]
        if len(items) != len(scores) or not items:
            raise ValueError("argmax/argmin need aligned non-empty operands")
        pick = max if operator == "argmax" else min
        index = pick(range(len(scores)), key=lambda i: scores[i])
        return items[index]
    if operator == "aligned_lookup":
        keys, values, selected = operands
        if (not isinstance(keys, (list, tuple)) or
                not isinstance(values, (list, tuple)) or
                len(keys) != len(values)):
            raise ValueError("aligned_lookup needs aligned collections")
        matches = [index for index, key in enumerate(keys)
                   if key == selected and type(key) is type(selected)]
        if len(matches) != 1:
            raise ValueError("aligned_lookup requires one unique key")
        return values[matches[0]]
    if operator == "basename":
        return str(operands[0]).rstrip("/").rsplit("/", 1)[-1]
    if operator == "path_join":
        return str(operands[0]).rstrip("/") + "/" + str(operands[1])
    raise ValueError(f"unknown Conditional operator: {operator}")


def resolve_conditional(state: RuntimeState,
                        clause: ConditionalClause) -> Binding | None:
    operands = [
        item["literal"] if isinstance(item, dict) else state.output(item)
        for item in clause.operands]
    if any(operand is UNRESOLVED for operand in operands):
        return None
    try:
        value = replay_operator(clause.operator, operands)
    except (TypeError, ValueError):
        # A closed proof that cannot be replayed is simply unresolved.  Invalid
        # runtime operands must fail closed at WRAP, not abort the whole task.
        return None
    if value is UNRESOLVED:
        return None
    return state.bind(Binding(clause.id, "conditional", value,
                              _operand_refs(state, clause.operand_refs)))


def resolve_derive(state: RuntimeState, clause: DeriveClause, value, *,
                   task: str = "", ground=None) -> Binding | None:
    """Resolve one semantic Derive output through the single validated agent.

    Inputs are the clause's declared sources: earlier clause outputs (backed by
    their receipt refs) and the trusted origins ``task``/``runtime-context``.
    ``ground`` is ``(task, instruction, inputs, value) -> bool`` — the agent
    judges only whether ``value`` is a faithful, task-authorized instantiation
    of the role over these inputs; deterministic code owns the provenance refs
    (always the inputs' own refs, never anything the agent supplies).  A false
    judgment, a missing agent, or an unresolved input binds nothing.
    """
    inputs: dict = {}
    refs: list[str] = []
    for ref in clause.input_refs:
        if ref in _TRUSTED_INPUT_REFS:
            inputs[ref] = ref
            refs.append(_TRUSTED_INPUT_REFS[ref])
            continue
        resolved = state.output(ref)
        if resolved is UNRESOLVED:
            return None
        inputs[ref] = resolved
        binding = state.bindings.get(str(ref).partition(".")[0])
        if binding is not None:
            refs.extend(binding.refs)
    # A task-sourced exact text span is already a deterministic authority
    # witness.  Do not ask the semantic agent to rediscover or reject it.
    if "task" in clause.input_refs and _exact_task_span(task, value):
        return state.bind(Binding(clause.id, "derive", value,
                                  tuple(dict.fromkeys(refs))))
    if ground is None:
        return None
    judgment = ground(
        task=task, instruction=clause.instruction, inputs=inputs, value=value)
    grounded = (judgment.get("grounded") is True
                if isinstance(judgment, dict) else judgment is True)
    if not grounded:
        return None
    semantic_refs = (SEMANTIC_REF, *(ref for ref in refs
                                     if ref not in {QUERY_REF, CONTEXT_REF}))
    return state.bind(Binding(clause.id, "derive", value,
                              tuple(dict.fromkeys(semantic_refs))))


@dataclass(frozen=True)
class Resolved:
    """One proposal-local value over the current unordered Receipt snapshot."""
    value: object
    refs: tuple[str, ...]
    receipt: Receipt | None = None


def _value_nodes(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _value_nodes(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _value_nodes(child)


def _exact_paths(value, target, path=""):
    """Yield JSON-pointer paths whose complete node equals ``target``."""
    if type(value) is type(target) and value == target:
        yield path
    if isinstance(value, dict):
        for key, child in value.items():
            part = str(key).replace("~", "~0").replace("/", "~1")
            yield from _exact_paths(child, target, path + "/" + part)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _exact_paths(child, target, path + "/" + str(index))
class LazyResolver:
    """Order-independent Clause evaluation for one proposal/snapshot.

    Persistent state owns only Clause→Receipt edges. Semantic placements and
    derived values live in this resolver's memo and are discarded after the
    proposal. A later Receipt therefore changes the snapshot rather than being
    ignored behind a first-arrival Binding.
    """

    def __init__(self, state: RuntimeState, contract, placements=None):
        self.state = state
        self.contract = contract
        self.placements = dict(placements or {})
        self.memo: dict[str, tuple[Resolved, ...]] = {}
        self.by_ref = {
            clause.output_ref: clause for clause in contract.clauses
            if clause.output_ref}

    @staticmethod
    def _equal(left, right) -> bool:
        return type(left) is type(right) and left == right

    def call_matches(self, clause: AcquireClause, arguments: dict) -> bool:
        """Whether call arguments follow literals/current upstream values."""
        arguments = dict(arguments or {})
        for name, spec in clause.call_arguments.items():
            if name not in arguments:
                return False
            proposed = arguments[name]
            if isinstance(spec, dict) and set(spec) == {"literal"}:
                if spec["literal"] != proposed:
                    return False
                continue
            if isinstance(spec, dict) and set(spec) == {"from"}:
                raw = spec["from"]
                sources = [raw] if isinstance(raw, str) else list(raw or ())
                values = [item for source in sources for item in self.values(source)]
                if not values or not any(
                        self._equal(node, proposed)
                        for item in values for node in _value_nodes(item.value)):
                    return False
                continue
            if spec != proposed:
                return False
        return True

    def values(self, ref: str, resolving=frozenset()) -> tuple[Resolved, ...]:
        ref = str(ref)
        if ref in self.memo:
            return self.memo[ref]
        if ref in resolving:
            return ()
        clause = self.by_ref.get(ref)
        if clause is None:
            return ()
        resolving = resolving | {ref}

        if isinstance(clause, AcquireClause):
            rows = []
            for receipt in self.state.receipts_for(clause.id):
                # ``receipts_for`` contains only Runtime-issued ownership
                # edges.  Invocation-role matching happened when that edge
                # was admitted.  Replaying it here is both redundant and
                # wrong: an Acquire may have been instantiated by a
                # proposal-local Derive/Conditional whose value is no longer
                # persistent at a later Effect proposal.  The immutable
                # Clause->Receipt edge is the closure witness.
                if receipt.capability == clause.capability:
                    rows.append(Resolved(
                        receipt.value, (receipt.digest + "#",), receipt))
            self.memo[ref] = tuple(rows)
            return self.memo[ref]

        if isinstance(clause, DeriveClause):
            placed = tuple(self.placements.get(ref, ()))
            if placed:
                self.memo[ref] = placed
                return placed
            binding = self.state.bindings.get(clause.id)
            if binding is not None and binding.kind != "acquire":
                self.memo[ref] = (Resolved(binding.value, binding.refs),)
                return self.memo[ref]
            self.memo[ref] = ()
            return ()

        if isinstance(clause, ConditionalClause):
            groups = []
            for operand in clause.operands:
                if isinstance(operand, dict) and set(operand) == {"literal"}:
                    groups.append([Resolved(operand["literal"], (QUERY_REF,))])
                else:
                    groups.append(list(self.values(str(operand), resolving)))
            result = self._apply(clause.operator, groups)
            self.memo[ref] = tuple(result)
            return self.memo[ref]
        return ()

    @staticmethod
    def _expanded(rows):
        output = []
        for row in rows:
            if isinstance(row.value, dict):
                output.extend(Resolved(
                    value, row.refs, row.receipt) for value in row.value.values())
            elif isinstance(row.value, (list, tuple)):
                output.extend(Resolved(
                    value, row.refs, row.receipt) for value in row.value)
            else:
                output.append(row)
        return output

    @staticmethod
    def _combine_refs(rows):
        return tuple(dict.fromkeys(ref for row in rows for ref in row.refs))

    def _apply(self, operator: str, groups: list[list[Resolved]]):
        if operator == "coalesce":
            return next((tuple(group) for group in groups if group), ())
        if (operator == "field" and len(groups) == 2 and groups[0] and
                not groups[1]):
            # Some lookup capabilities return a one-entry {identity: value}
            # object.  The dynamic identity may have existed only in the
            # proposal that established the Acquire ownership edge.  Close
            # the field projection from the immutable Receipt itself iff the
            # sole response key is also an exact node of that same call's
            # arguments.  This is deterministic invocation/return linkage,
            # not a semantic guess or a new authorization source.
            output = []
            for row in groups[0]:
                if (row.receipt is None or not isinstance(row.value, dict) or
                        len(row.value) != 1):
                    continue
                key, value = next(iter(row.value.items()))
                argument_paths = tuple(_exact_paths(
                    row.receipt.arguments, key, "/$arguments"))
                if not argument_paths:
                    continue
                part = str(key).replace("~", "~0").replace("/", "~1")
                refs = (
                    row.receipt.digest + "#/" + part,
                    *(row.receipt.digest + "#" + path
                      for path in argument_paths),
                )
                output.append(Resolved(value, refs, row.receipt))
            return tuple(output)
        if not groups or any(not group for group in groups):
            return ()
        if operator == "identity":
            return tuple(groups[0])
        if operator == "singleton":
            if len(groups[0]) != 1:
                return ()
            row = groups[0][0]
            return (Resolved([row.value], row.refs, row.receipt),)
        if operator in {"union", "flatten"}:
            collections = [row.value for row in groups[0]]
            try:
                value = replay_operator(operator, [collections])
            except (TypeError, ValueError):
                return ()
            return (Resolved(value, self._combine_refs(groups[0])),)
        if operator == "map_count":
            collections = [row.value for row in groups[0]]
            try:
                value = replay_operator("map_count", [collections])
            except (TypeError, ValueError):
                return ()
            return (Resolved(value, self._combine_refs(groups[0])),)
        if operator in {"argmin", "argmax"}:
            left, right = self._expanded(groups[0]), self._expanded(groups[1])
            if (len(groups[0]) == len(groups[1]) == 1 and
                    len(left) == len(right) and left):
                pairs = list(zip(left, right))
            else:
                pairs = []
                for candidate in left:
                    matches = [score for score in right
                               if score.receipt is not None and any(
                                   self._equal(node, candidate.value)
                                   for node in _value_nodes(
                                       score.receipt.arguments))]
                    if len(matches) != 1:
                        return ()
                    pairs.append((candidate, matches[0]))
            if not pairs:
                return ()
            try:
                selected = (min if operator == "argmin" else max)(
                    pairs, key=lambda pair: pair[1].value)
            except (TypeError, ValueError):
                return ()
            item, score = selected
            return (Resolved(
                item.value, tuple(dict.fromkeys(item.refs + score.refs)),
                item.receipt),)

        if (operator == "aligned_lookup" and len(groups) == 3 and
                len(groups[0]) == len(groups[2]) == 1):
            keys, selected = groups[0][0], groups[2][0]
            if isinstance(keys.value, (list, tuple)):
                key_matches = [item for item in keys.value
                               if self._equal(item, selected.value)]
                value_matches = [row for row in groups[1]
                                 if row.receipt is not None and any(
                                     self._equal(node, selected.value)
                                     for node in _value_nodes(
                                         row.receipt.arguments))]
                if len(key_matches) == len(value_matches) == 1:
                    row = value_matches[0]
                    refs = self._combine_refs((keys, row, selected))
                    return (Resolved(row.value, refs, row.receipt),)

        output = []
        for combination in product(*groups):
            try:
                value = replay_operator(
                    operator, [item.value for item in combination])
            except (IndexError, TypeError, ValueError):
                continue
            if value is UNRESOLVED:
                continue
            output.append(Resolved(
                value, self._combine_refs(combination),
                combination[0].receipt if len(combination) == 1 else None))
        unique = {}
        for row in output:
            unique.setdefault(stable(row.value), row)
        return tuple(unique.values())
