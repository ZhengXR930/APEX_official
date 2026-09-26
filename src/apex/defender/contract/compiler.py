"""Deterministic compiler for the explicit four-Clause TaskContract DSL."""
from __future__ import annotations

from .model import is_clause_ref


OPERATOR_ARITY = {
    "identity": 1, "singleton": 1, "count": 1, "map_count": 1,
    "union": 1, "flatten": 1, "keys": 1, "frequency": 1,
    "difference": 2, "argmin": 2, "argmax": 2, "coalesce": 2,
    "aligned_lookup": 3, "object_set": 3, "sort_by": 3,
    "basename": 1, "path_join": 2, "gt": 3, "lt": 3,
    "field": 2, "project": 2, "select_eq": 3,
    "add": 2, "multiply": 2,
    "percent_of": 2,
    "datetime_combine": 2, "add_duration": 2, "interval_free": 3,
}


_EXACT_SCHEMA_TYPES = frozenset({
    "boolean", "integer", "number", "array", "object",
})

_IDENTITY_ARGUMENT_KINDS = frozenset({
    "identity", "url", "email", "path",
})

_IDENTITY_ARGUMENT_NAMES = frozenset({
    "account", "account_id", "channel", "channel_id", "destination",
    "destination_id", "dest", "endpoint", "file_id", "path", "recipient",
    "resource", "resource_id", "target", "target_id", "to", "uri", "url",
})

_CLOSING_OPERATORS = frozenset({
    "argmin", "argmax", "coalesce", "gt", "interval_free", "lt",
    "select_eq",
})


def _exact_schema(schema) -> bool:
    """Return whether an argument needs a code-owned exact value.

    A task-root semantic Derive is meaningful for prose-like strings, but it
    cannot materialize a boolean, number, array, object, or closed enum. Such
    values need a typed Contract literal or deterministic Conditional before
    WRAP can prove the proposed argument.
    """
    return (isinstance(schema, dict) and
            schema.get("x-task-derived") is not True and
            (schema.get("type") in _EXACT_SCHEMA_TYPES or
             isinstance(schema.get("enum"), list)))


def _schema_value_matches(schema, value) -> bool:
    """Validate the JSON type/shape of one Contract literal."""
    if not isinstance(schema, dict) or not schema:
        return True
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return False
    kind = schema.get("type")
    if isinstance(kind, list):
        return any(_schema_value_matches({**schema, "type": item}, value)
                   for item in kind)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return (isinstance(value, int) and not isinstance(value, bool) and
                ("minimum" not in schema or value >= schema["minimum"]) and
                ("maximum" not in schema or value <= schema["maximum"]))
    if kind == "number":
        return (isinstance(value, (int, float)) and
                not isinstance(value, bool) and
                ("minimum" not in schema or value >= schema["minimum"]) and
                ("maximum" not in schema or value <= schema["maximum"]))
    if kind == "string":
        return isinstance(value, str)
    if kind == "array":
        return (isinstance(value, list) and
                ("minItems" not in schema or
                 len(value) >= schema["minItems"]) and
                ("maxItems" not in schema or
                 len(value) <= schema["maxItems"]) and
                all(_schema_value_matches(schema.get("items", {}), item)
                    for item in value))
    if kind == "object":
        if not isinstance(value, dict):
            return False
        properties = schema.get("properties")
        required = schema.get("required")
        if isinstance(required, list) and not set(required).issubset(value):
            return False
        if isinstance(properties, dict):
            if (schema.get("additionalProperties") is False and
                    not set(value).issubset(properties)):
                return False
            return all(
                name not in properties or
                _schema_value_matches(properties[name], child)
                for name, child in value.items())
        return True
    if kind == "null":
        return value is None
    return True


def _schema_format(schema) -> str:
    if not isinstance(schema, dict):
        return ""
    value = schema.get("format")
    return str(value).casefold() if isinstance(value, str) else ""


def _identity_argument(action: str, argument: str, schema,
                       argument_types: dict) -> bool:
    """Return whether an Effect argument carries external identity authority.

    Operator annotations are the primary source of truth.  The name/schema
    fallback covers legacy manifests that predate ``argument_types`` but expose
    conventional authority-bearing positions such as ``target_id``.
    """
    kind = str(argument_types.get(action, {}).get(argument, "")).casefold()
    if kind in _IDENTITY_ARGUMENT_KINDS:
        return True
    if _schema_format(schema) in {"uri", "url", "email"}:
        return True
    normalized = str(argument).strip().casefold().replace("-", "_")
    return normalized in _IDENTITY_ARGUMENT_NAMES


def normalize_contract(data):
    """Canonicalize ids and unambiguous aliases without adding a Clause."""
    if not isinstance(data, dict) or not isinstance(data.get("clauses"), list):
        return data
    aliases = {}
    for index, raw in enumerate(data["clauses"]):
        output = raw.get("output") if isinstance(raw, dict) else None
        if isinstance(output, str) and output:
            aliases[f"c{index}"] = f"c{index}.{output}"
            aliases[f"c{index}.output"] = f"c{index}.{output}"

    def ref(value):
        return aliases.get(value, value) if isinstance(value, str) else value

    def spec(value):
        if (not isinstance(value, dict) or
                set(value) not in ({"from"}, {"from", "delegated"})):
            return value
        origins = value["from"]
        if isinstance(origins, str):
            return {**value, "from": ref(origins)}
        if isinstance(origins, list):
            return {**value, "from": [ref(item) for item in origins]}
        return value

    clauses = []
    for index, raw in enumerate(data["clauses"]):
        if not isinstance(raw, dict):
            clauses.append(raw)
            continue
        clause = {key: value for key, value in raw.items() if value is not None}
        clause["id"] = f"c{index}"
        if isinstance(clause.get("arguments"), dict):
            clause["arguments"] = {
                name: spec(value) for name, value in clause["arguments"].items()}
        if isinstance(clause.get("from"), list):
            clause["from"] = [ref(item) for item in clause["from"]]
        if isinstance(clause.get("operands"), list):
            clause["operands"] = [ref(item) if isinstance(item, str) else item
                                  for item in clause["operands"]]
        clauses.append(clause)
    return {"task": data.get("task", ""), "clauses": clauses}


def validate_contract(data, trusted_task, actions, environment_sources,
                      allowed_args, required_args=None,
                      effect_return_actions=None, observation_actions=None,
                      argument_schemas=None, output_schemas=None,
                      argument_types=None):
    if not isinstance(data, dict):
        return ["contract is not an object"]
    errors = []
    if set(data) != {"task", "clauses"}:
        errors.append("contract fields mismatch")
    if data.get("task") != trusted_task:
        errors.append("task mismatch")
    clauses = data.get("clauses")
    if not isinstance(clauses, list):
        return errors + ["clauses is not a list"]
    available = {"task"}
    if "runtime-context" in set(environment_sources):
        available.add("runtime-context")
    effect_return_actions = set(effect_return_actions or ())
    observation_actions = set(observation_actions or allowed_args)
    required_args = required_args or {}
    argument_schemas = argument_schemas or {}
    output_schemas = output_schemas or {}
    argument_types = argument_types or {}
    prior_effects = {}
    # These outputs still depend only on an unmaterialized semantic read of
    # the trusted task. They carry authority, but no code-owned exact value.
    unmaterialized_task_derives = set()
    observation_dependent = set()
    contract_closed = set()

    def runtime_arguments(arguments):
        """Remove Contract-only metadata before effect-return call matching."""
        return {
            name: ({"from": spec["from"]}
                   if isinstance(spec, dict) and
                   set(spec) == {"from", "delegated"} else spec)
            for name, spec in dict(arguments or {}).items()
        }

    def schema_fields(value):
        fields = set()
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                fields.update(map(str, properties))
            for child in value.values():
                fields.update(schema_fields(child))
        elif isinstance(value, list):
            for child in value:
                fields.update(schema_fields(child))
        return fields

    attested_fields = set()
    for schema in output_schemas.values():
        attested_fields.update(schema_fields(schema))

    def task_literal(value) -> bool:
        if isinstance(value, str) and value:
            return value.casefold() in trusted_task.casefold()
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, (int, float)):
            import re
            return re.search(
                r"(?<![\w.])" + re.escape(str(value)) + r"(?![\w.])",
                trusted_task) is not None
        return False

    def output_valid(value):
        return isinstance(value, str) and bool(value) and "." not in value

    def operand_closed(operand):
        if isinstance(operand, dict) and set(operand) == {"literal"}:
            return True
        return operand in contract_closed

    def conditional_closes(operator, operands) -> bool:
        operands = tuple(operands or ())
        if operator in _CLOSING_OPERATORS:
            return True
        if operator == "field":
            return bool(operands and operand_closed(operands[0]))
        if operator == "aligned_lookup":
            return len(operands) == 3 and operand_closed(operands[2])
        if operator in {"basename", "identity", "singleton"}:
            return bool(operands and operand_closed(operands[0]))
        if operator == "path_join":
            return len(operands) == 2 and all(map(operand_closed, operands))
        if operator == "object_set":
            return len(operands) == 3 and operand_closed(operands[0])
        return False

    def references(spec, prefix, *, effect=False):
        fields = set(spec) if isinstance(spec, dict) else set()
        if fields == {"from", "delegated"}:
            if not effect or spec.get("delegated") is not True:
                errors.append(prefix + " invalid delegation")
                return ()
        elif fields not in ({"literal"}, {"from"}):
            errors.append(prefix + " argument must be exactly literal or from")
            return ()
        if set(spec) == {"literal"}:
            return ()
        if "from" not in spec:
            errors.append(prefix + " argument must be exactly literal or from")
            return ()
        raw = spec["from"]
        refs = (raw,) if isinstance(raw, str) else tuple(raw or ())
        if not refs or any(not isinstance(ref, str) or ref not in available or
                           ref == "task" for ref in refs):
            errors.append(prefix + " invalid from reference")
            return ()
        return refs

    common = {"id", "type", "instruction"}
    fields = {
        "acquire": common | {"capability", "arguments", "output"},
        "derive": common | {"from", "output"},
        "conditional": common | {"operator", "operands", "output"},
        "effect": common | {"action", "arguments"},
    }
    for index, raw in enumerate(clauses):
        prefix = f"clause[{index}]"
        if not isinstance(raw, dict):
            errors.append(prefix + " is not an object")
            continue
        kind = raw.get("type")
        allowed_fields = fields.get(kind)
        if (kind in {"acquire", "derive"} and
                set(raw) == fields[kind] | {"quantified"}):
            allowed_fields = set(raw)
        if kind not in fields or set(raw) != allowed_fields:
            errors.append(prefix + " fields mismatch")
            continue
        if raw.get("id") != f"c{index}":
            errors.append(prefix + " invalid id")
        if not isinstance(raw.get("instruction"), str) or not raw["instruction"].strip():
            errors.append(prefix + " invalid instruction")

        if kind == "derive":
            if "quantified" in raw and raw.get("quantified") is not True:
                errors.append(prefix + " quantified must be true when present")
            inputs = raw.get("from")
            if (not isinstance(inputs, list) or not inputs or
                    any(not isinstance(ref, str) or ref not in available
                        for ref in inputs)):
                errors.append(prefix + " invalid derive inputs")
            output = raw.get("output")
            if not output_valid(output):
                errors.append(prefix + " invalid output")
            else:
                output_ref = f"c{index}.{output}"
                available.add(output_ref)
                if (isinstance(inputs, list) and inputs and
                        all(ref == "task" or
                            ref in unmaterialized_task_derives
                            for ref in inputs)):
                    unmaterialized_task_derives.add(output_ref)
                if any(ref in observation_dependent for ref in inputs or ()):
                    observation_dependent.add(output_ref)
                else:
                    contract_closed.add(output_ref)
            continue

        if kind == "conditional":
            operator, operands = raw.get("operator"), raw.get("operands")
            arity = OPERATOR_ARITY.get(operator)
            valid_operands = True
            if isinstance(operands, list):
                for position, operand in enumerate(operands):
                    if (isinstance(operand, str) and operand in available and
                            is_clause_ref(operand)):
                        continue
                    if not (isinstance(operand, dict) and
                            set(operand) == {"literal"}):
                        valid_operands = False
                        continue
                    literal = operand["literal"]
                    field_position = (
                        (operator in {"field", "project", "object_set"} and
                         position == 1) or
                        (operator == "select_eq" and position == 1))
                    if field_position:
                        valid_operands &= (
                            isinstance(literal, str) and
                            literal in attested_fields)
                    elif operator == "sort_by" and position == 1:
                        valid_operands &= (
                            isinstance(literal, list) and bool(literal) and
                            all(isinstance(item, str) and
                                item in (attested_fields | {"value", "count"})
                                for item in literal))
                    elif operator == "sort_by" and position == 2:
                        valid_operands &= (
                            isinstance(literal, list) and bool(literal) and
                            all(item in {"asc", "desc"} for item in literal))
                    else:
                        valid_operands &= task_literal(literal)
            if (arity is None or not isinstance(operands, list) or
                    len(operands) != arity or
                    not valid_operands or
                    (operator in {"argmin", "argmax"} and
                     len(operands) == 2 and operands[0] == operands[1])):
                errors.append(prefix + " invalid conditional")
            output = raw.get("output")
            if not output_valid(output):
                errors.append(prefix + " invalid output")
            else:
                output_ref = f"c{index}.{output}"
                available.add(output_ref)
                if any(isinstance(operand, str) and
                       operand in unmaterialized_task_derives
                       for operand in (operands or ())):
                    unmaterialized_task_derives.add(output_ref)
                if any(isinstance(operand, str) and
                       operand in observation_dependent
                       for operand in (operands or ())):
                    observation_dependent.add(output_ref)
                if conditional_closes(operator, operands):
                    contract_closed.add(output_ref)
            continue

        arguments = raw.get("arguments")
        if not isinstance(arguments, dict):
            errors.append(prefix + " invalid arguments")
            continue
        if kind == "acquire" and raw.get("quantified") is True:
            axes = [value for value in arguments.values()
                    if isinstance(value, dict) and set(value) == {"from"}]
            if (len(axes) != 1 or
                    not isinstance(axes[0].get("from"), str)):
                errors.append(
                    prefix + " quantified Acquire needs exactly one scalar "
                    "from axis")
        name = raw.get("capability") if kind == "acquire" else raw.get("action")
        if kind == "acquire":
            if "quantified" in raw and raw.get("quantified") is not True:
                errors.append(prefix + " quantified must be true when present")
            if name not in observation_actions:
                errors.append(prefix + " unknown observation capability")
        elif name not in actions:
            errors.append(prefix + " unknown action")
        allowed = set(allowed_args.get(name, ()))
        if not set(arguments).issubset(allowed):
            errors.append(prefix + " arguments must be registered schema positions")
        missing = set(required_args.get(name, ())) - set(arguments)
        if missing:
            errors.append(prefix + " missing required arguments: " +
                          ",".join(sorted(missing)))
        role_uses = {}
        for argument, value in arguments.items():
            refs = references(value, prefix + "." + str(argument),
                              effect=(kind == "effect"))
            if isinstance(value, dict) and set(value) == {"literal"}:
                literal = value["literal"]
                schema = argument_schemas.get(name, {}).get(argument, {})
                if not _schema_value_matches(schema, literal):
                    errors.append(prefix + "." + str(argument) +
                                  " literal does not match operator schema")
                if "const" in schema and literal != schema["const"]:
                    errors.append(prefix + "." + str(argument) +
                                  " literal violates operator const")
                enum = schema.get("enum")
                if isinstance(enum, list) and literal not in enum:
                    errors.append(prefix + "." + str(argument) +
                                  " literal violates operator enum")
                operator_fixed = (
                    ("const" in schema and literal == schema["const"]) or
                    ("default" in schema and literal == schema["default"]))
                if schema.get("x-task-derived") is True:
                    errors.append(
                        prefix + "." + str(argument) +
                        " is operator-attested task-derived; use a Derive role")
                if (isinstance(literal, str) and not operator_fixed and
                        literal.casefold() not in trusted_task.casefold()):
                    errors.append(
                        prefix + "." + str(argument) +
                        " string literal is not an exact trusted-task value; "
                        "bind a task-derived role instead")
            elif (any(ref in unmaterialized_task_derives for ref in refs) and
                  _exact_schema(
                      argument_schemas.get(name, {}).get(argument, {}))):
                errors.append(
                    prefix + "." + str(argument) +
                    " exact-only argument cannot bind a task-only semantic "
                    "Derive; use a typed literal or deterministic Conditional")
            if kind == "effect":
                schema = argument_schemas.get(name, {}).get(argument, {})
                if (_identity_argument(name, str(argument), schema,
                                       argument_types) and
                        any((ref in unmaterialized_task_derives or
                             (ref in observation_dependent and
                              ref not in contract_closed))
                            for ref in refs)):
                    errors.append(
                        prefix + "." + str(argument) +
                        " identity-bearing Effect argument is semantic or "
                        "observation-dependent without deterministic Contract "
                        "closure; "
                        "use a trusted literal, closed Conditional, or leave "
                        "the Effect path unsupported")
            if kind == "effect":
                for ref in refs:
                    if is_clause_ref(ref):
                        role_uses.setdefault(ref, []).append(str(argument))
        if kind == "effect":
            for ref, names in role_uses.items():
                if len(set(names)) > 1:
                    errors.append(prefix + " output " + ref +
                                  " binds multiple argument roles; split scalar outputs")

        if kind == "acquire":
            output = raw.get("output")
            if not output_valid(output):
                errors.append(prefix + " invalid output")
            else:
                output_ref = f"c{index}.{output}"
                available.add(output_ref)
                observation_dependent.add(output_ref)
            if (name in effect_return_actions and
                    runtime_arguments(arguments) not in
                    prior_effects.get(name, ())):
                errors.append(prefix + " effect-return Acquire must be immediately preceded by an Effect Clause for the same capability with byte-for-byte identical arguments; insert the Effect, not another Acquire")
        elif name in actions:
            prior_effects.setdefault(name, []).append(
                runtime_arguments(arguments))
    return errors


def task_contract_tool_schema():
    argument = {"oneOf": [
        {"type": "object", "properties": {"literal": {}},
         "required": ["literal"], "additionalProperties": False},
        {"type": "object", "properties": {"from": {"oneOf": [
            {"type": "string"}, {"type": "array", "items": {"type": "string"},
                                "minItems": 1}]}},
         "required": ["from"], "additionalProperties": False},
        {"type": "object", "properties": {
            "from": {"oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"},
                 "minItems": 1}]},
            "delegated": {"type": "boolean", "const": True}},
         "required": ["from", "delegated"], "additionalProperties": False},
    ]}
    arguments = {"type": "object", "additionalProperties": argument}
    common = {"id": {"type": "string"}, "instruction": {"type": "string"}}

    def variant(kind, properties, required):
        return {"type": "object", "properties": {**common,
                "type": {"type": "string", "enum": [kind]}, **properties},
                "required": ["id", "type", "instruction", *required],
                "additionalProperties": False}

    clause = {"oneOf": [
        variant("acquire", {"capability": {"type": "string"},
                            "arguments": arguments,
                            "output": {"type": "string"},
                            "quantified": {"type": "boolean", "const": True}},
                ["capability", "arguments", "output"]),
        variant("derive", {"from": {"type": "array",
                                     "items": {"type": "string"}, "minItems": 1},
                           "output": {"type": "string"},
                           "quantified": {"type": "boolean", "const": True}},
                ["from", "output"]),
        variant("conditional", {"operator": {"type": "string",
                                               "enum": list(OPERATOR_ARITY)},
                                "operands": {"type": "array",
                                             "items": {"oneOf": [
                                                 {"type": "string"},
                                                 {"type": "object",
                                                  "properties": {"literal": {}},
                                                  "required": ["literal"],
                                                  "additionalProperties": False}]},
                                             "minItems": 1, "maxItems": 3},
                                "output": {"type": "string"}},
                ["operator", "operands", "output"]),
        variant("effect", {"action": {"type": "string"},
                           "arguments": arguments}, ["action", "arguments"]),
    ]}
    return {"type": "function", "function": {
        "name": "emit_task_contract",
        "description": "Return one explicit validated TaskContract candidate.",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string"},
            "clauses": {"type": "array", "items": clause}},
            "required": ["task", "clauses"], "additionalProperties": False}}}
