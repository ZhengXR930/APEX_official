"""WRAP: one deterministic gate from Root Effect Clauses to runtime calls.

There is exactly one authorization path.  The trusted Contract fixes the
Effect action and every argument role; runtime Binding resolves Clause outputs
from task-local Receipts, and this gate compares the complete proposal against
those outputs.  Runtime content may instantiate a declared Derive role, but it
cannot create a new Effect or an undeclared argument.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from apex.defender.contract import ConditionalClause, DeriveClause, EffectClause
from apex.defender.state import (CONTEXT_REF, GROUNDED_REF, QUERY_REF,
                                SEMANTIC_REF, RuntimeState, UNRESOLVED)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str
    refs: tuple[str, ...] = field(default_factory=tuple)
    clause_id: str = ""


_AUTHORITY_PATTERNS = {
    "url": re.compile(r"(?:https?://|www\.)[^\s<>'\"]+", re.I),
    "email": re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    "mention": re.compile(r"(?<![\w@])@[A-Za-z0-9_.-]+"),
}


def authority_atoms(value, grammars) -> tuple[str, ...]:
    """Parse only authority grammars explicitly attested by the operator."""
    atoms = []

    def scan(node):
        if isinstance(node, dict):
            for child in node.values():
                scan(child)
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                scan(child)
            return
        if not isinstance(node, str):
            return
        for grammar in grammars or ():
            pattern = _AUTHORITY_PATTERNS.get(str(grammar))
            if pattern is not None:
                atoms.extend(match.group(0).rstrip(".,;:!?)")
                             for match in pattern.finditer(node))

    scan(value)
    return tuple(dict.fromkeys(atoms))


def _atom_authorized(state, contract, atom: str) -> bool:
    """An authority atom must come from a trusted or closed source.

    Open prose can be summarized or rewritten, but an acquired document cannot
    smuggle a new endpoint, recipient or mention through a content argument.
    A trusted task literal or a deterministically replayed Conditional may
    introduce such an atom; ordinary Acquire/Derive provenance may not.
    """
    if atom in contract.task:
        return True
    for clause in contract.clauses:
        for spec in clause.arguments.values():
            if (isinstance(spec, dict) and set(spec) == {"literal"} and
                    isinstance(spec["literal"], str) and atom in spec["literal"]):
                return True

    def exact_node(value):
        if isinstance(value, dict):
            return any(exact_node(child) for child in value.values())
        if isinstance(value, (list, tuple)):
            return any(exact_node(child) for child in value)
        return value == atom

    return any(binding.kind in {"conditional", "supporting-conditional"} and
               SEMANTIC_REF not in binding.refs and
               exact_node(binding.value) for binding in state.bindings.values())


def _has_derive_source(contract, spec) -> bool:
    """Whether a Contract argument depends on at least one Derive clause."""
    if not isinstance(spec, dict) or "from" not in spec:
        return False
    by_ref = {
        clause.output_ref: clause for clause in contract.clauses
        if clause.output_ref
    }

    def visit(ref, seen=frozenset()):
        ref = str(ref)
        if ref in seen:
            return False
        clause = by_ref.get(ref)
        if isinstance(clause, DeriveClause):
            return True
        if isinstance(clause, ConditionalClause):
            return any(
                visit(operand, seen | {ref})
                for operand in clause.operands
                if isinstance(operand, str))
        return False

    raw = spec["from"]
    sources = [raw] if isinstance(raw, str) else list(raw or ())
    return any(visit(source) for source in sources)


def _trace_argument(state: RuntimeState, spec, value, equal,
                    delegated_refs=(), exact_refs=(), grounded_refs=(),
                    semantic_refs=(),
                    allow_semantic=False, exact_only=False):
    """Return deterministic proof refs for one Effect argument.

    A semantic binding may close only a Contract-declared Derive whose
    registered capability argument explicitly permits semantic support. It
    cannot close an exact-only or delegated authority position. The model
    binds the Derive; WRAP itself remains a deterministic comparison.
    """

    def deterministic(refs):
        refs = tuple(dict.fromkeys(map(str, refs or ())))
        if not refs:
            return ()
        if {SEMANTIC_REF, GROUNDED_REF}.intersection(refs):
            return ()
        return refs

    def semantic(refs):
        refs = tuple(dict.fromkeys(map(str, refs or ())))
        if (not allow_semantic or exact_only or not refs or
                not {SEMANTIC_REF, GROUNDED_REF}.intersection(refs)):
            return ()
        return refs

    if isinstance(spec, dict) and set(spec) == {"literal"}:
        return (equal(spec["literal"], value), (QUERY_REF,))
    if isinstance(spec, dict) and set(spec) == {"from"}:
        sources = spec["from"]
        sources = [sources] if isinstance(sources, str) else list(sources or ())
        for source in sources:
            resolved = state.output(source)
            if resolved is not UNRESOLVED and equal(resolved, value):
                binding = state.bindings.get(str(source).partition(".")[0])
                raw_refs = () if binding is None else binding.refs
                refs = deterministic(raw_refs) or semantic(raw_refs)
                if refs:
                    return True, refs
        refs = deterministic(exact_refs)
        if refs:
            return True, refs
        refs = semantic(grounded_refs) or semantic(semantic_refs)
        if refs:
            return True, refs
        return (False, ())
    if (isinstance(spec, dict) and
            set(spec) == {"from", "delegated"} and
            spec.get("delegated") is True):
        refs = deterministic(delegated_refs)
        if exact_only:
            return (False, ())
        return (bool(refs), refs)
    return (equal(spec, value), (QUERY_REF,))


def _check_clause(state: RuntimeState, contract, clause: EffectClause,
                  arguments: dict, required, content, content_atoms,
                  delegated_proofs, exact_proofs, grounded_proofs,
                  semantic_proofs, defaults,
                  equal, exact_only) -> Verdict:
    contracted = frozenset(clause.effect_arguments)
    # Operator defaults are deterministic runtime behavior, not agent-provided
    # authority.  If a supplied/materialized argument exactly equals the
    # trusted schema default, it closes without a semantic Binding even when a
    # conservative Contract also names the parameter.  Non-default values still
    # require the normal Contract/Binding proof below.
    defaulted = frozenset(
        name for name, value in arguments.items()
        if name in defaults and equal(name, defaults[name], value))
    extra = next((name for name in arguments
                  if name not in contracted and name not in defaulted), None)
    if extra is not None:
        return Verdict(False, f"uncontracted-arg:{extra}")
    restricted_default = next(
        (name for name in defaulted if name in exact_only), None)
    if restricted_default is not None:
        return Verdict(False, f"uncontracted-arg:{restricted_default}")
    missing = next((name for name in required if name not in arguments), None)
    if missing is not None:
        return Verdict(False, f"missing-arg:{missing}")

    refs = []
    for name, value in arguments.items():
        if name in defaulted:
            continue
        ok, proof_refs = _trace_argument(
            state, clause.effect_arguments.get(name), value,
            lambda left, right: equal(name, left, right),
            delegated_proofs.get((clause.id, name), ()),
            exact_proofs.get((clause.id, name), ()),
            grounded_proofs.get((clause.id, name), ()),
            semantic_proofs.get((clause.id, name), ()),
            (name in content and _has_derive_source(
                contract, clause.effect_arguments.get(name))),
            name in exact_only)
        if not ok:
            return Verdict(False, f"untraceable-arg:{name}")
        refs.extend(proof_refs)

    unauthorized = None
    for name, atoms in content_atoms.items():
        spec = clause.effect_arguments.get(name)
        local_delegation = (
            isinstance(spec, dict) and
            set(spec) == {"from", "delegated"} and
            bool(delegated_proofs.get((clause.id, name))))
        unauthorized = next(
            (atom for atom in atoms
             if not local_delegation and
             not _atom_authorized(state, contract, atom)), None)
        if unauthorized is not None:
            break
    if unauthorized is not None:
        return Verdict(False, "unauthorized-content-atom:" + unauthorized)
    return Verdict(True, "traced", tuple(dict.fromkeys(refs)), clause.id)


def check_effect(state: RuntimeState, contract, action: str, arguments: dict,
                 *, required=frozenset(), content=frozenset(),
                 content_atoms=None, delegated_proofs=None,
                 exact_proofs=None, grounded_proofs=None,
                 semantic_proofs=None, defaults=None,
                 exact_only=None, equal=None) -> Verdict:
    """PASS iff one Root Effect Clause closes every supplied argument."""
    action, arguments = str(action), dict(arguments or {})
    content_atoms = dict(content_atoms or {})
    delegated_proofs = dict(delegated_proofs or {})
    exact_proofs = dict(exact_proofs or {})
    grounded_proofs = dict(grounded_proofs or {})
    semantic_proofs = dict(semantic_proofs or {})
    defaults = dict(defaults or {})
    exact_only = frozenset(map(str, exact_only or ()))
    equal = equal or (lambda _name, left, right: left == right)
    clauses = [clause for clause in contract.clauses
               if isinstance(clause, EffectClause) and clause.action == action]
    if not clauses:
        return Verdict(False, f"unauthorized-action:{action}")
    first_failure = None
    for clause in clauses:
        verdict = _check_clause(
            state, contract, clause, arguments, frozenset(required),
            frozenset(content), content_atoms, delegated_proofs,
            exact_proofs, grounded_proofs, semantic_proofs,
            defaults, equal, exact_only)
        if verdict.ok:
            return verdict
        first_failure = first_failure or verdict
    return first_failure or Verdict(False, "untraceable")
