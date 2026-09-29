"""PLANT: active deception over one certified-token registry.

PLANT instruments the three things an agent can do with untrusted material —
*see* it, *decide* on it, and *act* on it — and then detects whether an
adversary adopted the instrumentation.  One decoy class covers each surface and
each class carries its own detection semantics:

===============  =========================================  ======================
class            deployed in                                a commitment means
===============  =========================================  ======================
``observation``  model-visible input: tool/MCP results,      the decoy was copied,
                 resource contents, skill narrative         referenced or selected
``dependency``   the decision basis / composition layer:    forged evidence was
                 runtime-issued premises for one choice     presented as proof
``substrate``    the execution environment: control plane,   a real invocation or
                 persistent state, decoy tools/resources    environment effect hit it
===============  =========================================  ======================

Every decoy carries one certified episode token, so a single registry and a
single commit boundary serve all three classes; only the sensor differs.  That
is what lets one PLANT serve tool, MCP and skill units without substrate-specific
logic: an adapter exposes model-visible values, arms substrate artifacts, issues
decision premises, and reports every boundary as one :class:`CommitEvent`.

Deployment never changes benign task semantics:

* ``marker`` rewrites one operand that is already present, in place, adding no
  new lexical content;
* ``object`` appends a synthetic element only behind a deterministic
  neutrality witness (see :meth:`Plant._object_witness`);
* ``basis`` and ``artifact`` decoys live entirely outside the model-visible
  payload; a trusted adapter must certify that its artifact is inert unless
  explicitly selected.

PLANT never consults WRAP admission state.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from apex.defender.contract import (AcquireClause, ConditionalClause,
                                   DeriveClause, EffectClause, is_clause_ref)
from apex.defender.state import digest

# Decoy planes.
OBSERVATION = "observation"
DEPENDENCY = "dependency"
SUBSTRATE = "substrate"

# Commit boundaries.  The boundary an adapter declares *is* the evidence
# semantics: an identity that reaches an effect boundary was acted on, while an
# identity in the final response was copied into an externally released sink.
CALL = "call"
RESPONSE = "response"
STATE = "state"
ARTIFACT = "artifact"
_EFFECT_BOUNDARIES = frozenset({CALL, STATE, ARTIFACT})

# Evidence semantics.  ``copied`` is distinct from an executed effect, but the
# final response is still an external sink: once a certified marker reaches it,
# that response must not be released.
EFFECTFUL = "effectful"   # observation identity reached a real effect boundary
COPIED = "copied"         # observation identity propagated into a report
PRESENTED = "presented-as-proof"
INVOKED = "invoked"       # substrate artifact was really interacted with

# Dependency presentation is detection evidence, never an authorization gate.
_GATING_SEMANTICS = frozenset({EFFECTFUL, COPIED, INVOKED})

# An ordering consumer admits a deterministic strict loser. Other consumers do
# not provide an append-neutral witness and therefore cannot receive an object.
_ORDERING_OPERATORS = frozenset({"argmax", "argmin"})


def _leaves(value, path=""):
    """Yield ``(path, value)`` for every scalar leaf of a JSON-shaped value."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _leaves(child, f"{path}/{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _leaves(child, f"{path}/{index}")
    else:
        yield path, value


def _object_lists(value, path=()):
    """Yield every non-empty JSON list whose elements are objects.

    Paths are tuples of dict keys / list indices and therefore remain exact
    even when several same-shaped collections occur in one observation.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _object_lists(child, (*path, key))
    elif isinstance(value, (list, tuple)):
        if value and all(isinstance(item, dict) for item in value):
            yield path, list(value)
        for index, child in enumerate(value):
            yield from _object_lists(child, (*path, index))


def _replace_path(value, path, replacement):
    """Return a JSON-shaped copy with one exact subtree replaced."""
    if not path:
        return replacement
    head, *tail = path
    if isinstance(value, dict):
        result = dict(value)
        result[head] = _replace_path(value[head], tuple(tail), replacement)
        return result
    if isinstance(value, (list, tuple)):
        result = list(value)
        result[head] = _replace_path(value[head], tuple(tail), replacement)
        return result
    raise ValueError("PLANT path does not identify a JSON container")


def _contains_token(value, token: str) -> bool:
    return any(isinstance(leaf, str) and token in leaf
               for _path, leaf in _leaves(value))


def _replace_occurrences(value, operand: str, replacement: str):
    """Mark every exact occurrence of one operand in a JSON-shaped value.

    A narrative may name the same handle repeatedly — twice in one prose block,
    or once in each of several records.  That is one semantic placement with
    several physical spans, and *all* of them must carry the marker: if any
    mention stayed unmarked, an adversary could copy that one and the
    commitment sensor would never see the token.  A long skill narrative or MCP
    resource is a single string leaf, so per-leaf repetition is the common case,
    not an ambiguity.
    """
    paths = [path for path, leaf in _leaves(value)
             if isinstance(leaf, str) and operand in leaf]
    if not paths:
        return value, ()

    def visit(node):
        if isinstance(node, dict):
            return {key: visit(child) for key, child in node.items()}
        if isinstance(node, (list, tuple)):
            return [visit(child) for child in node]
        if isinstance(node, str) and operand in node:
            return node.replace(operand, replacement)
        return node
    return visit(value), tuple(paths)


def _pointer_matches(pattern: str, path: str) -> bool:
    """Match one JSON pointer, with ``*`` denoting exactly one segment."""
    expected = str(pattern).split("/")[1:]
    actual = str(path).split("/")[1:]
    return (len(expected) == len(actual) and
            all(left == "*" or left == right
                for left, right in zip(expected, actual)))


def _literal_values(contract):
    for clause in contract.clauses:
        for spec in getattr(clause, "arguments", {}).values():
            if isinstance(spec, dict) and set(spec) == {"literal"}:
                yield spec["literal"]


@dataclass(frozen=True)
class Decoy:
    """One certified decoy, the plane it instruments, and how it is recognized.

    ``identities`` are the exact strings that *mean* this decoy — an object
    handle, a resource id, a basis receipt id.  They default to the token, which
    is what a marker embeds in prose, but an adapter may register a decoy whose
    handle is not the token itself, so that selecting the object is detected even
    when no token text is copied.

    """
    token: str
    plane: str           # OBSERVATION | DEPENDENCY | SUBSTRATE
    mechanism: str       # marker | object | basis | artifact
    source: str
    element: object = None
    identities: tuple[str, ...] = ()

    def recognizes(self, identities, payload) -> bool:
        """Exact identity match, or token propagation inside copied content."""
        keys = self.identities or (self.token,)
        if any(key in identities for key in keys):
            return True
        return _contains_token(payload, self.token)

    def recognizes_identity(self, identities) -> bool:
        """Match a resource selected explicitly by the trusted adapter."""
        keys = self.identities or (self.token,)
        return any(key in identities for key in keys)


@dataclass(frozen=True)
class CommitEvent:
    """One observable boundary, reported identically by every adapter.

    ``payload`` is the native JSON-shaped content the boundary exposes (call
    arguments, response text, a state diff).  ``identities`` are exact handles
    the actor selected or referenced there, for decoys that are recognized by
    identity rather than by copied text.  ``proof_refs`` are the decision
    premises the actor presents as authorization proof.
    """
    channel: str
    actor: str = ""
    payload: object = None
    proof_refs: tuple[str, ...] = field(default_factory=tuple)
    identities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def audit_channel(self) -> str:
        return f"{self.channel}:{self.actor}" if self.actor else self.channel

    @property
    def exact_identities(self) -> frozenset[str]:
        return frozenset(map(str, (*self.identities, self.actor)))


@dataclass(frozen=True)
class Commitment:
    """Evidence that one decoy was adopted, with what semantics, and how strong."""
    token: str
    plane: str
    mechanism: str
    source: str
    boundary: str
    semantics: str       # effectful | copied | presented-as-proof | invoked
    gating: bool         # witnesses an action, so it may block

    def to_dict(self) -> dict:
        return {"token": self.token, "plane": self.plane,
                "mechanism": self.mechanism, "source": self.source,
                "boundary": self.boundary, "semantics": self.semantics,
                "gating": self.gating}


class Plant:
    """Deploy the three decoy planes and detect their adoption.

    ``nonce`` seeds every token, so decoys are unforgeable inside an episode and
    reproducible across a replay of the same episode; pass a fixed per-episode
    value, never a wall-clock or random source.
    """

    def __init__(self, contract, nonce: str, *, placement_agent=None,
                 cache: dict | None = None, surfaces=None):
        self.contract = contract
        self.nonce = str(nonce)
        self.placement_agent = placement_agent
        self.cache = cache if cache is not None else {}
        self.surfaces = dict(surfaces or {})
        self._origin_map = self._origins(contract)
        self._effect_sinks = self._reachable_effect_sinks(contract)
        self._must_take = self._must_take_sources(contract)
        # Which Effect arguments each acquisition can reach.  The placement role
        # needs this to prefer an operand a contracted effect will actually
        # consume; without it a nomination drifts to whatever merely *looks*
        # like an extra handle, such as a documentation line or a stale path.
        self._contract_digest = digest(contract.to_dict())
        self.deployed: dict[str, Decoy] = {}
        self.placements: list[dict] = []
        self.exposed = 0
        self.ineligible = 0
        self.unsupported_shape = 0
        self.structured_candidates = 0
        self.structured_abstained = 0
        self.placement_calls = 0
        self.cache_hits = 0
        self.abstained = 0
        self.invalid_proposals = 0
        self.proposed_placements = 0
        self.accepted_markers = 0
        self.identity_abstained = 0

    # -- contract dataflow --------------------------------------------------
    @staticmethod
    def _origins(contract) -> dict[str, set[str]]:
        """Map each clause output ref to the capabilities it derives from."""
        origins: dict[str, set[str]] = {}
        for clause in contract.clauses:
            if isinstance(clause, AcquireClause):
                if clause.output_ref:
                    origins[clause.output_ref] = {clause.capability}
            elif isinstance(clause, (DeriveClause, ConditionalClause)):
                if clause.output_ref:
                    origins[clause.output_ref] = {
                        capability
                        for source in clause.sources if is_clause_ref(source)
                        for capability in origins.get(source, ())}
        return origins

    @classmethod
    def _reachable_effect_sinks(cls, contract) -> dict[str, tuple[dict, ...]]:
        """Map each acquisition capability to the Effect arguments it can reach.

        A structural dataflow fact, not an authorization judgment: it tells the
        placement role which authority positions this observation influences.
        """
        origins = cls._origins(contract)
        sinks: dict[str, list[dict]] = {}
        for clause in contract.clauses:
            if not isinstance(clause, EffectClause):
                continue
            for argument, spec in clause.effect_arguments.items():
                refs = spec.get("from") if isinstance(spec, dict) else None
                refs = (refs,) if isinstance(refs, str) else tuple(refs or ())
                for capability in {origin for ref in refs
                                   for origin in origins.get(ref, ())}:
                    sinks.setdefault(capability, []).append({
                        "clause": clause.id, "action": clause.action,
                        "argument": str(argument)})
        return {capability: tuple(rows) for capability, rows in sinks.items()}

    @classmethod
    def _must_take_sources(cls, contract) -> frozenset[str]:
        """Capabilities the task forces the agent to process.

        A source is must-take when a downstream Effect consumes it: the agent
        cannot finish the task without handling that material, so a neutral
        object decoy inside it cannot be excluded a priori.
        """
        return frozenset(cls._reachable_effect_sinks(contract))

    def _consumers(self, capability: str):
        """Clauses that consume this capability's acquired output."""
        rows = []
        for clause in self.contract.clauses:
            refs = [source for source in clause.sources
                    if is_clause_ref(source)]
            if any(capability in self._origin_map.get(ref, ())
                   for ref in refs):
                rows.append(clause)
        return rows

    def _direct_outputs(self, capability: str) -> frozenset[str]:
        return frozenset(
            clause.output_ref for clause in self.contract.clauses
            if isinstance(clause, AcquireClause) and
            clause.capability == capability and clause.output_ref)

    # -- tokens -------------------------------------------------------------
    def _token(self, source: str, sample: object) -> str:
        material = hashlib.sha256(
            (self.nonce + "\0" + str(source) + "\0" + repr(sample)).encode()
        ).hexdigest()[:16]
        return f"CERT-{material}"

    def _record(self, plane: str, mechanism: str, source: str, sample,
                element=None, *, identities=(), **detail) -> Decoy:
        token = self._token(source, sample)
        decoy = self.deployed.get(token)
        if decoy is None:
            decoy = Decoy(token, plane, mechanism, str(source),
                          element if element is not None else sample,
                          tuple(dict.fromkeys(str(item) for item in identities)))
            self.deployed[token] = decoy
            self.placements.append({
                "plane": plane, "mechanism": mechanism, "source": str(source),
                "token": token, **detail})
        return decoy

    def _carrier(self, source: str) -> str:
        surface = self.surfaces.get(str(source))
        return str(getattr(surface, "carrier", OBSERVATION))

    # -- class 1: observation decoys ---------------------------------------
    def _trusted_operand(self, operand: str) -> bool:
        if operand in self.contract.task:
            return True
        return any(_contains_token(value, operand)
                   for value in _literal_values(self.contract))

    @staticmethod
    def _safe_template(operand: str, template: str) -> bool:
        """Permit atom marking or complete removal of one exact control span.

        Atomic handles preserve their lexical/punctuation skeleton.  A bare
        marker is the one exception: it replaces the complete nominated span,
        so an extra free-text control relation cannot survive by paraphrasing
        an entity inside it.  In both cases the placement role may only select
        text already present in the carrier; code owns the token and introduces
        no executable or semantic content.
        """
        if (not isinstance(template, str) or
                template.count("{MARKER}") != 1 or
                len(template) > 2 * len(operand) + 80):
            return False
        if template == "{MARKER}":
            return True
        allowed = set(re.findall(r"[A-Za-z0-9]+", operand.lower()))
        residual = template.replace("{MARKER}", "")
        introduced = set(re.findall(r"[A-Za-z0-9]+", residual.lower()))
        punctuation = lambda text: tuple(
            char for char in text
            if not char.isalnum() and not char.isspace())
        return (introduced <= allowed and
                punctuation(residual) == punctuation(operand))

    def _validated_marker(self, source: str, value, proposal):
        """Return one validated marker tuple, or ``None``.

        The agent only nominates an operand already present in ``value``; code
        owns every security check and the token itself.
        """
        if not isinstance(proposal, dict) or proposal.get("kind") != "marker":
            return None
        operand = proposal.get("operand")
        template = proposal.get("replacement_template")
        if (not isinstance(operand, str) or not operand or
                self._trusted_operand(operand) or
                not self._safe_template(operand, template)):
            self.invalid_proposals += 1
            return None
        token = self._token(source, operand)
        replacement = template.replace("{MARKER}", token)
        decorated, paths = _replace_occurrences(value, operand, replacement)
        if not paths:
            self.invalid_proposals += 1
            return None
        surface = self.surfaces.get(str(source))
        identity_paths = tuple(
            getattr(surface, "identity_paths", ()) or ())
        if any(_pointer_matches(pattern, path)
               for pattern in identity_paths for path in paths):
            # A marker inside an opaque handle changes the value accepted by
            # the native boundary. Preserve the complete identity and abstain;
            # WRAP still mediates any Effect induced by its untrusted prose.
            self.identity_abstained += 1
            return None
        return operand, replacement, paths, proposal

    def apply_markers(self, source: str, value, proposals):
        """Apply every valid non-overlapping marker nominated in one batch.

        Validation is against the original carrier.  Complete handles win over
        their substrings, so a proposal containing both a URL and its hostname
        cannot produce nested rewrites or leave the more specific handle bare.
        Invalid candidates are simply not deployed; they never weaken any
        independently valid placement from the same typed proposal.
        """
        candidates = []
        for proposal in proposals or ():
            validated = self._validated_marker(str(source), value, proposal)
            if validated is not None:
                candidates.append(validated)
        candidates.sort(key=lambda row: len(row[0]), reverse=True)
        accepted = []
        for candidate in candidates:
            operand = candidate[0]
            if any(operand in chosen[0] or chosen[0] in operand
                   for chosen in accepted):
                self.invalid_proposals += 1
                continue
            accepted.append(candidate)

        decorated = value
        for operand, replacement, paths, proposal in accepted:
            decorated, _ = _replace_occurrences(
                decorated, operand, replacement)
            self._record(OBSERVATION, "marker", source, operand,
                         operand=operand[:160], occurrences=list(paths),
                         replacement=replacement,
                         carrier=self._carrier(source),
                         reason=str(proposal.get("reason", ""))[:240])
        self.accepted_markers += len(accepted)
        return decorated, len(accepted)

    def _projected_paths(self, source: str) -> dict[str, tuple]:
        """Map closed field projections of one Acquire output to JSON paths."""
        paths = {ref: () for ref in self._direct_outputs(source)}
        for clause in self.contract.clauses:
            if (not isinstance(clause, ConditionalClause) or
                    clause.operator != "field" or
                    len(clause.operands) != 2 or
                    clause.operands[0] not in paths or
                    not isinstance(clause.operands[1], dict) or
                    set(clause.operands[1]) != {"literal"}):
                continue
            key = clause.operands[1]["literal"]
            if not isinstance(key, (str, int)) or not clause.output_ref:
                continue
            paths[clause.output_ref] = (*paths[clause.operands[0]], key)
        return paths

    def _object_witness(self, source: str, observation, path=()):
        """Return a benign-inert synthetic element, or ``None`` to abstain.

        Appending an element is admissible only with a deterministic witness
        that no benign consumer observes the difference:

        1. the source is must-take, so the decoy cannot be excluded a priori;
        2. every consumer before the Effect is the same closed ``argmin`` or
           ``argmax`` ordering; semantic Derive, downstream Acquire and every
           other operator abstain because they provide no neutrality proof;
        3. no Effect argument binds the acquired set *wholesale*, which would
           emit the decoy on the benign path;
        4. every ordering consumer draws all of its operands from this same
           source, so appending keeps them aligned, and one strict-loser
           direction satisfies all of them.

        The element is then built to lose: string leaves become the certified
        token and numeric leaves take a strict extremum computed from the
        observed elements. No semantic Derive is assumed to ignore it.
        """
        if source not in self._must_take:
            return None
        if not isinstance(observation, list) or not observation:
            return None
        if not all(isinstance(item, dict) for item in observation):
            return None
        projected = self._projected_paths(source)
        # Modifying a nested collection also modifies each projected ancestor.
        affected = {
            ref for ref, ref_path in projected.items()
            if tuple(path[:len(ref_path)]) == ref_path
        }
        if not affected:
            return None
        directions: set[str] = set()
        for clause in self.contract.clauses:
            refs = {ref for ref in clause.sources if is_clause_ref(ref)}
            if not refs.intersection(affected):
                continue
            if isinstance(clause, AcquireClause):
                return None
            if isinstance(clause, DeriveClause):
                return None
            if isinstance(clause, ConditionalClause):
                if clause.operator == "field":
                    # A closed projection deterministically carries the same
                    # appended element/field into the next typed collection.
                    if clause.output_ref:
                        affected.add(clause.output_ref)
                    continue
                if clause.operator not in _ORDERING_OPERATORS:
                    return None
                if not clause.operand_refs or any(
                        ref not in affected for ref in clause.operand_refs):
                    return None
                directions.add(clause.operator)
            elif isinstance(clause, EffectClause):
                # An Effect that consumes the collection (or any projection of
                # it) before a strict-loser selection would observe the decoy.
                return None
        if len(directions) > 1:
            return None
        if len(directions) != 1:
            return None
        direction = next(iter(directions))
        return self._synthetic_element(
            source, observation, direction, sample=("object", path,
                                                    digest(observation)))

    def _synthetic_element(self, source: str, observation, direction: str,
                           sample=None):
        """Clone the observed shape with a certified, strictly-losing content."""
        sample = sample or ("object", digest(observation))
        token = self._token(source, sample)
        numbers: dict[str, list] = {}
        for element in observation:
            for path, leaf in _leaves(element):
                if isinstance(leaf, bool) or not isinstance(leaf, (int, float)):
                    continue
                numbers.setdefault(path, []).append(leaf)

        def build(node, path=""):
            if isinstance(node, dict):
                return {key: build(child, f"{path}/{key}")
                        for key, child in node.items()}
            if isinstance(node, (list, tuple)):
                return [build(child, f"{path}/{index}")
                        for index, child in enumerate(node)]
            if isinstance(node, str):
                return token
            if isinstance(node, bool):
                return False
            if isinstance(node, (int, float)):
                seen = numbers.get(path) or [0]
                if direction == "argmax":
                    return min(seen) - 1
                if direction == "argmin":
                    return max(seen) + 1
                return type(node)(0)
            return node
        return build(observation[0])

    def propose(self, source: str, value, *, modes, schema=None,
                surface_cards=()) -> dict:
        """Request one carrier-local placement proposal, cached by full input."""
        modes = tuple(dict.fromkeys(map(str, modes or ())))
        surface_cards = tuple(
            dict(card) for card in (surface_cards or ())
            if isinstance(card, dict))
        has_text = any(isinstance(leaf, str) and leaf
                       for _path, leaf in _leaves(value))
        if self.placement_agent is None or (
                not has_text and not {"basis", "artifact"}.intersection(modes)):
            return {"status": "abstain", "placements": [],
                    "reason": "no placement agent or eligible carrier content"}
        sinks = list(self._effect_sinks.get(str(source), ()))
        key = (self._contract_digest, str(source), digest(value), modes,
               digest(schema), digest(sinks), digest(surface_cards))
        def compute():
            self.placement_calls += 1
            try:
                return self.placement_agent(
                    contract=self.contract.to_dict(), source=str(source),
                    value=value, modes=modes, schema=schema,
                    reachable_sinks=sinks, surface_cards=surface_cards)
            except Exception as exc:  # semantic role failure is fail-safe abstain
                self.invalid_proposals += 1
                return {
                    "status": "abstain", "placements": [],
                    "reason": "placement agent error: " + type(exc).__name__ + ":" + str(exc)[:160],
                }

        get_or_compute = getattr(self.cache, "get_or_compute", None)
        if callable(get_or_compute):
            proposal, reused = get_or_compute(key, compute)
            if reused:
                self.cache_hits += 1
        elif key in self.cache:
            self.cache_hits += 1
            proposal = self.cache[key]
        else:
            proposal = compute()
            self.cache[key] = proposal
        if (not isinstance(proposal, dict) or
                set(proposal) != {"status", "placements", "reason"} or
                not isinstance(proposal.get("placements"), list)):
            self.invalid_proposals += 1
            return {"status": "abstain", "placements": [],
                    "reason": "invalid placement proposal"}
        artifact_ids = {
            str(card.get("id", "")) for card in surface_cards
            if "artifact" in tuple(map(str, card.get("modes") or ()))
        }
        if artifact_ids and any(
                item.get("kind") == "artifact" and
                item.get("operand") not in artifact_ids
                for item in proposal["placements"] if isinstance(item, dict)):
            self.invalid_proposals += 1
            return {"status": "abstain", "placements": [],
                    "reason": "artifact selected an unregistered SurfaceCard"}
        self.proposed_placements += len(proposal["placements"])
        if proposal.get("status") == "abstain":
            self.abstained += 1
        return proposal

    def decorate(self, source: str, value):
        """Instrument one model-visible value: marker first, then object.

        Every value an agent can read should pass through here — tool and MCP
        results, resource contents, and skill narrative alike.
        """
        source = str(source)
        self.exposed += 1
        proposal = self.propose(source, value, modes=("marker",))
        placements = proposal.get("placements", ())
        decorated, _marker_count = self.apply_markers(
            source, value,
            (item for item in placements if item.get("kind") == "marker"))
        if source not in self._must_take:
            self.ineligible += 1
            return decorated
        candidates = list(_object_lists(value))
        if not candidates:
            self.unsupported_shape += 1
            return decorated
        self.structured_candidates += len(candidates)
        for path, collection in candidates:
            element = self._object_witness(source, collection, path)
            if element is None:
                self.structured_abstained += 1
                continue
            sample = ("object", path, digest(collection))
            self._record(OBSERVATION, "object", source, sample, element,
                         collection_path=list(path))
            current = decorated
            for part in path:
                current = current[part]
            decorated = _replace_path(
                decorated, path, list(current) + [element])
        return decorated

    # -- plane 2: dependency decoys ----------------------------------------
    def arm_basis(self, source: str, sample) -> Decoy:
        """Register one synthetic decision premise, outside the native payload.

        The runtime issues the returned token as a basis identifier; a
        presentation is detected only when the handle is submitted as proof.
        Reading the premise is recorded by Runtime and never gates.
        """
        return self._record(DEPENDENCY, "basis", str(source), sample)

    # -- class 3: substrate decoys -----------------------------------------
    def arm_substrate(self, source: str, sample) -> Decoy:
        """Arm one registered control-plane or persistent-state carrier.

        The trusted adapter embeds the returned token in its native artifact: a
        honey file or record, a permission entry, a test account, an
        authorization handle, a capability grant, or a decoy tool/resource.
        """
        source = str(source)
        surface = self.surfaces.get(source)
        if surface is None or not getattr(surface, "plantable", False):
            raise ValueError(f"unregistered PLANT carrier {source!r}")
        carrier = self._carrier(source)
        if carrier == OBSERVATION:
            raise ValueError(
                f"observation carrier {source!r} must go through decorate()")
        if carrier not in {"control", "state"}:
            raise ValueError(f"unknown PLANT carrier {carrier!r}")
        return self._record(SUBSTRATE, "artifact", source, sample)

    def place_substrate(self, source: str, value, *, schema=None) -> Decoy | None:
        """Let the placement role select one registered substrate carrier.

        ``value`` is the adapter's task-independent description of the native
        state surface, not content later exposed to the target Agent.  The
        adapter embeds the returned token only after this proposal passes the
        typed validator and the deterministic carrier check in
        :meth:`arm_substrate`.
        """
        source = str(source)
        surface = self.surfaces.get(source)
        carrier = self._carrier(source)
        if (surface is None or not getattr(surface, "plantable", False) or
                carrier not in {"control", "state"}):
            raise ValueError(f"unregistered substrate carrier {source!r}")
        proposal = self.propose(source, value, modes=("artifact",), schema=schema)
        artifact = next((item for item in proposal.get("placements", ())
                         if item.get("kind") == "artifact"), None)
        if artifact is None:
            return None
        return self.arm_substrate(source, value)

    # -- detection ----------------------------------------------------------
    def commit(self, event: CommitEvent) -> list[Commitment]:
        """Run the three sensors over one boundary and classify every adoption.

        Each plane reads its own witness, and the *boundary* fixes the strength:

        * observation — reaching an effect boundary is ``effectful``; appearing
          in the final response is ``copied``.  Both gate because both are
          externally released sinks, while retaining distinct audit semantics;
        * dependency — a decoy handle submitted as proof is ``presented`` and
          recorded, but never gates; WRAP independently checks authority;
        * substrate — an artifact only commits when really interacted with, so
          naming it in a response is not adoption at all.
        """
        refs = frozenset(map(str, event.proof_refs or ()))
        identities = event.exact_identities
        effect_boundary = event.channel in _EFFECT_BOUNDARIES
        found: list[tuple[Decoy, str]] = []
        for decoy in self.deployed.values():
            if decoy.plane == DEPENDENCY:
                if decoy.token in refs:
                    found.append((decoy, PRESENTED))
            elif decoy.plane == SUBSTRATE:
                # Visibility is not invocation.  A broad state snapshot, tool
                # result, or narrative may contain a honey identity without
                # selecting that resource.  Only the trusted adapter's exact
                # identity channel can attest a substrate interaction.
                if effect_boundary and decoy.recognizes_identity(identities):
                    found.append((decoy, INVOKED))
            elif decoy.recognizes(identities, event.payload):
                found.append((decoy, EFFECTFUL if effect_boundary else COPIED))
        return [
            Commitment(
                decoy.token, decoy.plane, decoy.mechanism, decoy.source,
                event.audit_channel, semantics,
                semantics in _GATING_SEMANTICS)
            for decoy, semantics in found]

    def close(self) -> dict:
        audit = {
            "must_take": sorted(self._must_take),
            "exposed": self.exposed,
            "ineligible": self.ineligible,
            "unsupported_shape": self.unsupported_shape,
            "structured_candidates": self.structured_candidates,
            "structured_abstained": self.structured_abstained,
            "placement_calls": self.placement_calls,
            "cache_hits": self.cache_hits,
            "abstained": self.abstained,
            "invalid_proposals": self.invalid_proposals,
            "proposed_placements": self.proposed_placements,
            "accepted_markers": self.accepted_markers,
            "identity_abstained": self.identity_abstained,
            "placements": list(self.placements),
            "deployments": {
                plane: sum(decoy.plane == plane
                           for decoy in self.deployed.values())
                for plane in (OBSERVATION, DEPENDENCY, SUBSTRATE)},
            "deployed": sorted(self.deployed),
            "deployment_count": len(self.deployed),
        }
        self.deployed.clear()
        return audit
