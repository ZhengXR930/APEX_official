"""Deterministic Receipt ownership for Acquire clauses.

Map every observation Receipt to every compatible Acquire role.  Ownership is
monotonic and many-to-many: a clause is not frozen to the first Receipt that
arrives.  A model is used only to choose among multiple already-compatible
clauses; code validates the choice and records the edge.

There is no fallback: an ambiguous receipt with no resolver, an abstaining
resolver, or a proposal outside the candidate set binds nothing (the receipt
stays an unowned observation).
"""
from __future__ import annotations

from apex.defender.contract import AcquireClause, EffectClause
from apex.defender.resolver import LazyResolver
from apex.defender.state import (ClauseReceiptBinding, Receipt, RuntimeState,
                                     stable)


def literal_arguments_compatible(
        clause: AcquireClause, arguments: dict) -> bool:
    """Semantic matching may bridge workflow variance, never literal conflict."""
    for name, spec in clause.call_arguments.items():
        if isinstance(spec, dict) and set(spec) == {"literal"}:
            literal = spec["literal"]
        elif not isinstance(spec, dict):
            literal = spec
        else:
            continue
        if name not in arguments or arguments[name] != literal:
            return False
    return True


def _bind_receipt(state, clause, receipt):
    if not state.admit(clause.id, receipt):
        return None
    return ClauseReceiptBinding(clause.id, receipt.digest)


def _same_argument_role(acquire_spec, effect_spec) -> bool:
    """Compare the Contract data role, ignoring Effect-only delegation mode."""
    if (isinstance(acquire_spec, dict) and "from" in acquire_spec and
            isinstance(effect_spec, dict) and "from" in effect_spec):
        return stable(acquire_spec["from"]) == stable(effect_spec["from"])
    return stable(acquire_spec) == stable(effect_spec)


def bind_effect_return(state: RuntimeState, contract, receipt: Receipt,
                       effect_clause_id: str) -> tuple[ClauseReceiptBinding, ...]:
    """Bind one successful Effect return to its corresponding Acquire roles.

    ``effect_clause_id`` is produced by WRAP code when the invocation passes;
    it is never supplied by the target Agent.  The return may satisfy only an
    Acquire of the same capability whose declared invocation roles are a
    subset of that exact Effect Clause.  This preserves the authorization
    provenance without persisting proposal-local semantic/Delegation values.
    """
    if not receipt.effect_return:
        return ()
    effect = next((clause for clause in contract.clauses
                   if isinstance(clause, EffectClause) and
                   clause.id == str(effect_clause_id) and
                   clause.action == receipt.capability), None)
    if effect is None:
        return ()
    bindings = []
    for clause in contract.clauses:
        if (not isinstance(clause, AcquireClause) or
                clause.capability != receipt.capability or
                not literal_arguments_compatible(clause, receipt.arguments)):
            continue
        if any(name not in effect.effect_arguments or
               not _same_argument_role(spec, effect.effect_arguments[name])
               for name, spec in clause.call_arguments.items()):
            continue
        changed = state.admit_effect_return(clause.id, receipt)
        if changed:
            bindings.append(ClauseReceiptBinding(clause.id, receipt.digest))
    return tuple(bindings)


def bind_acquire(state: RuntimeState, contract, receipt: Receipt,
                 resolve_ambiguous=None) -> ClauseReceiptBinding | None:
    """Bind ``receipt`` to the AcquireClause it fulfils, or return None.

    ``resolve_ambiguous`` is the optional single binding agent, a callable
    ``(task, capability, arguments, candidates) -> {"clause_id": str} | None``.
    """
    resolver = LazyResolver(state, contract)
    candidates = [
        clause for clause in contract.clauses
        if isinstance(clause, AcquireClause) and
        clause.capability == receipt.capability and
        literal_arguments_compatible(clause, receipt.arguments)]
    if not candidates:
        return None
    exact = [clause for clause in candidates
             if resolver.call_matches(clause, receipt.arguments)]
    if exact:
        # Ownership is many-to-many. If invocation arguments match several
        # declared Acquire roles, the same immutable fact is reachable from
        # each role; no semantic choice or authority is created here.
        bindings = [_bind_receipt(state, clause, receipt) for clause in exact]
        return next((binding for binding in bindings
                     if binding is not None),
                    ClauseReceiptBinding(exact[0].id, receipt.digest))
    if resolve_ambiguous is None:
        return None
    choices = candidates
    proposal = resolve_ambiguous(
        task=contract.task, capability=receipt.capability,
        arguments=dict(receipt.arguments),
        candidates=[{
            "clause_id": clause.id,
            "instruction": clause.instruction,
            "call_arguments": clause.call_arguments,
            "output": clause.output,
        } for clause in choices]) or {}
    chosen = next((clause for clause in choices
                   if clause.id == proposal.get("clause_id")), None)
    if chosen is None:
        return None
    return _bind_receipt(state, chosen, receipt)
