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

import re
from dataclasses import dataclass
from itertools import product

from apex.defender.conditional_operators import (
    operator_operand_type, operator_value_matches, replay_operator,
)
from apex.defender.contract import (AcquireClause, ConditionalClause,
                                   DeriveClause)
from apex.defender.state import (Binding, CONTEXT_REF, QUERY_REF, SEMANTIC_REF,
                                Receipt, RuntimeState, UNRESOLVED, stable)

_TRUSTED_INPUT_REFS = {"task": QUERY_REF, "runtime-context": CONTEXT_REF}


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
