"""Thin orchestration: bind -> resolve -> gate, with independent PLANT.

One Episode owns one RuntimeState.  Reads are bound to Acquire roles and used
to resolve Conditional outputs deterministically; effects are traced by WRAP
and, independently, screened by PLANT for commitment.  The engine holds no
budgets; it owns the task/observation-keyed PLANT proposal cache shared by its
Episodes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from apex.defender.contract import (AcquireClause, ConditionalClause,
                                        DeriveClause, EffectClause,
                                        spec_sources)
from apex.defender.continuation import (REPAIR, REPLAN,
                                       ContinuationController)
from apex.defender.receipt_binding import bind_acquire, bind_effect_return
from apex.defender.plant import CALL, RESPONSE, CommitEvent, Plant
from apex.defender.memory import SourceSurface, argument_values_equal
from apex.defender.state import Receipt, RuntimeState, UNRESOLVED, digest
from apex.defender.proof import apply_placements, compile_goals
from apex.defender.wrap import authority_atoms, check_effect


@dataclass(frozen=True)
class Decision:
    route: str  # pass | deny | approval | commitment | replan
    reason: str = ""
    refs: tuple = field(default_factory=tuple)
    # Gating commitment tokens: these witness an action and set the route.
    commitments: tuple = field(default_factory=tuple)
    # Every PLANT detection at this boundary, gating or not, so a report-only
    # propagation stays measurable without being counted as an action.
    detections: tuple = field(default_factory=tuple)
    # Present only for an exact, task-local one-shot approval flow.
    approval_id: str = ""
    approval: dict = field(default_factory=dict)
    # Safe-by-default compatibility: an adapter that ignores these fields still
    # sees the original refusal. Only continue_decision() may consume the plan.
    continuation_id: str = ""
    continuation: dict = field(default_factory=dict)
    authorized_arguments: dict = field(default_factory=dict)
    # Internal WRAP witness used to retire exactly one Contract obligation
    # after the native invocation succeeds.
    effect_clause_id: str = ""


@dataclass(frozen=True)
class AuthorityScope:
    """The exact Effect region covered by one trusted authority receipt."""
    action: str
    arguments: tuple[tuple[str, object], ...] = ()

    @classmethod
    def for_effect(cls, action: str, arguments=None):
        return cls(str(action), tuple(sorted(
            (str(name), value) for name, value in
            dict(arguments or {}).items())))

    def to_dict(self) -> dict:
        return {"action": self.action, "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class BasisReceipt:
    """Runtime-issued premise with a conservative source role.

    Ordinary exports are ``authority=False``.  An authority receipt must name
    an exact Effect scope; it is never inferred from prose or from an Agent.
    """
    id: str
    source: str
    value: object
    receipt_role: str = "advisory"
    authority: bool = False
    scope: AuthorityScope | None = None
    decoy: bool = False
    # Present only for an automatically mediated cross-unit transfer. These
    # fields bind the opaque handle to the exact runtime fact and intended
    # consumer; they never grant authority.
    receipt_digest: str = ""
    consumer: str = ""


@dataclass(frozen=True)
class BasisAccessReceipt:
    """Runtime witness that one actor dereferenced one issued handle."""
    id: str
    basis_id: str
    actor: str


@dataclass(frozen=True)
class CarrierView:
    """Native carrier value plus defense-only basis sidecars."""
    value: object
    basis_receipts: tuple[BasisReceipt, ...] = ()
    proposal: dict = field(default_factory=dict)

    @property
    def artifact_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            str(item.get("operand"))
            for item in self.proposal.get("placements", ())
            if isinstance(item, dict) and item.get("kind") == "artifact" and
            item.get("operand")))


@dataclass(frozen=True)
class EffectEnvelope:
    """Defense-only metadata carried beside an unchanged native payload."""
    payload: object
    proof_refs: tuple[str, ...] = ()


def _pointer_matches(pattern: str, path: str) -> bool:
    """Match one JSON pointer; ``*`` denotes exactly one segment."""
    expected = str(pattern).split("/")[1:]
    actual = str(path).split("/")[1:]
    return (len(expected) == len(actual) and
            all(left == "*" or left == right
                for left, right in zip(expected, actual)))


def _redact_marker(value, paths, operands):
    """Remove exact marked operands from selected JSON leaves only."""
    paths = frozenset(map(str, paths or ()))
    operands = tuple(str(item) for item in operands if isinstance(item, str) and item)

    def walk(node, path=""):
        if isinstance(node, dict):
            return {key: walk(child, path + "/" + str(key).replace(
                "~", "~0").replace("/", "~1"))
                    for key, child in node.items()}
        if isinstance(node, (list, tuple)):
            return [walk(child, path + "/" + str(index))
                    for index, child in enumerate(node)]
        if path in paths and isinstance(node, str):
            for operand in operands:
                node = node.replace(operand, "[invalidated]")
        return node

    return walk(value)


class Episode:
    """Run one trusted task under the lean defense.

    One Binding Agent handles Acquire ambiguity and proposal-local Derive
    placement. Deterministic code owns every candidate, ref, value, and closed
    operator replay.
    """

    def __init__(self, contract, nonce: str, *, capabilities=None,
                 skills=None,
                 acquire_agent=None, binding_agent=None,
                 plant_agent=None, plant_cache=None, plant_surfaces=None,
                 approval_enabled: bool = True,
                 continuation_enabled: bool = True,
                 max_replans: int = 1,
                 continuation_explanation_agent=None,
                 wrap_enabled: bool = True,
                 plant_enabled: bool = True):
        self.contract = contract
        self.nonce = str(nonce)
        self.state = RuntimeState()
        self.wrap_enabled = bool(wrap_enabled)
        self.plant_enabled = bool(plant_enabled)
        self.plant = Plant(contract, nonce, placement_agent=plant_agent,
                           cache=plant_cache, surfaces=plant_surfaces)
        self.capabilities = capabilities or {}
        self.skills = skills or {}
        self.acquire_agent = acquire_agent
        self.binding_agent = binding_agent
        self.approval_enabled = bool(approval_enabled)
        self.continuation = (ContinuationController(
            contract, max_replans=max_replans,
            explanation_agent=continuation_explanation_agent,
            capabilities=self.capabilities)
            if continuation_enabled else None)
        self._plant_scopes: dict[str, dict] = {}
        # Non-Receipt carriers (Skill prose, MCP resources) are immutable for
        # one Episode.  Continuations replay this exact decorated view; they
        # never reconstruct Agent input from the raw carrier.
        self._source_carriers: dict[str, tuple[str, CarrierView]] = {}
        self._acquire_cache: dict[tuple, object] = {}
        self._proposal_binding_cache: dict[tuple, dict] = {}
        self._basis_receipts: dict[str, BasisReceipt] = {}
        self._basis_accesses: dict[str, BasisAccessReceipt] = {}
        self._proof_presentations: list[dict] = []
        self._dependency_transfers: dict[tuple[str, str], CarrierView] = {}
        self._approval_requests: dict[str, dict] = {}
        self._approval_grants: set[str] = set()
        self._approval_denied: set[str] = set()
        self._approval_consumed: set[str] = set()
        # WRAP-issued provenance for dual effect/observation capabilities.
        # The native adapter emits the corresponding Receipt only after the
        # authorized invocation succeeds; consuming this queue then links the
        # return to matching Acquire roles without model involvement.
        self._authorized_effect_returns: dict[tuple[str, str], list[str]] = {}
        # Deterministic route-verdict memo. In runaway replan loops the Agent
        # re-submits equivalent already-rejected candidates; each one otherwise
        # re-invokes the Binding Agent (whose own cache key folds in the
        # state-dependent proof goals, so it can miss) and re-runs the WRAP
        # check. Keyed on the exact inputs that determine the verdict — action,
        # arguments, the immutable Receipt snapshot, the value-scoped
        # restrictions, and the cited authority — a hit is provably identical to
        # recomputation, so reusing it changes cost only, never semantics.
        self._verdict_memo: dict[tuple, object] = {}
        # Identity carriers have two planes. The Agent sees a readable view
        # plus an opaque episode-local handle; only the broker can recover the
        # exact operator-issued value accepted by the native boundary. This
        # keeps untrusted prose visible/plantable without rewriting the handle.
        self._identity_handles: dict[str, dict] = {}
        self._identity_views: dict[str, dict] = {}

    def _ref_reaches_quantified_clause(self, ref: str, seen=None) -> bool:
        """Whether a Contract output reference ranges over a finite collection.

        A single Effect clause may authorize multiple native invocations when
        its argument is drawn from a quantified Acquire/Derive or a closed
        Conditional over such quantified facts. Clause-level retirement is only
        valid for scalar, single-obligation Effects.
        """
        if seen is None:
            seen = set()
        ref = str(ref)
        if ref in seen:
            return False
        seen.add(ref)
        clause = next(
            (item for item in self.contract.clauses
             if getattr(item, "output_ref", None) == ref),
            None)
        if clause is None:
            return False
        if isinstance(clause, (AcquireClause, DeriveClause)) and clause.quantified:
            return True
        if isinstance(clause, ConditionalClause):
            sources = tuple(str(item) for item in clause.operand_refs)
        else:
            sources = tuple(str(item) for item in getattr(clause, "sources", ()))
        return any(
            self._ref_reaches_quantified_clause(source, seen)
            for source in sources
            if isinstance(source, str) and "." in source)

    def _effect_clause_has_quantified_domain(self, clause_id: str) -> bool:
        clause_id = str(clause_id or "")
        for clause in self.contract.clauses:
            if not (isinstance(clause, EffectClause) and clause.id == clause_id):
                continue
            sources = [
                source
                for spec in clause.effect_arguments.values()
                for source in spec_sources(spec)
            ]
            return any(self._ref_reaches_quantified_clause(source)
                       for source in sources)
        return False

    def extend_contract(self, contract) -> None:
        """Advance one episode after a new trusted user turn arrives.

        The runtime may extend authority only from an actually received trusted
        history. Existing receipts and PLANT state stay episode-local; every
        component that consults the Contract is switched atomically.
        """
        if not contract.clauses:
            raise ValueError("extended Contract must be non-empty")
        if contract.task != self.contract.task and not contract.task.startswith(
                self.contract.task + "\n"):
            raise ValueError("extended Contract must preserve trusted task history")
        self.contract = contract
        self.plant.contract = contract
        if self.continuation is not None:
            self.continuation.contract = contract
        self._acquire_cache.clear()
        self._proposal_binding_cache.clear()
        self._authorized_effect_returns.clear()
        self._verdict_memo.clear()
        self._reconcile()

    # -- observation --------------------------------------------------------
    def record_receipt(self, capability: str, arguments: dict, value) -> Receipt:
        """Record one mediated runtime fact without exposing it as input.

        This is the boundary for structured Agent outputs such as a control
        decision.  Recording provenance never grants authority; an adapter must
        separately present a scoped authority receipt before an Effect.
        """
        surface = self.capabilities.get(str(capability))
        receipt = self.state.record(
            Receipt(str(capability), dict(arguments or {}), value,
                    bool(surface is not None and surface.committed_return),
                    getattr(surface, "receipt_role", "data")))
        if receipt.effect_return:
            key = (receipt.capability, digest(receipt.arguments))
            clauses = self._authorized_effect_returns.get(key, [])
            if clauses:
                # WRAP authorized this exact Effect, so its committed return is
                # an authorized fact of the episode even when no Acquire role
                # owns it. Mark it so a later Effect argument can trace to this
                # authorized product deterministically (see proof.py).
                self.state.authorized_effect_returns.add(receipt.digest)
                bind_effect_return(
                    self.state, self.contract, receipt, clauses.pop(0))
                if not clauses:
                    self._authorized_effect_returns.pop(key, None)
        self._reconcile()
        return receipt

    def observe(self, capability: str, arguments: dict, value, *,
                consumer: str | None = None, placement_schema=None,
                return_view: bool = False):
        """Record a read and expose it at its actual runtime boundary.

        ``consumer`` is an execution identity, not a semantic label. When a
        mediated Receipt crosses into another Tool/MCP/Skill/Agent unit, the
        Runtime automatically creates the dependency carrier. Adapters may
        report the real consumer but cannot opt a value into or out of the
        dependency plane.
        """
        receipt = self.record_receipt(str(capability), arguments, value)
        if consumer is not None:
            view = self.transfer(receipt, consumer, schema=placement_schema)
            if self.continuation is not None:
                self.continuation.record_receipt_view(receipt, view.value)
            return view if return_view else view.value
        if return_view:
            # This is a versioned Receipt carrier, not a source-only document;
            # repeated observations from one capability may legitimately vary.
            view = self._place_carrier(
                str(capability), value, modes=("marker",),
                schema=placement_schema, surface_cards=())
            self._index_plant_scopes(str(capability), view.value, receipt)
            if self.continuation is not None:
                self.continuation.record_receipt_view(receipt, view.value)
            return view
        identity_view = self._identity_carrier_view(
            str(capability), value, receipt)
        if identity_view is not None:
            if self.continuation is not None:
                self.continuation.record_receipt_view(receipt, identity_view)
            return identity_view
        if not self.plant_enabled:
            return value
        exposed = self.plant.decorate(str(capability), value)
        self._index_plant_scopes(str(capability), exposed, receipt)
        if self.continuation is not None:
            self.continuation.record_receipt_view(receipt, exposed)
        return exposed

    def _identity_carrier_view(self, source: str, value,
                               receipt: Receipt | None = None):
        """Separate typed identity handles from their model-visible views.

        Identity positions come only from the operator manifest. The handle is
        episode-local and maps to the exact raw scalar; the view is ordinary
        untrusted observation text and therefore remains eligible for PLANT.
        ``None`` means the capability has no declared identity positions.
        """
        surface = self.capabilities.get(str(source))
        patterns = tuple(getattr(surface, "identity_paths", ()) or ())
        if not patterns:
            return None

        issued: list[tuple[str, object, str]] = []

        def walk(node, path=""):
            if isinstance(node, dict):
                return {key: walk(child, path + "/" + str(key).replace(
                    "~", "~0").replace("/", "~1"))
                        for key, child in node.items()}
            if isinstance(node, (list, tuple)):
                return [walk(child, path + "/" + str(index))
                        for index, child in enumerate(node)]
            if not any(_pointer_matches(pattern, path)
                       for pattern in patterns):
                return node
            handle = "IDENTITY-" + digest({
                "nonce": self.nonce, "source": str(source),
                "path": path, "value": node,
            })[:16]
            # JSON quoting makes the view boundaries explicit while retaining
            # every byte of untrusted content for the Agent and PLANT.
            text = str(node)
            display = re.split(r"[\x00-\x1f\x7f]", text, maxsplit=1)[0]
            display = display or text
            rendered = ("IDENTITY_HANDLE=" + handle + "; DISPLAY=" +
                        json.dumps(display, ensure_ascii=False) +
                        "; UNTRUSTED_VIEW=" +
                        json.dumps(node, ensure_ascii=False, default=str))
            self._identity_handles[handle] = {
                "value": node, "identities": ()}
            issued.append((handle, node, display))
            return rendered

        visible = walk(value)
        if not issued:
            return None
        if not self.plant_enabled:
            return visible

        # Placement sees the original capability/dataflow, but marker
        # validation runs on the view plane, where rewriting cannot corrupt an
        # executable identity. Object append is intentionally unavailable for
        # this representation: it would manufacture a new identity.
        self.plant.exposed += 1
        proposal = self.plant.propose(
            str(source), visible, modes=("marker",))
        view_source = str(source) + "#identity-view"
        placements = proposal.get("placements", ())
        decorated, _ = self.plant.apply_markers(
            view_source, visible,
            (item for item in placements if item.get("kind") == "marker"))

        def register(node):
            if isinstance(node, dict):
                for child in node.values():
                    register(child)
            elif isinstance(node, (list, tuple)):
                for child in node:
                    register(child)
            elif isinstance(node, str):
                for handle, raw, _display in issued:
                    if handle in node:
                        # Selecting an identity is not evidence that the Agent
                        # adopted text merely displayed beside it. Markers
                        # still gate if copied as ordinary outbound content.
                        record = {"value": raw, "identities": ()}
                        self._identity_views[node] = record
                        self._identity_handles[handle] = record

        register(decorated)
        counts = {}
        for _handle, _raw, display in issued:
            counts[display] = counts.get(display, 0) + 1
        for _handle, raw, display in issued:
            if counts[display] == 1:
                self._identity_views[display] = {
                    "value": raw, "identities": ()}
        self._index_plant_scopes(view_source, decorated, receipt)
        return decorated

    def resolve_identity_handles(self, value, *, include_identities=False):
        """Resolve exact issued handles/views recursively for native use.

        Substring matching is deliberately forbidden: arbitrary Agent text
        containing a handle never becomes an executable identity.
        """
        def walk(node):
            if isinstance(node, dict):
                out, identities = {}, []
                for key, child in node.items():
                    resolved, selected = walk(child)
                    out[key] = resolved
                    identities.extend(selected)
                return out, identities
            if isinstance(node, (list, tuple)):
                out, identities = [], []
                for child in node:
                    resolved, selected = walk(child)
                    out.append(resolved)
                    identities.extend(selected)
                return out, identities
            if isinstance(node, str):
                record = (self._identity_handles.get(node) or
                          self._identity_views.get(node))
                if record is not None:
                    return record["value"], list(record["identities"])
            return node, []

        resolved, identities = walk(value)
        identities = tuple(dict.fromkeys(map(str, identities)))
        return ((resolved, identities) if include_identities else resolved)

    # -- substrate PLANT carriers ------------------------------------------
    def arm_substrate(self, carrier: str, sample) -> str:
        """Return the token a trusted control/state adapter must embed.

        The adapter owns the native artifact — a honey file or record, a
        permission entry, a test account, an authorization handle, or a decoy
        tool/resource — and only a real interaction with it commits.
        """
        if not self.plant_enabled:
            return ""
        decoy = self.plant.arm_substrate(carrier, sample)
        self._plant_scopes[decoy.token] = {
            "mechanism": decoy.mechanism,
            "source": decoy.source,
            "refs": (),
            "source_refs": ("source:" + decoy.source,),
            "identities": decoy.identities or (decoy.token,),
            "operand": decoy.element,
            "paths": (),
        }
        return decoy.token

    def arm_attested_substrate(self, carrier: str, sample, *,
                               description: str = "",
                               kind: str = "state") -> str:
        """Arm one substrate after adapter-side deployability attestation.

        The carrier may originate from a Skill/MCP surface card rather than from
        the global perception plan.  The placement role can select the card, but
        deterministic adapter code must verify inert deployment and exact
        interaction attestation before calling this method.
        """
        if not self.plant_enabled:
            return ""
        carrier = str(carrier)
        if carrier not in self.plant.surfaces:
            self.plant.surfaces[carrier] = SourceSurface(
                carrier, str(description), True, str(kind))
        return self.arm_substrate(carrier, sample)

    def place_substrate(self, carrier: str, state, *, schema=None) -> str | None:
        """Ask the shared placement role whether to arm a state/control slot.

        Registration establishes that the adapter can deploy and monitor the
        carrier.  The Agent proposes only whether this episode benefits from an
        artifact; deterministic code creates the token and the adapter embeds
        it in native state.
        """
        if not self.plant_enabled:
            return None
        decoy = self.plant.place_substrate(str(carrier), state, schema=schema)
        if decoy is None:
            return None
        self._plant_scopes[decoy.token] = {
            "mechanism": decoy.mechanism,
            "source": decoy.source,
            "refs": (),
            "source_refs": ("source:" + decoy.source,),
            "identities": decoy.identities or (decoy.token,),
            "operand": decoy.element,
            "paths": (),
        }
        return decoy.token

    def expose(self, source: str, value):
        """Instrument registered non-tool model input without a Receipt.

        This is the seam for skill narrative and MCP resource content: material
        the agent reads that is not a Contract-bound acquisition.
        """
        return self.place_carrier(
            str(source), value, modes=("marker",)).value

    def _index_plant_scopes(self, source: str, exposed,
                            receipt: Receipt | None = None) -> None:
        """Tie deployed identities to the exact runtime carrier that held them."""
        for row in self.plant.placements:
            if str(row.get("source")) != str(source):
                continue
            token = str(row.get("token", ""))
            decoy = self.plant.deployed.get(token)
            if decoy is None or not decoy.recognizes((), exposed):
                continue
            paths = tuple(map(str, row.get("occurrences", ()) or ()))
            refs = (() if receipt is None else tuple(
                receipt.digest + "#" + path for path in paths))
            previous = self._plant_scopes.get(token, {})
            self._plant_scopes[token] = {
                "mechanism": decoy.mechanism,
                "source": decoy.source,
                "refs": tuple(dict.fromkeys((*previous.get("refs", ()), *refs))),
                "source_refs": (() if receipt is not None else
                                ("source:" + decoy.source,)),
                "identities": decoy.identities or (decoy.token,),
                "operand": decoy.element,
                "replacement": row.get("replacement", token),
                "paths": tuple(dict.fromkeys(
                    (*previous.get("paths", ()), *paths))),
            }

    def issue_basis(self, source: str, value, *, receipt_role: str | None = None,
                    authority: bool | None = None,
                    scope: AuthorityScope | None = None,
                    decoy: bool = False, receipt_digest: str = "",
                    consumer: str = "") -> BasisReceipt:
        """Issue one episode-local premise; only explicit trusted scope grants."""
        source = str(source)
        surface = self.capabilities.get(source)
        role = str(receipt_role or getattr(surface, "receipt_role", "advisory"))
        if role not in {"data", "advisory", "control"}:
            raise ValueError(f"unknown receipt role {role!r}")
        authorized = (role == "control" if authority is None else bool(authority))
        if decoy and authorized:
            raise ValueError("a dependency decoy cannot carry authority")
        if authorized and role != "control":
            raise ValueError("untrusted source role cannot introduce authority")
        if authorized and not isinstance(scope, AuthorityScope):
            raise ValueError("authority basis requires an exact Effect scope")
        if decoy:
            basis_id = self.plant.arm_basis(source, value).token
        else:
            basis_id = "BASIS-" + digest({
                "nonce": self.nonce, "source": source, "value": value,
                "role": role, "authority": authorized,
                "scope": None if scope is None else scope.to_dict(),
            })[:16]
        receipt = BasisReceipt(
            basis_id, source, value, role, authorized, scope, bool(decoy),
            str(receipt_digest), str(consumer))
        self._basis_receipts[basis_id] = receipt
        return receipt

    def resolve_basis(self, basis_id: str, *, actor: str) -> object:
        """Return a premise through the controlled carrier and attest access."""
        basis_id, actor = str(basis_id), str(actor)
        receipt = self._basis_receipts.get(basis_id)
        if receipt is None:
            raise ValueError("unknown basis handle")
        if receipt.consumer and receipt.consumer != actor:
            raise ValueError("basis handle is scoped to another consumer")
        access_id = "ACCESS-" + digest({
            "nonce": self.nonce, "basis": basis_id, "actor": actor,
        })[:16]
        self._basis_accesses.setdefault(
            access_id, BasisAccessReceipt(access_id, basis_id, actor))
        return receipt.value

    def place_carrier(self, source: str, value, *, modes=("marker",),
                      schema=None, surface_cards=()) -> CarrierView:
        """Place on a carrier that is not a cross-unit Receipt flow.

        Dependency placement is deliberately unavailable here: it is created
        only by :meth:`transfer`, after Runtime observes an actual producer to
        consumer flow.
        """
        source = str(source)
        modes = tuple(dict.fromkeys(map(str, modes or ())))
        if "basis" in modes:
            raise ValueError("dependency basis requires transfer(receipt, consumer)")
        fingerprint = digest(value)
        frozen = self._source_carriers.get(source)
        if frozen is not None:
            if frozen[0] != fingerprint:
                raise ValueError("source-only carrier changed within one Episode")
            return frozen[1]
        view = self._place_carrier(
            source, value, modes=modes, schema=schema,
            surface_cards=surface_cards)
        self._source_carriers[source] = (fingerprint, view)
        return view

    def _place_carrier(self, source: str, value, *, modes,
                       schema=None, surface_cards=(),
                       receipt: Receipt | None = None,
                       consumer: str = "") -> CarrierView:
        """Shared placement primitive; only ``transfer`` may request basis."""
        if not self.plant_enabled:
            receipts = ()
            if "basis" in tuple(map(str, modes or ())):
                if receipt is None or not consumer:
                    raise ValueError(
                        "dependency basis requires an exact Receipt flow")
                receipts = (self.issue_basis(
                    str(source), value, receipt_role=receipt.receipt_role,
                    authority=False, receipt_digest=receipt.digest,
                    consumer=consumer),)
            return CarrierView(value, receipts, {
                "source": str(source),
                "placements": [],
                "policy": "plant_disabled",
            } if not receipts else {
                "source": str(source),
                "placements": [],
                "basis_ids": [item.id for item in receipts],
                "policy": "plant_disabled",
            })
        self.plant.exposed += 1
        modes = tuple(dict.fromkeys(map(str, modes or ())))
        proposal = self.plant.propose(
            str(source), value, modes=modes, schema=schema,
            surface_cards=surface_cards)
        placements = proposal.get("placements", ())
        placed, _marker_count = self.plant.apply_markers(
            str(source), value,
            (item for item in placements if item.get("kind") == "marker"))
        receipts = ()
        if "basis" in modes:
            if receipt is None or not consumer:
                raise ValueError("dependency basis requires an exact Receipt flow")
            # Cross-unit data never acquires authority merely by being passed.
            # Both real and decoy handles retain the Receipt role and audience.
            real = self.issue_basis(
                str(source), placed, receipt_role=receipt.receipt_role,
                authority=False, receipt_digest=receipt.digest,
                consumer=consumer)
            receipts = (real,)
            basis = next((item for item in placements
                          if item.get("kind") == "basis"), None)
            if basis is not None:
                decoy = self.issue_basis(
                    str(source), basis.get("basis_value", ""),
                    receipt_role=receipt.receipt_role, authority=False,
                    decoy=True, receipt_digest=receipt.digest,
                    consumer=consumer)
                receipts += (decoy,)
        view = CarrierView(placed, receipts, dict(proposal))
        self._index_plant_scopes(str(source), view.value)
        return view

    def transfer(self, receipt: Receipt, consumer: str, *, schema=None,
                 surface_cards=()) -> CarrierView:
        """Expose one recorded Receipt to another execution unit.

        This is the sole dependency-carrier constructor. The decision is
        structural: an active Receipt is crossing to a named consumer. It
        does not depend on adapter roles, task semantics, or attack labels.
        Repeated delivery of the same fact to the same consumer reuses the
        episode-local carrier and therefore causes no additional Agent call.
        """
        if not any(item is receipt for item in self.state.receipts):
            raise ValueError("dependency transfer requires an active Receipt")
        consumer = str(consumer).strip()
        if not consumer:
            raise ValueError("dependency transfer requires a consumer identity")
        key = (receipt.digest, consumer)
        if key not in self._dependency_transfers:
            surface = self.capabilities.get(receipt.capability)
            inferred_schema = (schema if schema is not None else
                               getattr(surface, "output_schema", None))
            self._dependency_transfers[key] = self._place_carrier(
                receipt.capability, receipt.value,
                modes=("marker", "basis"), schema=inferred_schema,
                surface_cards=surface_cards, receipt=receipt,
                consumer=consumer)
        view = self._dependency_transfers[key]
        self._index_plant_scopes(receipt.capability, view.value, receipt)
        return view

    @staticmethod
    def envelope(payload, proof_refs=()) -> EffectEnvelope:
        """Build defense metadata without modifying the native payload."""
        refs = tuple(
            item.id if isinstance(item, BasisReceipt) else str(item)
            for item in (proof_refs or ()))
        return EffectEnvelope(payload, tuple(dict.fromkeys(refs)))

    def commit(self, channel: str, actor: str, value, *, proof_refs=(),
               identities=()) -> Decision:
        """Report one observable boundary and run all three PLANT sensors.

        ``channel`` is ``call``, ``response``, ``state`` or ``artifact`` — the
        boundary, which fixes the evidence semantics.  A certified observation
        identity in either an effect argument or the final response routes to
        ``commitment``; the former is ``effectful`` and the latter ``copied``.
        A really-invoked artifact gates. Dependency proof presentation is
        recorded but never authorizes or blocks by itself.

        Every adapter — tool, MCP or skill — reports its boundaries here.
        """
        if not self.plant_enabled:
            return Decision("pass", "PLANT disabled")
        envelope = (value if isinstance(value, EffectEnvelope)
                    else self.envelope(value, proof_refs))
        selected = frozenset(map(str, (*tuple(identities or ()), actor)))
        if (self.continuation is not None and
                selected & self.continuation.denied_resources):
            return Decision("deny", "continuation-denied-resource",
                            refs=tuple(sorted(
                                selected & self.continuation.denied_resources)))
        unknown = tuple(ref for ref in envelope.proof_refs
                        if ref not in self._basis_receipts)
        if unknown:
            return Decision("deny", "unknown-basis-ref:" + unknown[0],
                            refs=unknown)
        if envelope.proof_refs:
            self._proof_presentations.append({
                "actor": str(actor), "boundary": str(channel),
                "proof_refs": list(envelope.proof_refs),
            })
        events = self.plant.commit(CommitEvent(
            str(channel), str(actor), envelope.payload, envelope.proof_refs,
            tuple(identities or ())))
        detections = tuple(event.to_dict() for event in events)
        gating = tuple(event for event in events if event.gating)
        if gating:
            decision = Decision(
                "commitment", "PLANT commitment",
                refs=tuple(event.semantics for event in gating),
                commitments=tuple(event.token for event in gating),
                detections=detections)
            if self.continuation is None:
                return decision
            arguments = (dict(envelope.payload)
                         if isinstance(envelope.payload, dict)
                         else {"value": envelope.payload})
            plan = self.continuation.propose(
                self.state, action=str(actor), arguments=arguments,
                reason=decision.reason, refs=decision.refs,
                events=tuple(event.to_dict() for event in gating),
                plant_scopes=self._plant_scopes,
                proof_refs=envelope.proof_refs)
            return self._with_continuation(decision, plan)
        return Decision("pass", "clean commitment channel",
                        detections=detections)

    @staticmethod
    def _with_continuation(decision: Decision, plan, *, context=None,
                           authorized_arguments=None) -> Decision:
        value = plan.to_dict()
        if context is not None:
            value["state"] = context
        return Decision(
            decision.route, decision.reason, decision.refs,
            decision.commitments, decision.detections,
            decision.approval_id, dict(decision.approval),
            plan.id, value, dict(authorized_arguments or {}),
            decision.effect_clause_id)

    def _resolve_acquire_once(self, **request):
        """Cache one semantic choice for an identical observation mapping."""
        key = (str(request.get("capability", "")),
               digest(request.get("arguments", {})),
               digest(request.get("candidates", ())))
        if key not in self._acquire_cache:
            self._acquire_cache[key] = self.acquire_agent(**request)
        return self._acquire_cache[key]

    def _reconcile(self) -> None:
        """Reach a monotonic fixed point over the unordered Receipt set.

        This phase records only Clause→Receipt ownership. It never commits to
        a Clause output value, so a later Receipt can extend or supersede the
        evidence without depending on arrival order.
        """
        resolver = (self._resolve_acquire_once
                    if self.acquire_agent is not None else None)
        while True:
            before = sum(len(items)
                         for items in self.state.clause_receipts.values())
            for receipt in self.state.active_receipts():
                bind_acquire(
                    self.state, self.contract, receipt, resolver)
            after = sum(len(items)
                        for items in self.state.clause_receipts.values())
            if after == before:
                return

    def _resolve_proposal_bindings(self, action: str, arguments: dict,
                                   surface, *, use_agent: bool = True):
        """Resolve all arguments against one immutable Receipt snapshot.

        Deterministic code first closes exact and operator-replayable paths.
        If unresolved Derive goals remain, one Binding Agent call selects
        opaque evidence ids for the whole proposal. The answer is cached by the
        code-compiled proof-goal domain; replay computes every resulting value
        and ref from the current Receipt snapshot.
        """
        equal = lambda name, left, right: argument_values_equal(
            surface, name, left, right)
        goals, immediate, immediate_delegated = compile_goals(
            self.state, self.contract, action, arguments, surface, equal)
        proposal = {}
        if use_agent and goals and self.binding_agent is not None:
            # Cache the semantic choice by the exact code-compiled domain the
            # Agent can see.  An unrelated Receipt must not invalidate a
            # proposal, while any changed candidate value, role, type or
            # composition option necessarily changes this digest.  Replaying
            # the cached opaque ids still uses the current code-owned goals
            # below, so the Agent can never retain stale refs or expand scope.
            public_goals = [goal.public() for goal in goals]
            key = (str(action), digest(arguments), digest(public_goals))
            if key not in self._proposal_binding_cache:
                context = [skill.to_dict() for skill in self.skills.values()
                           if action in skill.tools]
                self._proposal_binding_cache[key] = self.binding_agent(
                    task=self.contract.task, action=str(action),
                    arguments=dict(arguments),
                    goals=public_goals,
                    skill_context=context) or {}
            proposal = self._proposal_binding_cache[key]
        return apply_placements(
            self.state, self.contract, action, arguments, surface,
            goals, immediate, immediate_delegated, proposal, equal)

    # -- effect -------------------------------------------------------------
    def _authority_proofs(self, proof_refs, action: str, arguments: dict,
                          surface) -> tuple[BasisReceipt, ...]:
        """Validate fresh, trusted, scope-matched authority receipts."""
        cited = (self._basis_receipts.get(
            item.id if isinstance(item, BasisReceipt) else str(item))
            for item in (proof_refs or ()))
        valid = []
        for receipt in cited:
            if (receipt is None or not receipt.authority or
                    receipt.scope is None or receipt.scope.action != str(action)):
                continue
            if all(name in arguments and argument_values_equal(
                    surface, name, expected, arguments[name])
                   for name, expected in receipt.scope.arguments):
                valid.append(receipt)
        return tuple(valid)

    def _validate_repair(self, action: str, arguments: dict, *, proof_refs=()):
        """Recheck a mechanically reconstructed proposal without any Agent."""
        action, arguments = str(action), dict(arguments or {})
        surface = self.capabilities.get(action)
        if not self.wrap_enabled:
            return True, ()
        if (surface is not None and surface.requires_authority_proof and
                not self._authority_proofs(
                    proof_refs, action, arguments, surface)):
            return False, ()
        if self.plant_enabled:
            events = self.plant.commit(CommitEvent(CALL, action, arguments))
            if any(event.gating for event in events):
                return False, ()
        required = frozenset(getattr(surface, "required", ()) or ())
        content = frozenset(
            name for name in (getattr(surface, "arguments", ()) or ())
            if surface is not None and surface.accepts_semantic_support(name))
        atoms = {
            name: authority_atoms(
                arguments.get(name), surface.authority_grammars(name))
            for name in content if name in arguments}
        proofs = self._resolve_proposal_bindings(
            action, arguments, surface, use_agent=False)
        defaults = {
            name: schema["default"]
            for name, schema in (getattr(surface, "argument_schemas", ()) or ())
            if isinstance(schema, dict) and "default" in schema}
        verdict = check_effect(
            self.state, self.contract, action, arguments,
            required=required, content=content, content_atoms=atoms,
            delegated_proofs=proofs.delegated,
            exact_proofs=proofs.exact,
            grounded_proofs=proofs.grounded,
            semantic_proofs=proofs.semantic,
            defaults=defaults,
            exact_only=(() if self.continuation is None else
                        self.continuation.restricted_arguments_for(
                            action, arguments)),
            equal=lambda name, left, right: argument_values_equal(
                surface, name, left, right))
        return verdict.ok, verdict.refs

    def _apply_invalidation(self, plan) -> None:
        """Invalidate the smallest runtime region represented by PLANT refs."""
        by_receipt: dict[str, dict] = {}
        for token in plan.denied_resources:
            scope = self._plant_scopes.get(str(token), {})
            if scope.get("mechanism") != "marker":
                continue
            for ref in scope.get("refs", ()):
                receipt_digest = str(ref).split("#", 1)[0]
                row = by_receipt.setdefault(
                    receipt_digest, {"paths": set(), "operands": set(),
                                     "replacements": set()})
                row["paths"].update(scope.get("paths", ()))
                operand = scope.get("operand")
                if isinstance(operand, str) and operand:
                    row["operands"].add(operand)
                replacement = scope.get("replacement")
                if isinstance(replacement, str) and replacement:
                    row["replacements"].add(replacement)

        replaced = set()
        for receipt_digest, row in by_receipt.items():
            receipt = next((item for item in self.state.receipts
                            if item.digest == receipt_digest), None)
            if receipt is None:
                continue
            sanitized = _redact_marker(
                receipt.value, row["paths"], row["operands"])
            if sanitized == receipt.value:
                self.state.invalidate_receipts((receipt_digest,))
            else:
                replacement = self.state.replace_receipt(
                    receipt_digest, sanitized)
                if self.continuation is not None and replacement is not None:
                    view = self.continuation._receipt_views.get(receipt_digest)
                    if view is not None:
                        self.continuation.record_receipt_view(
                            replacement,
                            _redact_marker(
                                view, row["paths"], row["replacements"]))
            replaced.add(receipt_digest)

        # If the adapter could identify only a receipt root, conservatively
        # invalidate it. Source-only Skill/MCP narrative is omitted from the
        # continuation context and remains audit evidence, not active proof.
        remaining = {
            str(ref).split("#", 1)[0]
            for ref in plan.invalidated_refs if "#" in str(ref)
        } - replaced
        self.state.invalidate_receipts(remaining)
        self._reconcile()

    def sanitized_source(self, source: str):
        """Replay the frozen decorated carrier, minus committed marker scopes."""
        source = str(source)
        frozen = self._source_carriers.get(source)
        if frozen is None:
            raise ValueError("source-only carrier was not placed in this Episode")
        if not self.plant_enabled:
            return frozen[1].value
        paths, replacements = set(), set()
        denied = (() if self.continuation is None else
                  self.continuation.denied_resources)
        for token in denied:
            scope = self._plant_scopes.get(str(token), {})
            if (scope.get("mechanism") == "marker" and
                    scope.get("source") == source):
                paths.update(scope.get("paths", ()))
                replacement = scope.get("replacement")
                if isinstance(replacement, str) and replacement:
                    replacements.add(replacement)
        return _redact_marker(frozen[1].value, paths, replacements)

    def continue_decision(self, decision: Decision) -> Decision:
        """Consume one attached plan; unchanged adapters remain fail-closed."""
        if self.continuation is None or not decision.continuation_id:
            return decision
        plan = self.continuation.consume(decision.continuation_id)
        if plan.mode == REPAIR:
            ok, refs = self._validate_repair(
                plan.action, plan.candidate_arguments or {},
                proof_refs=plan.proof_refs)
            if not ok:
                return Decision(
                    "deny", "repair-revalidation-failed",
                    detections=decision.detections,
                    continuation_id=plan.id,
                    continuation=plan.to_dict())
            repaired = Decision(
                "pass", "verified-repair", refs,
                detections=decision.detections)
            return self._with_continuation(
                repaired, plan,
                authorized_arguments=plan.candidate_arguments)
        if plan.mode == REPLAN:
            self._apply_invalidation(plan)
            context = self.continuation.context(self.state, plan)
            replanned = Decision(
                "replan", "sanitized-replan", decision.refs,
                decision.commitments, decision.detections)
            return self._with_continuation(replanned, plan, context=context)
        return self._with_continuation(
            Decision("deny", "safe-abort", decision.refs,
                     decision.commitments, decision.detections), plan)

    def effect_succeeded(
        self, action: str, arguments: dict, *, verified: bool = False,
        invocation_id: str = "", clause_id: str = "",
    ) -> None:
        """Record native execution without equating it to task completion.

        Most adapters can attest only that a tool crossed its native boundary.
        That is an attempted Effect, not a verified task outcome.  Adapters with
        an independent postcondition witness may opt into ``verified=True``.
        """
        if self.continuation is not None:
            self.continuation.record_effect(
                action, arguments, verified=verified,
                invocation_id=invocation_id, clause_id=clause_id)

    def _approval_scope(self, action: str, arguments: dict, required,
                        surface) -> str:
        """Classify only proposals that an exact user approval can recover.

        A missing Root Effect is eligible only after runtime evidence exists.
        For an existing Root Effect, approval may close unresolved ``from``
        roles, but never an extra argument, a missing required argument, or a
        conflict with a trusted literal.
        """
        roots = [clause for clause in self.contract.clauses
                 if isinstance(clause, EffectClause) and
                 clause.action == action]
        if (self.capabilities and
                (surface is None or not getattr(surface, "effect", False))):
            return ""
        if not roots:
            return "unknown-effect" if self.state.receipts else ""
        producers = {clause.output_ref: clause for clause in self.contract.clauses
                     if clause.output_ref}
        for clause in roots:
            specs = clause.effect_arguments
            if any(name not in specs for name in arguments):
                continue
            if any(name not in arguments for name in required):
                continue
            compatible = True
            for name, spec in specs.items():
                if name not in arguments:
                    continue
                if isinstance(spec, dict) and set(spec) == {"literal"}:
                    compatible = argument_values_equal(
                        surface, name, spec["literal"], arguments[name])
                    if not compatible:
                        break
                elif (isinstance(spec, dict) and
                      set(spec) in ({"from"}, {"from", "delegated"})):
                    sources = spec.get("from")
                    sources = ([sources] if isinstance(sources, str)
                               else list(sources or ()))
                    # Approval may fill an unresolved semantic role; it may not
                    # override an already replayed BindingProof with a
                    # conflicting proposal value.
                    if (sources and all(self.state.output(source) is not UNRESOLVED
                                        for source in sources) and
                            not all(isinstance(producers.get(source), DeriveClause)
                                    for source in sources)):
                        compatible = False
                        break
            if compatible:
                return "unresolved-binding"
        return ""

    def _approval_decision(self, action: str, arguments: dict, reason: str,
                           scope: str, detections=()) -> Decision:
        receipt_refs = tuple(receipt.digest + "#"
                             for receipt in self.state.receipts)
        approval_id = "APPROVAL-" + digest({
            "nonce": self.nonce,
            "task": self.contract.task,
            "action": action,
            "arguments": arguments,
            "receipts": receipt_refs,
        })[:20]
        if approval_id in self._approval_consumed:
            return Decision("deny", "approval-consumed", detections=detections)
        if approval_id in self._approval_denied:
            return Decision("deny", "approval-denied", detections=detections)
        request = {
            "id": approval_id,
            "scope": scope,
            "task": self.contract.task,
            "action": action,
            "arguments": dict(arguments),
            "receipt_refs": list(receipt_refs),
            "evidence": [
                {"ref": receipt.digest + "#",
                 "capability": receipt.capability,
                 "arguments": dict(receipt.arguments),
                 "value": receipt.value}
                for receipt in self.state.receipts
            ],
            "trigger": reason,
        }
        self._approval_requests.setdefault(approval_id, request)
        if approval_id in self._approval_grants:
            return Decision(
                "pass", "one-shot-approval", receipt_refs,
                detections=detections, approval_id=approval_id)
        return Decision(
            "approval", scope, receipt_refs, detections=detections,
            approval_id=approval_id, approval=request)

    def decide_approval(self, approval_id: str, approved: bool) -> None:
        """Record a trusted user's decision for one exact pending proposal."""
        approval_id = str(approval_id)
        if approval_id not in self._approval_requests:
            raise ValueError("unknown approval request")
        if approval_id in self._approval_consumed:
            raise ValueError("approval already consumed")
        if approved:
            self._approval_denied.discard(approval_id)
            self._approval_grants.add(approval_id)
        else:
            self._approval_grants.discard(approval_id)
            self._approval_denied.add(approval_id)

    def approval_succeeded(self, approval_id: str) -> None:
        """Consume a one-shot grant only after the native effect succeeds."""
        approval_id = str(approval_id)
        if approval_id not in self._approval_grants:
            raise ValueError("approval is not granted")
        self._approval_grants.remove(approval_id)
        self._approval_consumed.add(approval_id)

    def effect(self, action: str, arguments: dict, *, proof_refs=(),
               identities=()) -> Decision:
        """Gate one effect; advisory premises never contribute authority."""
        arguments = dict(arguments or {})
        commitment = self.commit(
            CALL, str(action), arguments, proof_refs=proof_refs,
            identities=identities)
        if commitment.route != "pass":
            return commitment
        # A non-gating detection still happened here; carry it through every
        # later verdict so a measurable signal is never lost to the route.
        seen = commitment.detections
        if (self.continuation is not None and
                self.continuation.effect_already_committed(
                    self.state, str(action), arguments)):
            return Decision(
                "deny", "effect-already-committed", detections=seen)
        if not self.wrap_enabled:
            return Decision(
                "pass", "WRAP disabled after clean PLANT commitment",
                detections=seen)
        surface = self.capabilities.get(str(action))
        authority_required = bool(
            surface is not None and surface.requires_authority_proof)
        authority = (self._authority_proofs(
            proof_refs, str(action), arguments, surface)
                     if authority_required else ())
        if authority_required and not authority:
            return Decision(
                "deny", "insufficient-authority-proof", detections=seen)
        # Runaway replan loops re-submit equivalent already-rejected candidates.
        # The WRAP verdict is a pure function of these inputs (action,
        # arguments, the immutable Receipt snapshot, the value-scoped
        # restrictions, and the cited authority), so a memo hit is identical to
        # recomputation — it only skips the Binding Agent call and the WRAP
        # replay, never altering the route. Continuation bookkeeping below still
        # runs so replan/abort budgets advance exactly as before.
        restricted = (self.continuation.restricted_arguments_for(
            str(action), arguments) if self.continuation is not None else ())
        required = frozenset(getattr(surface, "required", ()) or ())
        memo_key = (str(action), digest(arguments),
                    self.state.receipt_version,
                    digest(tuple(sorted(str(ref) for ref in (proof_refs or ())))),
                    digest(tuple(restricted)))
        verdict = self._verdict_memo.get(memo_key)
        if verdict is None:
            self._reconcile()
            proofs = self._resolve_proposal_bindings(
                str(action), arguments, surface)
            exact_proofs = dict(proofs.exact)
            if authority:
                # A trusted adapter may declare that this Root Effect is conferred
                # by cited, runtime-issued granting premises. Those receipts—not a
                # semantic model—then close its unresolved ordinary roles. Literal
                # conflicts and undeclared arguments remain impossible in WRAP.
                authority_refs = tuple(receipt.id for receipt in authority)
                for clause in self.contract.clauses:
                    if not (isinstance(clause, EffectClause) and
                            clause.action == str(action)):
                        continue
                    for name, spec in clause.effect_arguments.items():
                        if (name in arguments and isinstance(spec, dict) and
                                set(spec) == {"from"}):
                            exact_proofs.setdefault(
                                (clause.id, name), authority_refs)
            content = frozenset(
                name for name in (getattr(surface, "arguments", ()) or ())
                if surface is not None and surface.accepts_semantic_support(name))
            atoms = {
                name: authority_atoms(
                    arguments.get(name), surface.authority_grammars(name))
                for name in content if name in arguments}
            defaults = {
                name: schema["default"]
                for name, schema in (getattr(
                    surface, "argument_schemas", ()) or ())
                if isinstance(schema, dict) and "default" in schema}
            verdict = check_effect(
                self.state, self.contract, str(action), arguments,
                required=required, content=content,
                content_atoms=atoms, delegated_proofs=proofs.delegated,
                exact_proofs=exact_proofs,
                grounded_proofs=proofs.grounded,
                semantic_proofs=proofs.semantic, defaults=defaults,
                exact_only=restricted,
                equal=lambda name, left, right: argument_values_equal(
                    surface, name, left, right))
            self._verdict_memo[memo_key] = verdict
        if verdict.ok:
            if (self.continuation is not None and
                    self.continuation.clause_already_committed(
                        verdict.clause_id) and
                    not self._effect_clause_has_quantified_domain(
                        verdict.clause_id)):
                return Decision(
                    "deny", "effect-obligation-already-committed",
                    detections=seen)
            if surface is not None and surface.committed_return:
                key = (str(action), digest(arguments))
                self._authorized_effect_returns.setdefault(key, []).append(
                    verdict.clause_id)
            return Decision(
                "pass", verdict.reason, verdict.refs,
                detections=seen, effect_clause_id=verdict.clause_id)
        failed = Decision(
            "deny", verdict.reason, verdict.refs, detections=seen)
        plan = None
        if self.continuation is not None:
            plan = self.continuation.propose(
                self.state, action=str(action), arguments=arguments,
                reason=verdict.reason, refs=verdict.refs,
                required=required,
                equal=lambda name, left, right: argument_values_equal(
                    surface, name, left, right),
                validate=lambda candidate: self._validate_repair(
                    str(action), candidate, proof_refs=proof_refs),
                proof_refs=proof_refs)
            if plan.mode == REPAIR:
                return self._with_continuation(failed, plan)
        if self.approval_enabled:
            scope = self._approval_scope(
                str(action), arguments, required, surface)
            if scope:
                return self._approval_decision(
                    str(action), arguments, verdict.reason, scope, seen)
        return (self._with_continuation(failed, plan)
                if plan is not None else failed)

    def response(self, value, *, proof_refs=()) -> Decision:
        """Detect PLANT commitment at the final outbound response sink."""
        return self.commit(RESPONSE, "$response", value, proof_refs=proof_refs)

    def close(self) -> dict:
        basis = [{"id": receipt.id, "source": receipt.source,
                  "receipt_role": receipt.receipt_role,
                  "authority": receipt.authority,
                  "scope": (None if receipt.scope is None
                            else receipt.scope.to_dict()),
                  "decoy": receipt.decoy,
                  "receipt_digest": receipt.receipt_digest,
                  "consumer": receipt.consumer}
                 for receipt in self._basis_receipts.values()]
        accesses = [vars(receipt)
                    for receipt in self._basis_accesses.values()]
        presentations = list(self._proof_presentations)
        approvals = {
            "requested": len(self._approval_requests),
            "granted": len(self._approval_grants) + len(self._approval_consumed),
            "consumed": len(self._approval_consumed),
            "denied": len(self._approval_denied),
            "requests": list(self._approval_requests.values()),
        }
        self._approval_requests.clear()
        self._approval_grants.clear()
        self._approval_denied.clear()
        self._approval_consumed.clear()
        self._basis_receipts.clear()
        self._basis_accesses.clear()
        self._proof_presentations.clear()
        transfers = [
            {"receipt_digest": receipt_digest, "consumer": consumer,
             "basis_ids": [item.id for item in view.basis_receipts]}
            for (receipt_digest, consumer), view in
            self._dependency_transfers.items()
        ]
        self._dependency_transfers.clear()
        continuation = (self.continuation.close()
                        if self.continuation is not None else {})
        binding = {
            "acquire_decisions": len(self._acquire_cache),
            "proposal_decisions": len(self._proposal_binding_cache),
        }
        self._acquire_cache.clear()
        self._proposal_binding_cache.clear()
        self._authorized_effect_returns.clear()
        self._identity_handles.clear()
        self._identity_views.clear()
        return {"configuration": {
                    "wrap_enabled": self.wrap_enabled,
                    "plant_enabled": self.plant_enabled,
                },
                "wrap": self.state.close(), "plant": self.plant.close(),
                "binding": binding,
                "basis_receipts": basis, "basis_accesses": accesses,
                "dependency_transfers": transfers,
                "proof_presentations": presentations,
                "approvals": approvals,
                "continuation": continuation}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class Engine:
    """Perceive the environment, compile the current task, and start Episodes."""

    def __init__(self, model: str | None = None, *,
                 acquire_agent=None, binding_agent=None, plant_agent=None,
                 plant_cache=None,
                 approval_enabled: bool = True,
                 continuation_enabled: bool = True,
                 max_replans: int = 1,
                 continuation_explanation_agent=None):
        self.model = str(model) if model else ""
        self.wrap_enabled = True
        self.plant_enabled = True
        self.approval_enabled = bool(approval_enabled)
        self.continuation_enabled = bool(continuation_enabled)
        self.max_replans = max(0, int(max_replans))
        # The single validated binding agent handles genuine ambiguity that the
        # deterministic pipeline cannot; it can only narrow authority.
        if self.model and (acquire_agent is None or binding_agent is None):
            from apex.defender.binding_agent import BindingAgent
            agent = BindingAgent(self.model)
            acquire_agent = acquire_agent or agent.disambiguate_acquire
            binding_agent = binding_agent or agent.place_proposal
        self.acquire_agent = acquire_agent
        self.binding_agent = binding_agent
        if self.model and self.plant_enabled and plant_agent is None:
            from apex.defender.plant_agent import PlantPlacementAgent
            plant_agent = PlantPlacementAgent(self.model).place
        self.plant_agent = plant_agent
        self.continuation_explanation_agent = (
            continuation_explanation_agent or
            (self._make_continuation_explanation_agent(self.model)
             if self.model else None))
        if plant_cache is None:
            from apex.defender.plant_cache import ConcurrentPersistentCache
            plant_cache = ConcurrentPersistentCache()
        self._plant_cache = plant_cache
        self.plan = None

    @staticmethod
    def _make_continuation_explanation_agent(model: str):
        def explain(context: dict) -> str:
            from apex.defender.agent_role import run_typed_agent, typed_tool
            prompt = (
                "Polish this runtime refusal context into one concise "
                "natural-language explanation for a fresh recovery agent. "
                "Do not add new policy, authority, retry rules, or fields. "
                "Explain only why the previous attempted action or argument "
                "is not currently supported by the trusted task and verified "
                "runtime state.\n\nCONTEXT:\n" +
                json.dumps(context, ensure_ascii=False, default=str))
            tool = typed_tool(
                "submit_explanation",
                "Submit the advisory recovery explanation.",
                {
                    "why_not_supported": {
                        "type": "string",
                        "description": (
                            "One concise sentence; advisory only; no new "
                            "authorization or retry policy."),
                    },
                },
                required=("why_not_supported",))
            answer, _trace = run_typed_agent(
                name="ContinuationExplanationAgent",
                model=model,
                prompt=prompt,
                instructions=(
                    "You only rewrite an existing runtime refusal reason into "
                    "clear recovery guidance. Keep it general and task-scoped. "
                    "Never invent facts or authorization."),
                tool_schema=tool,
                timeout_seconds=60.0)
            return str(answer.get("why_not_supported", "")).strip()

        return explain

    def perceive(self, tool_schemas, source_carriers=(), skill_manifests=()):
        from apex.defender.surveyor import Surveyor
        tool_schemas = list(tool_schemas or ())
        Surveyor.validate_boundary_manifest(tool_schemas)
        self.plan = Surveyor(self.model or None).perceive(
            tool_schemas, list(source_carriers or ()),
            list(skill_manifests or ()))
        return self.plan

    def perceive_skills(self, skill_files, capability_manifest,
                        plant_carriers=(), skill_manifests=()):
        """Register Tool-local boundaries from one or more installed Skills."""
        from apex.defender.surveyor import Surveyor
        self.plan = Surveyor(self.model or None).perceive_skills(
            skill_files, capability_manifest, plant_carriers,
            skill_manifests)
        return self.plan

    def contract(self, task, effect_entries=None):
        from apex.defender.taskcontractor import TaskContractor
        if self.plan is None:
            raise RuntimeError("perceive() must run before contract synthesis")
        if not self.model:
            raise RuntimeError("contract synthesis requires a defense model")
        entries = tuple(sorted(map(str, effect_entries or ()))) or None
        return TaskContractor(self.model).extract(
            str(task), self.plan, effect_entries=entries)

    def start(self, contract, task_id: str | None = None) -> Episode:
        return Episode(
            contract, str(task_id or digest(contract.to_dict())),
            capabilities=getattr(self.plan, "capabilities", {}),
            skills=getattr(self.plan, "skills", {}),
            acquire_agent=self.acquire_agent,
            binding_agent=self.binding_agent,
            plant_agent=self.plant_agent, plant_cache=self._plant_cache,
            plant_surfaces=getattr(self.plan, "sources", {}),
            approval_enabled=self.approval_enabled,
            continuation_enabled=self.continuation_enabled,
            max_replans=self.max_replans,
            continuation_explanation_agent=self.continuation_explanation_agent,
            wrap_enabled=self.wrap_enabled,
            plant_enabled=self.plant_enabled)
