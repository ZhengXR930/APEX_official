"""One constrained proposal-time Binding placement over unordered Receipts.

Persistent state owns only Clause→Receipt edges.  This module builds all
unresolved semantic goals from the current Receipt snapshot, enumerates their
Clause-reachable evidence, and asks one Binding Agent to select opaque ids.
The Agent never writes a Receipt ref, span, value, operator, Clause, or Effect.
Code projects exact node/span/list/object values and replays every Conditional.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from apex.defender.contract import (AcquireClause, ConditionalClause,
                                   DeriveClause, EffectClause)
from apex.defender.resolver import (LazyResolver, Resolved,
                                   operator_operand_type,
                                   operator_value_matches)
from apex.defender.state import (GROUNDED_REF, QUERY_REF, SEMANTIC_REF,
                                     RuntimeState, stable)


@dataclass(frozen=True)
class BindingGoal:
    id: str
    clause_id: str
    argument: str
    target_ref: str
    instruction: str
    mode: str                 # intermediate | direct | delegated
    expected_type: str
    proposed: object
    allow_semantic: bool
    allow_grounded: bool
    quantified: bool
    candidates: tuple[dict, ...]

    def public(self) -> dict:
        compose = (["list"] if self.mode != "intermediate" and
                   isinstance(self.proposed, (list, tuple)) else
                   ["object"] if self.mode != "intermediate" and
                   isinstance(self.proposed, dict) else
                   ["scalar", "list"] if self.expected_type == "number-list"
                   else ["scalar"])
        return {
            "goal_id": self.id,
            "argument": self.argument,
            "role": self.instruction,
            "mode": self.mode,
            "expected_type": self.expected_type,
            "proposed": self.proposed,
            # This is compiled from the trusted capability Manifest.  The
            # Binding Agent may select evidence under the declared mode, but
            # cannot promote an exact-only argument to semantic support.
            "support_mode": ("exact_or_grounded" if self.allow_grounded and
                             self.mode == "intermediate" else
                             "role_selection" if self.mode == "intermediate" else
                             "exact_or_semantic" if self.allow_semantic else
                             "exact"),
            # This is compiled from the Contract clause.  It describes a
            # reusable role; it is not an Agent-issued authority bit.
            "quantified": self.quantified,
            "allowed_compose": compose,
            "candidates": [
                {"candidate_id": row["id"], "value": row["value"]}
                for row in self.candidates],
        }


@dataclass(frozen=True)
class ProposalProofs:
    """Disjoint proof channels produced by deterministic validation."""

    exact: dict
    grounded: dict
    semantic: dict
    delegated: dict
    placements: dict


def _nodes(value, ref):
    yield value, ref
    if isinstance(value, dict):
        for key, child in value.items():
            part = str(key).replace("~", "~0").replace("/", "~1")
            yield from _nodes(child, ref + "/" + part)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _nodes(child, ref + "/" + str(index))


def _numeric_spans(value, ref):
    if not isinstance(value, str):
        return
    for match in re.finditer(r"(?<![\w.])-?\d+(?:\.\d+)?(?!\w|\.\d)", value):
        number = Decimal(match.group(0))
        parsed = (int(number) if number == number.to_integral()
                  else float(number))
        yield parsed, f"{ref}@{match.start()}:{match.end()}"


def _typed_spans(value, ref, expected_type):
    # Numbers are self-describing exact spans even when an upstream schema
    # cannot propagate a narrower type through a generic equality operator.
    # Enumerating them does not interpret their role; the Agent may only pick
    # one of these code-issued spans and replay still checks the operator.
    if expected_type in {"any", "number", "number-list"}:
        yield from _numeric_spans(value, ref) or ()
    if isinstance(value, str) and expected_type in {"any", "string", "url"}:
        yield from _url_spans(value, ref) or ()
    if not isinstance(value, str) or expected_type not in {"string", "datetime"}:
        return
    patterns = (
        r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2})?\b",
        r"\b\d{1,2}:\d{2}\b",
        r"\b(?:one|two|three|four|\d+(?:\.\d+)?)\s+"
        r"(?:minute|minutes|hour|hours)\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, value, re.I):
            yield match.group(0), f"{ref}@{match.start()}:{match.end()}"


def _source_leaves(value, ref):
    if isinstance(value, dict):
        for key, child in value.items():
            part = str(key).replace("~", "~0").replace("/", "~1")
            yield from _source_leaves(child, ref + "/" + part)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _source_leaves(child, ref + "/" + str(index))
    else:
        yield value, ref


_URL_AUTHORITY_SPAN = re.compile(r"(?:https?://|www\.)[^\s<>'\"]+", re.I)
_URL_TRAILING_PUNCTUATION = ".,;:!?)"


def _url_spans(value: str, ref: str):
    """Yield conservative URL authority spans from trusted text leaves.

    This is still exact proof: code points to the concrete trusted substring.
    Canonical equivalence is checked later by the operator-declared URL
    argument schema, so apex domains are not silently equated with ``www``.
    """
    if not isinstance(value, str):
        return
    for match in _URL_AUTHORITY_SPAN.finditer(value):
        start, end = match.start(), match.end()
        token = match.group(0)
        trimmed = token.rstrip(_URL_TRAILING_PUNCTUATION)
        if not trimmed:
            continue
        end -= len(token) - len(trimmed)
        yield trimmed, f"{ref}@{start}:{end}"


def _project_scalar(value, selected, equal=None):
    exact, spans = [], []
    for row in selected:
        for leaf, ref in _source_leaves(row["value"], row["ref"]):
            if leaf == value and type(leaf) is type(value):
                exact.extend(row.get("refs") or (ref,))
            elif (equal is not None and isinstance(value, str) and
                  isinstance(leaf, str) and value and equal(leaf, value)):
                exact.extend(row.get("refs") or (ref,))
            elif isinstance(value, str) and isinstance(leaf, str) and value:
                starts = [item.start() for item in re.finditer(
                    re.escape(value), leaf)]
                if len(starts) == 1:
                    start = starts[0]
                    spans.extend(row.get("refs") or
                                 (f"{ref}@{start}:{start + len(value)}",))
                if equal is not None:
                    for token, span_ref in _url_spans(leaf, ref) or ():
                        if equal(token, value):
                            spans.extend(row.get("refs") or (span_ref,))
            elif (isinstance(value, (int, float)) and
                  not isinstance(value, bool) and isinstance(leaf, str)):
                for match in re.finditer(
                        r"(?<![\w.])-?\d+(?:\.\d+)?(?!\w|\.\d)", leaf):
                    try:
                        same = Decimal(match.group(0)) == Decimal(str(value))
                    except (InvalidOperation, ValueError):
                        same = False
                    if same:
                        spans.extend(row.get("refs") or
                                     (f"{ref}@{match.start()}:{match.end()}",))
    witnesses = list(dict.fromkeys(exact or spans))
    return tuple(witnesses) if len(witnesses) == 1 else ()


def project_value(value, selected, equal=None):
    """Replay exact node/span or recursive list/object composition."""
    for row in selected:
        if stable(row["value"]) == stable(value):
            return tuple(row.get("refs") or (row["ref"],))
    if isinstance(value, dict):
        refs = []
        for child in value.values():
            proof = project_value(child, selected, equal)
            if not proof:
                return ()
            refs.extend(proof)
        return tuple(dict.fromkeys(refs)) if value else ()
    if isinstance(value, (list, tuple)):
        refs = []
        for child in value:
            proof = project_value(child, selected, equal)
            if not proof:
                return ()
            refs.extend(proof)
        return tuple(dict.fromkeys(refs)) if value else ()
    return _project_scalar(value, selected, equal)


def _project_closed_member(value, selected):
    """Return provenance when ``value`` is one unique exact scalar member."""
    matches = []
    for row in selected:
        for leaf, _ref in _source_leaves(row["value"], row["ref"]):
            if type(leaf) is type(value) and leaf == value:
                matches.append(row)
    if len(matches) != 1:
        return ()
    row = matches[0]
    return tuple(dict.fromkeys(row.get("refs") or (row["ref"],)))


def project_delegated_value(value, candidates, equal=None):
    """Prove an explicit delegation by unique exact leaf projection.

    Delegation authorizes a named upstream value source, not semantic value
    invention.  Consequently code may close a proposed value only when every
    scalar component has exactly one node/span witness among the reachable
    Receipt leaves.  Containers are excluded as competing coarse witnesses;
    ambiguity between distinct leaves remains fail-closed.
    """
    leaves = tuple(row for row in candidates
                   if not isinstance(row["value"], (dict, list, tuple)))

    def project(item):
        if isinstance(item, dict):
            refs = []
            for child in item.values():
                proof = project(child)
                if not proof:
                    return ()
                refs.extend(proof)
            return tuple(dict.fromkeys(refs)) if item else ()
        if isinstance(item, (list, tuple)):
            refs = []
            for child in item:
                proof = project(child)
                if not proof:
                    return ()
                refs.extend(proof)
            return tuple(dict.fromkeys(refs)) if item else ()
        return _project_scalar(item, leaves, equal)

    return project(value)


def _reachable_receipts(state, contract, target_ref):
    by_ref = {clause.output_ref: clause for clause in contract.clauses
              if clause.output_ref}
    found = {}

    def visit(ref, seen=frozenset()):
        ref = str(ref)
        if ref in seen:
            return
        clause = by_ref.get(ref)
        if isinstance(clause, AcquireClause):
            for receipt in state.receipts_for(clause.id):
                found[receipt.digest] = receipt
        elif isinstance(clause, DeriveClause):
            for source in clause.input_refs:
                if source != "task":
                    visit(source, seen | {ref})
        elif isinstance(clause, ConditionalClause):
            for source in clause.operand_refs:
                visit(source, seen | {ref})

    clause = by_ref.get(str(target_ref))
    task_reachable = isinstance(clause, DeriveClause) and "task" in clause.input_refs
    visit(target_ref)
    return tuple(found.values()), task_reachable


def _candidate_rows(state, contract, target_ref, expected_type):
    receipts, task_reachable = _reachable_receipts(
        state, contract, target_ref)
    rows = []
    if task_reachable:
        rows.append({"ref": QUERY_REF, "value": contract.task})
        rows.extend({"ref": ref, "value": value}
                    for value, ref in _typed_spans(
                        contract.task, QUERY_REF, expected_type) or ())
    for receipt in receipts:
        for value, ref in _nodes(receipt.value, receipt.digest + "#"):
            rows.append({"ref": ref, "value": value})
            rows.extend({"ref": span_ref, "value": span_value}
                        for span_value, span_ref in
                        (_typed_spans(value, ref, expected_type) or ()))
        if receipt.arguments:
            for value, ref in _nodes(
                    receipt.arguments, receipt.digest + "#/$arguments"):
                rows.append({"ref": ref, "value": value})

    # A semantic role may consume a value produced by an already-closed
    # Conditional (for example keys(object) or sort_by(records,...)). Expose
    # those replayed outputs as code-owned candidates; the Agent still sees
    # only opaque ids and cannot invent a value, ref, operator, or scope.
    by_ref = {clause.output_ref: clause for clause in contract.clauses
              if clause.output_ref}
    target = by_ref.get(str(target_ref))
    if isinstance(target, DeriveClause):
        # A WRAP-authorized Effect return is an authorized fact of this episode.
        # Exposing its committed nodes lets a later Effect argument that reuses
        # an authorized product (for example running the exact artifact a prior
        # authorized write created) trace to real provenance instead of a
        # re-grounded semantic judgment. Exact projection downstream still needs
        # a unique byte-exact match, so this adds evidence, never a guess.
        rows.extend(_authorized_effect_rows(state))
        resolver = LazyResolver(state, contract)
        for source in target.input_refs:
            if source in {"task", "runtime-context"}:
                continue
            # Acquire values are already enumerated above with their exact
            # JSON node/span locators. Re-emitting them here would coarsen a
            # precise span into the Conditional's aggregate provenance. Only
            # closed replay outputs add genuinely new candidate values.
            if not isinstance(by_ref.get(str(source)), ConditionalClause):
                continue
            for resolved in resolver.values(source):
                if not resolved.refs:
                    continue
                for value, ref in _nodes(resolved.value, "<replay>:" + source):
                    rows.append({"ref": ref, "refs": resolved.refs,
                                 "value": value})
    unique, seen = [], set()
    for row in rows:
        key = (row["ref"], tuple(row.get("refs") or ()),
               stable(row["value"]))
        if key not in seen:
            seen.add(key)
            unique.append({**row, "id": "n" + str(len(unique))})
    return tuple(unique)


def _root_candidates(rows):
    """Keep complete evidence roots without recursively duplicated nodes."""
    compact = []
    for row in rows:
        ref = str(row["ref"])
        replay = ref.removeprefix("<replay>:")
        if (ref == QUERY_REF or ref.endswith("#") or
                (ref.startswith("<replay>:") and "/" not in replay)):
            compact.append(row)
    return compact


def _collect_leaves(contract, ref, expected, found, seen=frozenset()):
    by_ref = {clause.output_ref: clause for clause in contract.clauses
              if clause.output_ref}
    ref = str(ref)
    if ref in seen:
        return
    clause = by_ref.get(ref)
    if isinstance(clause, (AcquireClause, DeriveClause)):
        found.setdefault(ref, set()).add(expected)
    elif isinstance(clause, ConditionalClause):
        for index, operand in enumerate(clause.operands):
            if isinstance(operand, str) and operand in by_ref:
                _collect_leaves(
                    contract, operand,
                    operator_operand_type(clause.operator, index), found,
                    seen | {ref})


def _decimal(value):
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple)):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _number_value(value: Decimal):
    return int(value) if value == value.to_integral() else float(value)


def _unique_value(resolver, operand):
    if isinstance(operand, dict) and set(operand) == {"literal"}:
        return True, operand["literal"]
    if not isinstance(operand, str):
        return False, None
    rows = tuple(resolver.values(operand)) if resolver is not None else ()
    if len(rows) != 1:
        return False, None
    return True, rows[0].value


def _single_unknown_numeric_inverse(operator, operands, proposed, resolver):
    """Return one ``(operand, value)`` hint for closed single-unknown algebra.

    The hint is never authority by itself.  It only turns an Effect argument
    already supplied by the target into a concrete value hypothesis for a
    Contract-declared Derive role; WRAP still requires the ordinary
    candidate-placement and replay proof before an Effect can pass.
    """
    target = _decimal(proposed)
    if target is None:
        return ()
    known = []
    unknown = []
    for index, operand in enumerate(operands):
        ok, value = _unique_value(resolver, operand)
        if ok:
            parsed = _decimal(value)
            if parsed is None:
                return ()
            known.append((index, parsed))
        elif isinstance(operand, str):
            unknown.append((index, operand))
        else:
            return ()
    if len(unknown) != 1:
        return ()
    index, operand = unknown[0]
    try:
        if operator == "add":
            value = target - sum((item for _idx, item in known), Decimal(0))
        elif operator == "multiply" and len(operands) == 2 and known:
            known_value = known[0][1]
            if known_value == 0:
                return ()
            value = target / known_value
        elif operator == "percent_of" and len(operands) == 2 and known:
            known_index, known_value = known[0]
            if known_value == 0:
                return ()
            if index == 0:
                # target = base * percent / 100
                value = target * Decimal(100) / known_value
            elif known_index == 0:
                # target = base * percent / 100
                value = target * Decimal(100) / known_value
            else:
                return ()
        else:
            return ()
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return ()
    return ((operand, _number_value(value)),)


def _derive_hints(contract, ref, proposed, found, resolver=None,
                  seen=frozenset()):
    """Back-propagate an Effect value through closed proof algebra.

    This never asks the Binding Agent to invent a value.  The hypothesis is
    the concrete Effect argument supplied by code; the Agent may only select
    evidence for the Contract-declared Derive role.
    """
    by_ref = {clause.output_ref: clause for clause in contract.clauses
              if clause.output_ref}
    ref = str(ref)
    if ref in seen:
        return
    clause = by_ref.get(ref)
    if isinstance(clause, DeriveClause):
        prior = found.get(ref)
        if prior is None or stable(prior) == stable(proposed):
            found[ref] = proposed
        else:
            # Conflicting downstream hypotheses are not safe to ground.
            found[ref] = _CONFLICT
        return
    if not isinstance(clause, ConditionalClause):
        return
    operands = tuple(clause.operands)
    forwarded = []
    if clause.operator in {"identity", "gt", "lt"} and operands:
        forwarded = [(operands[0], proposed)]
    elif clause.operator == "coalesce":
        forwarded = [(operand, proposed) for operand in operands]
    elif (clause.operator == "singleton" and operands and
          isinstance(proposed, (list, tuple)) and len(proposed) == 1):
        forwarded = [(operands[0], proposed[0])]
    elif clause.operator in {"add", "multiply", "percent_of"}:
        forwarded = _single_unknown_numeric_inverse(
            clause.operator, operands, proposed, resolver)
    for operand, value in forwarded:
        if isinstance(operand, str) and operand in by_ref:
            _derive_hints(contract, operand, value, found, resolver,
                          seen | {ref})


_CONFLICT = object()
_PUBLIC_GOAL_BUDGET = 8_000


def _authorized_effect_rows(state):
    """Candidate rows from Receipts a prior Effect return was WRAP-authorized.

    A WRAP-authorized Effect return is an authorized fact of this episode: its
    committed arguments and return value may deterministically ground a later
    Effect argument (for example running the exact artifact an authorized write
    just created). Only the committed nodes are exposed, and downstream exact
    projection still requires a unique byte-exact match — no semantic guess.
    """
    rows = []
    for receipt in state.authorized_effect_receipts():
        root = receipt.digest + "#"
        for value, ref in _nodes(receipt.value, root):
            rows.append({"ref": ref, "value": value})
        if receipt.arguments:
            for value, ref in _nodes(
                    receipt.arguments, receipt.digest + "#/$arguments"):
                rows.append({"ref": ref, "value": value})
    return rows


def compile_goals(state: RuntimeState, contract, action, arguments, surface,
                  equal):
    resolver = LazyResolver(state, contract)
    by_ref = {clause.output_ref: clause for clause in contract.clauses
              if clause.output_ref}
    goals, immediate, immediate_delegated, seen = [], {}, {}, set()
    effects = [clause for clause in contract.clauses
               if isinstance(clause, EffectClause) and clause.action == action]
    for effect in effects:
        for name, spec in effect.effect_arguments.items():
            if name not in arguments or not isinstance(spec, dict) or "from" not in spec:
                continue
            raw = spec["from"]
            sources = [raw] if isinstance(raw, str) else list(raw or ())
            hints = {}
            for source in sources:
                _derive_hints(contract, source, arguments[name], hints,
                              resolver)
            matches = [row for source in sources for row in resolver.values(source)
                       if equal(name, row.value, arguments[name])]
            if matches:
                refs = tuple(dict.fromkeys(
                    ref for row in matches for ref in row.refs))
                target = (immediate_delegated
                          if spec.get("delegated") is True else immediate)
                target[(effect.id, name)] = refs
                continue

            # A closed Conditional has already encoded the selection role.
            # When an Effect is quantified over its finite output, each exact
            # unique member is therefore a deterministic projection—not a new
            # semantic choice for the Binding Agent to repeat inconsistently.
            closed_rows = []
            for source in sources:
                if not isinstance(by_ref.get(str(source)), ConditionalClause):
                    continue
                for index, row in enumerate(resolver.values(source)):
                    closed_rows.append({
                        "value": row.value,
                        "ref": f"<closed>:{source}/{index}",
                        "refs": row.refs,
                    })
            projected = _project_closed_member(arguments[name], closed_rows)
            if projected:
                target = (immediate_delegated
                          if spec.get("delegated") is True else immediate)
                target[(effect.id, name)] = projected
                continue

            # A WRAP-authorized Effect return is an authorized fact of this
            # episode. When the proposed argument reuses one of its committed
            # values (for example running the exact artifact a prior authorized
            # write created), close it deterministically here — no re-grounding,
            # no Binding Agent — provided the byte-exact match is unique.
            authorized = _project_closed_member(
                arguments[name], _authorized_effect_rows(state))
            if authorized:
                target = (immediate_delegated
                          if spec.get("delegated") is True else immediate)
                target[(effect.id, name)] = authorized
                continue

            leaves = {}
            for source in sources:
                _collect_leaves(contract, source, "any", leaves)
            direct = len(leaves) == 1 and any(str(source) in leaves for source in sources)
            delegated = (set(spec) == {"from", "delegated"} and
                         spec.get("delegated") is True)
            for target_ref, kinds in leaves.items():
                # A closed suffix can already consume this value. Only a
                # direct Effect projection still needs the Agent to select the
                # exact node within an acquired collection.
                if not direct and resolver.values(target_ref):
                    continue
                key = (effect.id, name, target_ref)
                if key in seen:
                    continue
                seen.add(key)
                concrete = kinds - {"any"}
                expected = (next(iter(concrete)) if len(concrete) == 1 else
                            "any" if not concrete else "conflict")
                if expected == "conflict":
                    continue
                clause = next(item for item in contract.clauses
                              if item.output_ref == target_ref)
                allow_semantic = bool(
                    direct and not delegated and surface is not None and
                    surface.accepts_semantic_support(name))
                rows = list(_candidate_rows(
                    state, contract, target_ref, expected))
                if delegated and direct:
                    # Preserve the zero-model exact path over the complete
                    # code-owned domain. Candidate compaction below is only a
                    # public-protocol optimization after exact closure failed.
                    refs = project_delegated_value(
                        arguments[name], rows,
                        equal=lambda candidate, proposed, argument=name: equal(
                            argument, candidate, proposed))
                    if refs:
                        immediate_delegated[(effect.id, name)] = refs
                        continue
                oversized = sum(len(stable(row["value"])) for row in rows) > \
                    _PUBLIC_GOAL_BUDGET
                if allow_semantic and oversized:
                    # One complete Receipt root retains every nested node and
                    # span needed by deterministic ``project_value``. Sending
                    # the root plus all of its recursively duplicated children
                    # only consumes the bounded Agent prompt and may truncate
                    # later goals in the same Action batch. Closed replay rows
                    # remain because they add values not present in a Receipt.
                    rows = _root_candidates(rows)
                elif delegated and direct and oversized:
                    # Delegated identities remain exact-only. Prefer a closed
                    # Conditional replay root (for example a selected/search
                    # subset) over all recursively reachable raw Receipts.
                    # ``project_value`` still proves every proposed scalar by
                    # a unique exact leaf before issuing any delegation ref.
                    replay = [row for row in _root_candidates(rows)
                              if str(row["ref"]).startswith("<replay>:")]
                    rows = replay or _root_candidates(rows)
                if not direct:
                    rows = [row for row in rows
                            if operator_value_matches(expected, row["value"])]
                if not rows:
                    continue
                mode = ("delegated" if delegated and direct else
                        "direct" if direct else "intermediate")
                proposed = (arguments[name] if direct else
                            hints.get(target_ref))
                if proposed is _CONFLICT:
                    proposed = None
                allow_grounded = bool(
                    isinstance(clause, DeriveClause) and proposed is not None
                    and not delegated and (not direct or allow_semantic))
                goal_id = "g" + str(len(goals))
                rows = tuple({**row, "id": goal_id + "." + row["id"]}
                             for row in rows)
                goals.append(BindingGoal(
                    goal_id, effect.id, name, target_ref,
                    clause.instruction, mode, expected,
                    proposed,
                    allow_semantic,
                    allow_grounded,
                    bool(getattr(clause, "quantified", False)), rows))
    return tuple(goals), immediate, immediate_delegated


def apply_placements(state, contract, action, arguments, surface, goals,
                     immediate, immediate_delegated, proposal, equal):
    """Validate placements and keep exact/model proof channels disjoint."""
    rows = proposal.get("placements") if isinstance(proposal, dict) else None
    if not isinstance(rows, list):
        rows = []
    by_goal = {goal.id: goal for goal in goals}
    selected = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "goal_id", "candidate_ids", "compose"}:
            continue
        goal = by_goal.get(str(row.get("goal_id")))
        ids, compose = row.get("candidate_ids"), row.get("compose")
        allowed = set(goal.public()["allowed_compose"]) if goal else set()
        if (goal is None or goal.id in selected or
                not isinstance(ids, list) or not ids or
                len(set(map(str, ids))) != len(ids) or compose not in allowed):
            continue
        choices = {item["id"]: item for item in goal.candidates}
        if any(str(item) not in choices for item in ids):
            continue
        selected[goal.id] = (compose, [choices[str(item)] for item in ids])

    placements, direct_exact, direct_grounded, direct_semantic = {}, {}, {}, {}
    delegated = dict(immediate_delegated)
    for goal in goals:
        choice = selected.get(goal.id)
        if choice is None:
            continue
        compose, candidates = choice
        if goal.mode == "intermediate":
            evidence_refs = tuple(dict.fromkeys(
                ref for item in candidates
                for ref in (item.get("refs") or (item["ref"],))))
            exact_refs = (project_value(goal.proposed, candidates)
                          if goal.proposed is not None else ())
            if exact_refs:
                value, refs = goal.proposed, exact_refs
            elif goal.allow_grounded and evidence_refs:
                value = goal.proposed
                refs = (GROUNDED_REF, *evidence_refs)
            else:
                if compose == "scalar" and len(candidates) != 1:
                    continue
                value = (candidates[0]["value"] if compose == "scalar" else
                         [item["value"] for item in candidates])
                refs = evidence_refs
            if not operator_value_matches(goal.expected_type, value):
                continue
            placements[goal.target_ref] = (Resolved(value, refs),)
            continue

        if (compose == "list") != isinstance(goal.proposed, (list, tuple)):
            continue
        if (compose == "object") != isinstance(goal.proposed, dict):
            continue
        refs = project_value(
            goal.proposed, candidates,
            equal=lambda candidate, proposed, argument=goal.argument: equal(
                argument, candidate, proposed))
        key = (goal.clause_id, goal.argument)
        if refs:
            placements[goal.target_ref] = (
                Resolved(goal.proposed, refs),)
            (delegated if goal.mode == "delegated" else direct_exact)[key] = refs
        elif goal.allow_grounded:
            evidence_refs = tuple(dict.fromkeys(
                ref.split("@", 1)[0] for item in candidates
                for ref in (item.get("refs") or (item["ref"],))))
            if evidence_refs:
                refs = (GROUNDED_REF, *evidence_refs)
                placements[goal.target_ref] = (
                    Resolved(goal.proposed, refs),)
                direct_grounded[key] = refs
        elif goal.allow_semantic:
            evidence_refs = tuple(dict.fromkeys(
                ref.split("@", 1)[0] for item in candidates
                for ref in (item.get("refs") or (item["ref"],))))
            if evidence_refs:
                direct_semantic[key] = (SEMANTIC_REF, *evidence_refs)

    resolver = LazyResolver(state, contract, placements)
    exact = dict(immediate)
    effects = [clause for clause in contract.clauses
               if isinstance(clause, EffectClause) and clause.action == action]
    for effect in effects:
        for name, spec in effect.effect_arguments.items():
            if name not in arguments or not isinstance(spec, dict) or "from" not in spec:
                continue
            raw = spec["from"]
            sources = [raw] if isinstance(raw, str) else list(raw or ())
            matches = [row for source in sources for row in resolver.values(source)
                       if equal(name, row.value, arguments[name])]
            if matches:
                refs = tuple(dict.fromkeys(
                    ref for row in matches for ref in row.refs))
                target = (delegated if spec.get("delegated") is True else exact)
                target[(effect.id, name)] = refs
    exact.update(direct_exact)
    separated_exact, grounded = {}, dict(direct_grounded)
    semantic = dict(direct_semantic)
    for key, refs in exact.items():
        refs = tuple(refs)
        if SEMANTIC_REF in refs:
            semantic[key] = refs
        elif GROUNDED_REF in refs:
            grounded[key] = refs
        else:
            separated_exact[key] = refs
    return ProposalProofs(
        separated_exact, grounded, semantic, delegated, placements)
