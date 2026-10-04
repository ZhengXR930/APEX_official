"""Canonical on-disk schema for benchmark capability registries."""
from __future__ import annotations

import json
import hashlib
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

from apex.defender.memory import EnvironmentPlan

SCHEMA = "apex-benchmark-registry-v2"


def capability(raw: dict[str, Any], *, name: str | None = None) -> dict[str, Any]:
    """Normalize one operator-owned capability without changing its schemas."""
    item = dict(raw)
    item["name"] = str(name or item.get("name") or "")
    if not item["name"]:
        raise ValueError("registered capability has no name")
    input_schema = item.get("inputSchema")
    if not isinstance(input_schema, dict):
        argument_schemas = item.get("argument_schemas") or {}
        required = item.get("required_arguments") or []
        input_schema = {
            "type": "object", "properties": argument_schemas,
            "required": list(required), "additionalProperties": False,
        }
    else:
        input_schema = dict(input_schema)
        input_schema.setdefault("type", "object")
        input_schema.setdefault("properties", {})
        input_schema.setdefault(
            "required", list(item.get("required_arguments") or ()))
    effect = item.get("effect")
    observation = item.get("observation")
    if type(effect) is not bool or type(observation) is not bool:
        raise TypeError(
            f"capability {item['name']!r} needs boolean effect/observation")
    output_schema = item.get("outputSchema", item.get("output_schema"))
    if not isinstance(output_schema, dict):
        if observation:
            raise ValueError(f"capability {item['name']!r} has no output schema")
        output_schema = {"type": "null"}
    return {
        "name": item["name"],
        "description": str(item.get("description") or ""),
        "inputSchema": input_schema,
        "outputSchema": dict(output_schema),
        "effect": effect,
        "observation": observation,
        "effect_return": bool(item.get("effect_return", False)),
        "receipt_role": str(item.get("receipt_role", "data")),
        "argument_types": dict(item.get("argument_types") or {}),
        "output_types": dict(item.get("output_types") or {}),
    }


def _visible_tool(item: dict) -> dict:
    return {key: item[key] for key in
            ("name", "description", "inputSchema", "outputSchema")}


def environment(identifier: str, capabilities, *, sources=None, skills=None,
                agent_visible_surface=None) -> dict:
    """Build one complete defense registration environment."""
    normalized = [capability(item) for item in capabilities]
    by_name = {item["name"]: item for item in normalized}
    if len(by_name) != len(normalized):
        raise ValueError(f"duplicate capability in environment {identifier!r}")
    skill_map = dict(skills or {})
    visible = agent_visible_surface
    if visible is None:
        visible = {
            "tools": [_visible_tool(item) for item in normalized],
            "skills": [
                {"name": str(value.get("name") or key),
                 "description": str(value.get("description") or ""),
                 "tools": list(value.get("tools") or ())}
                for key, value in skill_map.items()
            ],
        }
    return {
        "id": str(identifier),
        "sources": dict(sources or {}),
        "capabilities": by_name,
        "skills": skill_map,
        "agent_visible_surface": dict(visible),
    }


def _unit_id(item: dict) -> str:
    body = json.dumps(item, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", item["name"]).strip("_")
    return f"{slug or 'capability'}@{hashlib.sha256(body).hexdigest()[:12]}"


def bundle(benchmark: str, environments: dict[str, dict], *,
           case_bindings=None, **metadata) -> dict:
    """Deduplicate capabilities while retaining reusable environments.

    A benchmark case is not a capability unit. Multiple cases therefore bind
    to one environment whenever their registered execution boundary is the
    same. The final artifact keeps the case coverage explicit without copying
    identical schemas hundreds of times.
    """
    units: dict[str, dict] = {}
    compact = {}
    for key, raw in environments.items():
        value = dict(raw)
        refs = []
        for item in (value.pop("capabilities") or {}).values():
            unit_id = _unit_id(item)
            existing = units.setdefault(unit_id, item)
            if existing != item:
                raise ValueError(f"capability unit hash collision: {unit_id}")
            refs.append(unit_id)
        if len(refs) != len(set(refs)):
            raise ValueError(f"duplicate capability unit in environment {key!r}")
        value["capability_units"] = refs
        compact[str(key)] = value

    bindings = dict(case_bindings or {})
    for case_id, binding in bindings.items():
        if not isinstance(binding, dict):
            raise TypeError(f"case binding {case_id!r} must be an object")
        target = str(binding.get("environment", ""))
        if target not in compact:
            raise ValueError(
                f"case binding {case_id!r} references unknown environment {target!r}")
        for condition, overlay in (binding.get("conditions") or {}).items():
            variant = str((overlay or {}).get("environment", target))
            if variant not in compact:
                raise ValueError(
                    f"case binding {case_id!r}/{condition!r} references "
                    f"unknown environment {variant!r}")
    return {
        "schema": SCHEMA,
        "benchmark": str(benchmark),
        "capability_units": units,
        "environments": compact,
        "case_bindings": bindings,
        **metadata,
    }


@lru_cache(maxsize=32)
def _read_bundle(path: str, modified_ns: int, size: int) -> dict:
    del modified_ns, size
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_bundle(path: str | Path, *, benchmark: str | None = None) -> dict:
    target = Path(path).expanduser().resolve()
    stat = target.stat()
    raw = _read_bundle(str(target), stat.st_mtime_ns, stat.st_size)
    if (raw.get("schema") != SCHEMA or
            not isinstance(raw.get("capability_units"), dict) or
            not isinstance(raw.get("environments"), dict) or
            not isinstance(raw.get("case_bindings"), dict)):
        raise ValueError(f"invalid registry bundle: {path}")
    if benchmark is not None and raw.get("benchmark") != benchmark:
        raise ValueError(
            f"registry benchmark mismatch: expected {benchmark}, got {raw.get('benchmark')}")
    return raw


def _materialize(raw: dict, identifier: str) -> dict:
    try:
        selected = raw["environments"][str(identifier)]
    except KeyError as exc:
        raise KeyError(f"unknown registry environment: {identifier}") from exc
    selected = dict(selected)
    capabilities = {}
    for unit_id in selected.pop("capability_units", ()):
        try:
            item = raw["capability_units"][unit_id]
        except KeyError as exc:
            raise KeyError(f"unknown capability unit: {unit_id}") from exc
        name = str(item.get("name", ""))
        if name in capabilities:
            raise ValueError(
                f"duplicate capability name {name!r} in environment {identifier!r}")
        capabilities[name] = item
    selected["capabilities"] = capabilities
    return selected


def load_environment(path: str | Path, identifier: str,
                     *, benchmark: str | None = None) -> EnvironmentPlan:
    raw = load_bundle(path, benchmark=benchmark)
    selected = _materialize(raw, str(identifier))
    # agent_visible_surface is an audit projection. EnvironmentPlan consumes
    # only the authoritative sources/capabilities/skills fields.
    return EnvironmentPlan.from_dict(selected)


def load_case_environment(path: str | Path, case_id: str, *, condition=None,
                          benchmark: str | None = None) -> EnvironmentPlan:
    """Resolve one case binding and apply its source/Skill overlays."""
    raw = load_bundle(path, benchmark=benchmark)
    try:
        binding = dict(raw["case_bindings"][str(case_id)])
    except KeyError as exc:
        raise KeyError(f"unknown registry case binding: {case_id}") from exc
    overlay = None
    if condition is not None:
        variants = binding.get("conditions") or {}
        if str(condition) not in variants:
            raise KeyError(
                f"case binding {case_id!r} has no condition {condition!r}")
        overlay = variants[str(condition)]
    environment_id = str(
        (overlay or {}).get("environment", binding["environment"]))
    selected = _materialize(raw, environment_id)
    overlays = [binding, *(tuple([overlay]) if overlay is not None else ())]
    for overlay in overlays:
        for field in ("sources", "skills"):
            selected[field] = {
                **dict(selected.get(field) or {}),
                **dict(overlay.get(field) or {}),
            }
    suffix = f":{condition}" if condition is not None else ""
    selected["id"] = f"{selected['id']}:{case_id}{suffix}"
    return EnvironmentPlan.from_dict(selected)
