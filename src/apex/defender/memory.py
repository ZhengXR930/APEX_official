"""Persistent environment perception; no task or runtime trace is stored here."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import math
import re
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class SourceSurface:
    id: str
    description: str = ""
    plantable: bool = False
    # Where the decoy is carried.  This affects deployment/audit only; every
    # carrier commits through the same exact-token detector.
    carrier: str = "observation"
    # Operator-attested JSON-pointer patterns whose scalar leaves are native
    # resource identities. ``*`` matches exactly one path segment. In-band
    # PLANT rewriting must never alter these values because equality is part
    # of the benign Tool/MCP/Skill protocol.
    identity_paths: tuple[str, ...] = ()

    def __post_init__(self):
        if self.carrier not in {"observation", "control", "state"}:
            raise ValueError(f"unknown PLANT carrier {self.carrier!r}")
        if any(not isinstance(path, str) or not path.startswith("/")
               for path in self.identity_paths):
            raise ValueError("identity paths must be JSON-pointer patterns")

    @classmethod
    def from_dict(cls, value):
        return cls(str(value.get("id", "")), str(value.get("description", "")),
                   bool(value.get("plantable", False)),
                   str(value.get("carrier", "observation")),
                   tuple(map(str, value.get("identity_paths") or ())))

    @classmethod
    def skill_instructions(cls, skill_name: str):
        """Compile loaded SKILL.md prose into its standard observation slot."""
        name = str(skill_name).strip()
        if not name:
            raise ValueError("Skill instruction carrier requires a Skill name")
        return cls(
            f"skill:{name}:instructions",
            f"Loaded {name} SKILL.md prose visible to the target Agent.",
            True,
            "observation",
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "plantable": self.plantable,
            "carrier": self.carrier,
            "identity_paths": list(self.identity_paths),
        }


RUNTIME_CONTEXT_SOURCE = SourceSurface(
    id="runtime-context",
    description="Operator-attested episode context",
    plantable=False,
)


def _schema_allows_null(schema: dict) -> bool:
    if not isinstance(schema, dict):
        return False
    raw_type = schema.get("type")
    if raw_type == "null":
        return True
    if isinstance(raw_type, list) and "null" in raw_type:
        return True
    return any(
        _schema_allows_null(item)
        for item in (schema.get("anyOf") or schema.get("oneOf") or ())
        if isinstance(item, dict))


def _argument_schema_with_operator_default(
        name: str, schema: dict, required: set[str]) -> dict:
    schema = dict(schema)
    if (name not in required and "default" not in schema and
            _schema_allows_null(schema)):
        # A nullable, non-required tool argument has an operator-defined
        # omitted value of None.  Treating an explicit None the same way keeps
        # patch-style updates from needing semantic authority for fields that
        # the native boundary will not modify.  Any non-None value still goes
        # through the ordinary Contract/Binding proof.
        schema["default"] = None
    return schema


@dataclass(frozen=True)
class CapabilitySurface:
    name: str
    description: str = ""
    arguments: tuple[str, ...] = ()
    effect: bool = False
    observation: bool = False
    # Empty means the registration explicitly declares no required argument.
    required_arguments: tuple[str, ...] = ()
    # Operator-attested semantic type for arguments whose JSON Schema alone is
    # insufficient.  Types choose parsing/comparison behavior; they never
    # grant authority.  Omitted arguments are opaque.
    argument_types: tuple[tuple[str, str], ...] = ()
    # Optional JSON-pointer-to-type annotations for structured returns.
    output_types: tuple[tuple[str, str], ...] = ()
    # Operator/substrate-attested JSON Schema for the canonical tool return.
    # None means the boundary did not publish an output contract.
    output_schema: dict | None = None
    # Operator/substrate-attested schema per call argument.  This is retained
    # only for deterministic shape/format handling, never semantic authority.
    argument_schemas: tuple[tuple[str, dict], ...] = ()
    # True only when the operator attests that this observation is produced
    # by committing the same effectful call (rather than by a read boundary).
    effect_return: bool = False
    # Runtime-issued receipts inherit this task-independent role.  Data and
    # advisory receipts never grant authority.  A control receipt can do so
    # only with a separately verified exact Effect scope.
    receipt_role: str = "data"

    @classmethod
    def from_dict(cls, value):
        effect = bool(value.get("effect", False))
        arguments = value.get("arguments") or value.get("params")
        required = value.get("required_arguments")
        output_schema = (value.get("output_schema")
                         if "output_schema" in value else value.get("outputSchema"))
        argument_schemas = value.get("argument_schemas")
        if argument_schemas is None:
            input_schema = value.get("inputSchema")
            input_schema = input_schema if isinstance(input_schema, dict) else {}
            argument_schemas = input_schema.get("properties") or {}
        if not isinstance(argument_schemas, dict):
            raise TypeError(
                f"capability {value.get('name', '')!r} argument schemas must be an object")
        if output_schema is not None and not isinstance(output_schema, dict):
            raise TypeError(
                f"capability {value.get('name', '')!r} output schema must be an object")
        schema = value.get("inputSchema")
        schema = schema if isinstance(schema, dict) else {}
        if not arguments:
            properties = schema.get("properties")
            arguments = list(properties) if isinstance(properties, dict) else []
        if required is None and "required" in schema:
            required = schema.get("required") or []
        if required is None:
            raise ValueError(
                f"capability {value.get('name', '')!r} lacks required arguments")
        required_set = set(map(str, required or ()))
        argument_types = value.get("argument_types") or {}
        output_types = value.get("output_types") or {}
        if not isinstance(argument_types, dict):
            raise TypeError(
                f"capability {value.get('name', '')!r} argument types must be an object")
        if not isinstance(output_types, dict):
            raise TypeError(
                f"capability {value.get('name', '')!r} output types must be an object")
        argument_names = set(map(str, arguments or ()))
        argument_types = tuple(
            (str(name), _validate_content_type(kind))
            for name, kind in argument_types.items()
            if str(name) in argument_names)
        output_types = tuple(
            (str(pointer), _validate_content_type(kind))
            for pointer, kind in output_types.items())
        receipt_role = str(value.get("receipt_role", "data"))
        if receipt_role not in {"data", "advisory", "control"}:
            raise ValueError(
                f"capability {value.get('name', '')!r} has unknown receipt role "
                f"{receipt_role!r}")
        return cls(str(value.get("name", "")), str(value.get("description", "")),
                   tuple(map(str, arguments or [])),
                   effect, bool(value.get("observation", not effect)),
                   tuple(map(str, required)),
                   argument_types,
                   output_types,
                   None if output_schema is None else dict(output_schema),
                   tuple((str(name), _argument_schema_with_operator_default(
                             str(name), schema, required_set))
                         for name, schema in argument_schemas.items()
                         if str(name) in set(map(str, arguments or ())) and
                         isinstance(schema, dict)),
                   bool(value.get("effect_return", False)),
                   receipt_role)

    @property
    def required(self) -> tuple[str, ...]:
        return self.required_arguments

    @property
    def committed_return(self) -> bool:
        """Whether a passed call has an operator-attested canonical return.

        A dual outbound/inbound capability with a published output schema is
        already an attestation that the effect call returns this observation.
        Other substrates must explicitly set ``effect_return``.
        """
        return bool(self.effect_return or (
            self.effect and self.observation and self.output_schema is not None))

    @property
    def operator_class(self) -> str:
        """Task-independent mediation class attested by the operator.

        ``observation`` never grants downstream authority.  ``effect`` and
        ``effect-return`` cross the outbound Effect boundary and are gated by
        WRAP; the latter additionally emits a Receipt after execution.
        """
        if self.effect and self.observation:
            return "effect-return"
        if self.effect:
            return "effect"
        if self.observation:
            return "observation"
        return "internal"

    @property
    def requires_authority_proof(self) -> bool:
        """Whether this Effect grants control authority rather than doing work.

        This is an operator fact: a positive control Effect must cite a fresh,
        exact-scope control receipt.  Data/advisory receipts and semantic model
        judgments can never satisfy it.
        """
        return bool(self.effect and self.receipt_role == "control")

    def argument_type(self, argument: str) -> str:
        for name, kind in self.argument_types:
            if name == str(argument):
                return kind
        schema = self.argument_schema(argument)
        format_name = _schema_format(schema)
        return {"uri": "url", "url": "url", "email": "email"}.get(
            format_name, "opaque")

    @property
    def identity_paths(self) -> tuple[str, ...]:
        """Return operator-declared scalar identity positions in the output."""
        return tuple(pointer for pointer, kind in self.output_types
                     if kind == "identity")

    def accepts_semantic_support(self, argument: str) -> bool:
        kind = self.argument_type(argument)
        return (kind in {"natural_language", "code", "path"} or
                kind.startswith("code/"))

    def authority_grammars(self, argument: str) -> tuple[str, ...]:
        # Natural-language endpoints can launder authority.  Machine text is
        # handled at its eventual execution boundary, not by prose regexes.
        return (("url", "email", "mention")
                if self.argument_type(argument) == "natural_language" else ())

    def argument_schema(self, argument: str):
        for name, schema in self.argument_schemas:
            if name == str(argument):
                return schema
        return None


def _validate_content_type(value) -> str:
    kind = str(value)
    if kind in {"natural_language", "code", "path", "opaque", "url", "email",
                "identity"}:
        return kind
    if kind.startswith("code/") and len(kind) > len("code/"):
        return kind
    raise ValueError(f"unknown capability content type {kind!r}")


@dataclass(frozen=True)
class SkillSurface:
    """Operator-registered Skill boundary above its member Tools.

    This is deliberately smaller than a workflow policy.  It records the
    Skill's trusted purpose, Tool membership, and package-level invariants; it
    does not predict a task trace or authorize a member Tool by itself.
    """
    name: str
    description: str = ""
    tools: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value):
        return cls(
            str(value.get("name", value.get("id", ""))),
            str(value.get("description", "")),
            tuple(dict.fromkeys(map(str, value.get("tools") or ()))),
            tuple(dict.fromkeys(map(str, value.get("constraints") or ()))),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "constraints": list(self.constraints),
        }


def _schema_format(schema):
    if not isinstance(schema, dict):
        return None
    direct = schema.get("format")
    if isinstance(direct, str):
        return direct
    formats = {item for branch in (schema.get("anyOf") or schema.get("oneOf") or ())
               if (item := _schema_format(branch)) is not None}
    return next(iter(formats)) if len(formats) == 1 else None


def _canonical_temporal(value, format_name):
    if not isinstance(value, str):
        return value
    text = value.strip()
    try:
        if format_name == "date":
            return date.fromisoformat(text).isoformat()
        if format_name != "date-time":
            return value
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return value


def _canonical_url(value):
    if not isinstance(value, str) or not value.strip():
        return value
    text = value.strip()
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urlsplit(text)
        if not parsed.hostname:
            return value
        host = parsed.hostname.lower()
        port = parsed.port
    except ValueError:
        return value
    if port is not None and not (
            parsed.scheme.lower() == "https" and port == 443):
        host += ":" + str(port)
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), host, path,
                       parsed.query, parsed.fragment))


def _completes_local_datetime(expected, proposed) -> bool:
    """Complete an operator-declared date into its required local datetime."""
    if not isinstance(expected, str) or not isinstance(proposed, str):
        return False
    try:
        bounded_date = date.fromisoformat(expected.strip())
        completed = datetime.strptime(proposed.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    return completed.date() == bounded_date


_INTEGER_SCALAR = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_NUMBER_SCALAR = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")


def _enum_allows(schema: dict, value) -> bool:
    """Keep a coercion only when it also satisfies const/enum guards."""
    if "const" in schema and (
            type(value) is not type(schema["const"]) or
            value != schema["const"]):
        return False
    enum = schema.get("enum")
    return not isinstance(enum, list) or any(
        type(value) is type(item) and value == item for item in enum)


def canonical_schema_scalar(schema, value):
    """Return one lossless scalar representation selected by trusted schema.

    Containers, null, exponent/leading-zero spellings and undeclared enum
    aliases remain unchanged.  Callers therefore fail closed when the
    operator schema cannot prove a unique representation.
    """
    if (not isinstance(schema, dict) or value is None or
            isinstance(value, (dict, list)) or
            schema.get("x-scalar-coercion") is False):
        return value
    kind = schema.get("type")
    candidate = value
    if kind == "integer":
        if isinstance(value, str) and _INTEGER_SCALAR.fullmatch(value):
            candidate = int(value)
        elif (isinstance(value, float) and math.isfinite(value) and
              value.is_integer()):
            candidate = int(value)
    elif kind == "number":
        if isinstance(value, str) and _NUMBER_SCALAR.fullmatch(value):
            candidate = (int(value) if "." not in value else float(value))
            if isinstance(candidate, float) and not math.isfinite(candidate):
                return value
    elif kind == "boolean" and isinstance(value, str):
        if value == "true":
            candidate = True
        elif value == "false":
            candidate = False
    elif kind == "string":
        if isinstance(value, bool):
            candidate = "true" if value else "false"
        elif isinstance(value, int) and not isinstance(value, bool):
            candidate = str(value)
        elif isinstance(value, float) and math.isfinite(value):
            candidate = repr(value)
    return candidate if _enum_allows(schema, candidate) else value


def canonical_argument_value(surface, argument: str, value):
    """Return the trusted-schema canonical value used for stable identity.

    This is narrower than semantic equality: only lossless scalar coercions
    and an operator-declared URL canonicalization may change the value. Code,
    path, and identity roles remain byte-exact.
    """
    schema = surface.argument_schema(argument) if surface is not None else None
    kind = surface.argument_type(argument) if surface is not None else ""
    if (isinstance(schema, dict) and
            (kind in {"code", "path", "identity"} or
             str(kind).startswith("code/"))):
        schema = dict(schema)
        schema["x-scalar-coercion"] = False
    canonical = canonical_schema_scalar(schema, value)
    if (isinstance(schema, dict) and
            schema.get("x-canonicalization") == "url-default-https"):
        return _canonical_url(canonical)
    return canonical


def schema_values_equal(schema, left, right) -> bool:
    """Compare only equivalences explicitly enabled by an argument schema."""
    if type(left) is type(right) and left == right:
        return True
    if isinstance(schema, dict):
        for branch in (schema.get("anyOf") or schema.get("oneOf") or ()):
            if isinstance(branch, dict) and schema_values_equal(
                    branch, left, right):
                return True
    canonical_left = canonical_schema_scalar(schema, left)
    canonical_right = canonical_schema_scalar(schema, right)
    if (type(canonical_left) is type(canonical_right) and
            canonical_left == canonical_right):
        return True
    if (isinstance(schema, dict) and schema.get("type") == "number" and
            isinstance(canonical_left, (int, float)) and
            not isinstance(canonical_left, bool) and
            isinstance(canonical_right, (int, float)) and
            not isinstance(canonical_right, bool) and
            canonical_left == canonical_right):
        return True
    if (isinstance(schema, dict) and
            schema.get("x-canonicalization") == "url-default-https"):
        return _canonical_url(left) == _canonical_url(right)
    if (isinstance(schema, dict) and schema.get("x-completion") ==
            "date-to-local-datetime"):
        return _completes_local_datetime(left, right)
    format_name = _schema_format(schema)
    if format_name not in {"date", "date-time"}:
        return False
    return _canonical_temporal(left, format_name) == _canonical_temporal(right, format_name)


def argument_values_equal(surface, argument: str, left, right) -> bool:
    schema = surface.argument_schema(argument) if surface is not None else None
    kind = surface.argument_type(argument) if surface is not None else ""
    if (isinstance(schema, dict) and
            (kind in {"code", "path", "identity"} or
             str(kind).startswith("code/"))):
        schema = dict(schema)
        schema["x-scalar-coercion"] = False
    if schema_values_equal(schema, left, right):
        return True
    # The operator type is the trusted boundary for this relaxation.  Case is
    # not semantically meaningful in natural-language roles (for example
    # ``US`` versus ``us``), while paths, code, credentials and opaque IDs
    # must remain byte-for-byte exact.
    return bool(
        surface is not None and
        kind == "natural_language" and
        isinstance(left, str) and isinstance(right, str) and
        left.casefold() == right.casefold())


@dataclass
class EnvironmentPlan:
    id: str = ""
    sources: dict[str, SourceSurface] = field(default_factory=dict)
    capabilities: dict[str, CapabilitySurface] = field(default_factory=dict)
    skills: dict[str, SkillSurface] = field(default_factory=dict)

    def to_dict(self):
        return {"id": self.id,
                "sources": {key: vars(value) for key, value in self.sources.items()},
                "capabilities": {key: {**vars(value),
                                      "arguments": list(value.arguments),
                                      "argument_types": dict(value.argument_types),
                                      "output_types": dict(value.output_types),
                                      "required_arguments": list(
                                          value.required_arguments),
                                      "output_schema": value.output_schema,
                                      "argument_schemas": {
                                          name: schema for name, schema
                                          in value.argument_schemas
                                      }}
                            for key, value in self.capabilities.items()},
                "skills": {key: value.to_dict()
                           for key, value in self.skills.items()}}

    @classmethod
    def from_dict(cls, value):
        return cls(str(value.get("id", "")),
                   {key: SourceSurface.from_dict(item) for key, item in (value.get("sources") or {}).items()},
                   {key: CapabilitySurface.from_dict(item) for key, item in
                    (value.get("capabilities") or {}).items()},
                   {key: SkillSurface.from_dict(item) for key, item in
                    (value.get("skills") or {}).items()})
