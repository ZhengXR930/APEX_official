"""Single task-local runtime state for the lean defense.

Everything the deterministic pipeline needs lives in one object: the
observation receipts the agent has read and the clause outputs resolved from
them. There are deliberately no caches and no per-proposal
budgets — the hot path is deterministic, so there is nothing to memoize across
proposals and nothing to ration.  This replaces the ~30 scattered dicts/sets of
the previous WrapRuntime.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


def stable(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      default=str, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(stable(value).encode()).hexdigest()


UNRESOLVED = object()  # sentinel: a clause role with no runtime value yet

QUERY_REF = "<query>"            # provenance: fixed by the trusted request
SEMANTIC_REF = "<semantic-content>"  # support only; never target authority
GROUNDED_REF = "<grounded-derive>"  # runtime-issued Derive grounding witness
CONTEXT_REF = "<runtime-context>"  # provenance: a registered runtime context value


@dataclass(frozen=True)
class Receipt:
    """One observation return the agent actually read."""
    capability: str
    arguments: dict
    value: object
    effect_return: bool = False
    receipt_role: str = "data"
    @property
    def digest(self) -> str:
        # A Receipt identifies one concrete call, not merely its return value.
        # Two calls that return the same scalar must retain distinct argument
        # provenance for downstream effect-return Binding.
        return digest({
            "capability": self.capability,
            "arguments": self.arguments,
            "value": self.value,
            "effect_return": self.effect_return,
            "receipt_role": self.receipt_role,
        })


@dataclass(frozen=True)
class Binding:
    """One resolved clause output and the receipt refs that back it.

    ``kind`` is acquire|derive|conditional.  ``refs`` are the exact
    receipt provenance pointers (``<digest>#<path>``) the value derives from;
    WRAP uses them to trace an effect argument back to trusted origin.
    """
    clause_id: str
    kind: str
    value: object
    refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClauseReceiptBinding:
    """Persistent ownership edge; it does not choose a Clause output value."""
    clause_id: str
    receipt_digest: str


@dataclass
class RuntimeState:
    receipts: list[Receipt] = field(default_factory=list)
    clause_receipts: dict[str, set[str]] = field(default_factory=dict)
    # Exact Clause→Receipt edges issued from a successful WRAP-authorized
    # effect-return invocation. Unlike ordinary semantic Acquire ownership,
    # these edges need no later dynamic-call re-resolution.
    effect_return_bindings: set[tuple[str, str]] = field(default_factory=set)
    # Digests of Receipts that WRAP authorized as an Effect return. Their
    # committed arguments/return are authorized facts of this episode, usable as
    # deterministic provenance for a later Effect argument without re-grounding.
    authorized_effect_returns: set[str] = field(default_factory=set)
    superseded_receipts: set[str] = field(default_factory=set)
    bindings: dict[str, Binding] = field(default_factory=dict)
    supporting_clauses: list[dict] = field(default_factory=list)
    invalidated_receipts: list[Receipt] = field(default_factory=list)
    invalidated_bindings: dict[str, Binding] = field(default_factory=dict)

    def record(self, receipt: Receipt) -> Receipt:
        self.receipts.append(receipt)
        return receipt

    @property
    def receipt_version(self) -> tuple[str, ...]:
        return tuple(sorted(
            receipt.digest for receipt in self.receipts
            if receipt.digest not in self.superseded_receipts))

    def active_receipts(self) -> tuple[Receipt, ...]:
        return tuple(receipt for receipt in self.receipts
                     if receipt.digest not in self.superseded_receipts)

    def admit(self, clause_id: str, receipt: Receipt) -> bool:
        """Add one order-independent Clause→Receipt ownership edge.

        Repeating the same concrete call supersedes its older return for this
        Clause. Different calls remain jointly owned and are resolved lazily.
        """
        clause_id = str(clause_id)
        owned = self.clause_receipts.setdefault(clause_id, set())
        if receipt.digest in owned:
            return False
        for prior in self.receipts:
            if (prior.digest in owned and
                    prior.capability == receipt.capability and
                    stable(prior.arguments) == stable(receipt.arguments)):
                owned.remove(prior.digest)
                self.superseded_receipts.add(prior.digest)
        owned.add(receipt.digest)
        return True

    def admit_effect_return(self, clause_id: str, receipt: Receipt) -> bool:
        """Record code-attested ownership of a successful Effect return."""
        changed = self.admit(clause_id, receipt)
        self.effect_return_bindings.add((str(clause_id), receipt.digest))
        self.authorized_effect_returns.add(receipt.digest)
        return changed

    def authorized_effect_receipts(self) -> tuple[Receipt, ...]:
        """Active Receipts issued by a WRAP-authorized Effect return.

        Their committed values are authorized facts of this episode, so a later
        Effect argument that reuses one of them (for example running the exact
        artifact a prior authorized ``write`` created) traces to real
        provenance instead of a re-grounded semantic judgment.
        """
        return tuple(receipt for receipt in self.active_receipts()
                     if receipt.digest in self.authorized_effect_returns)

    def is_effect_return_binding(
            self, clause_id: str, receipt_digest: str) -> bool:
        return (str(clause_id), str(receipt_digest)) in \
            self.effect_return_bindings

    def receipts_for(self, clause_id: str) -> tuple[Receipt, ...]:
        owned = self.clause_receipts.get(str(clause_id), set())
        return tuple(receipt for receipt in self.active_receipts()
                     if receipt.digest in owned)

    def bind(self, binding: Binding) -> Binding:
        # Append-only within a task: a clause role is resolved at most once.
        if binding.clause_id in self.bindings:
            raise ValueError(f"clause {binding.clause_id} already bound")
        self.bindings[binding.clause_id] = binding
        return binding

    def output(self, clause_ref: str):
        """Resolved value for a ``cN.name`` reference, or UNRESOLVED."""
        clause_id = str(clause_ref).partition(".")[0]
        binding = self.bindings.get(clause_id)
        return binding.value if binding is not None else UNRESOLVED

    @staticmethod
    def _root(ref: str) -> str:
        value = str(ref)
        return value.split("#", 1)[0] + "#" if "#" in value else value

    def invalidate_receipts(self, receipt_digests) -> tuple[str, ...]:
        """Remove receipt roots and their complete Binding dependency closure.

        Removed objects remain in the audit archive, but every proof routine
        sees only the surviving active lists. Binding refs already contain the
        transitive receipt roots of their inputs, so one pass closes the
        derivation dependency set.
        """
        roots = {str(item).split("#", 1)[0]
                 for item in receipt_digests or ()}
        if not roots:
            return ()
        kept = []
        for receipt in self.receipts:
            if receipt.digest in roots:
                self.invalidated_receipts.append(receipt)
            else:
                kept.append(receipt)
        self.receipts = kept
        self.superseded_receipts.difference_update(roots)
        self.authorized_effect_returns.difference_update(roots)
        self.effect_return_bindings = {
            (clause_id, receipt_digest)
            for clause_id, receipt_digest in self.effect_return_bindings
            if receipt_digest not in roots}
        for clause_id in list(self.clause_receipts):
            self.clause_receipts[clause_id].difference_update(roots)
            if not self.clause_receipts[clause_id]:
                del self.clause_receipts[clause_id]

        removed = []
        for clause_id, binding in list(self.bindings.items()):
            if any(str(ref).split("#", 1)[0] in roots
                   for ref in binding.refs if "#" in str(ref)):
                removed.append(clause_id)
                self.invalidated_bindings[clause_id] = binding
                del self.bindings[clause_id]
        self.supporting_clauses = [
            row for row in self.supporting_clauses
            if not any(str(ref).split("#", 1)[0] in roots
                       for ref in row.get("refs", ()) if "#" in str(ref))]
        return tuple(sorted(removed))

    def replace_receipt(self, receipt_digest: str, value) -> Receipt | None:
        """Replace one invalidated receipt with a sanitized immutable version."""
        old = next((receipt for receipt in self.receipts
                    if receipt.digest == str(receipt_digest)), None)
        if old is None:
            return None
        self.invalidate_receipts((old.digest,))
        replacement = Receipt(
            old.capability, dict(old.arguments), value,
            old.effect_return, old.receipt_role)
        self.receipts.append(replacement)
        return replacement

    def close(self) -> dict:
        audit = {"receipts": len(self.receipts),
                 "receipt_bindings": {
                     clause_id: len(digests)
                     for clause_id, digests in self.clause_receipts.items()},
                 "effect_return_bindings": len(self.effect_return_bindings),
                 "bindings": {cid: b.kind for cid, b in self.bindings.items()},
                 "supporting_clauses": list(self.supporting_clauses),
                 "invalidated_receipts": len(self.invalidated_receipts),
                 "invalidated_bindings": {
                     cid: binding.kind
                     for cid, binding in self.invalidated_bindings.items()}}
        self.receipts.clear()
        self.clause_receipts.clear()
        self.effect_return_bindings.clear()
        self.authorized_effect_returns.clear()
        self.superseded_receipts.clear()
        self.bindings.clear()
        self.supporting_clauses.clear()
        self.invalidated_receipts.clear()
        self.invalidated_bindings.clear()
        return audit
