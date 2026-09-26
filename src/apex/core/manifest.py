"""Strict adequacy checks for benchmark capability manifests.

Core Surveyor remains compatible with compact unit-test manifests.  Formal
benchmark adapters use this gate before Contract synthesis so a missing schema
cannot be mistaken for a TaskContractor failure.
"""
from __future__ import annotations


def validate_registrations(rows, benchmark: str) -> None:
    rows = [dict(row) for row in rows or ()]
    if not rows:
        raise ValueError(f"{benchmark} manifest is empty")
    names = set()
    for row in rows:
        name = str(row.get("name", ""))
        prefix = f"{benchmark} capability {name!r}"
        if not name or name in names:
            raise ValueError(prefix + " is empty or duplicated")
        names.add(name)
        description = str(row.get("description", "")).strip()
        if not description or description == name:
            raise ValueError(prefix + " has no functional description")
        for field in ("effect", "observation"):
            if type(row.get(field)) is not bool:
                raise TypeError(prefix + f" requires boolean {field}")
        schema = row.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise TypeError(prefix + " requires an object inputSchema")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise TypeError(prefix + " requires inputSchema.properties")
        if any(not isinstance(value, dict) or not value
               for value in properties.values()):
            raise ValueError(prefix + " has an untyped argument")
        if "required_arguments" in row:
            required = row["required_arguments"]
        elif "required" in schema:
            required = schema["required"]
        else:
            raise ValueError(prefix + " does not attest required arguments")
        if (not isinstance(required, (list, tuple)) or
                not set(map(str, required)).issubset(properties)):
            raise ValueError(prefix + " has invalid required arguments")
        output = row.get("outputSchema", row.get("output_schema"))
        if row["observation"] and (
                not isinstance(output, dict) or not output):
            raise ValueError(prefix + " observation has no output schema")
        effect_return = row.get("effect_return", False)
        if type(effect_return) is not bool:
            raise TypeError(prefix + " requires boolean effect_return")
        if effect_return and not (
                row["effect"] and row["observation"] and
                isinstance(output, dict) and output):
            raise ValueError(prefix + " has invalid effect-return boundary")
        argument_types = row.get("argument_types", {})
        if (not isinstance(argument_types, dict) or
                not set(map(str, argument_types)).issubset(properties)):
            raise ValueError(prefix + " has invalid argument_types")
        if row.get("receipt_role", "data") not in {
                "data", "advisory", "control"}:
            raise ValueError(prefix + " has invalid receipt_role")


def validate_plan(plan, benchmark: str) -> None:
    capabilities = getattr(plan, "capabilities", {}) or {}
    if not capabilities:
        raise ValueError(f"{benchmark} environment plan is empty")
    for name, surface in capabilities.items():
        prefix = f"{benchmark} capability {name!r}"
        if not surface.description or surface.description == name:
            raise ValueError(prefix + " has no functional description")
        if surface.required_arguments is None:
            raise ValueError(prefix + " does not attest required arguments")
        schemas = dict(surface.argument_schemas)
        if set(schemas) != set(surface.arguments) or any(
                not isinstance(value, dict) or not value
                for value in schemas.values()):
            raise ValueError(prefix + " has incomplete argument schemas")
        if surface.observation and not surface.output_schema:
            raise ValueError(prefix + " observation has no output schema")
        if surface.effect_return and not (
                surface.effect and surface.observation and
                surface.output_schema):
            raise ValueError(prefix + " has invalid effect-return boundary")
