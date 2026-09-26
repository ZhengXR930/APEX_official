"""Build normalized APEX registry bundles from trusted benchmark metadata."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from benchmark.registry.schema import bundle, environment


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def binding(environment_id: str, *conditions: str, **extra) -> dict:
    return {
        "environment": environment_id,
        "conditions": {name: {} for name in conditions},
        **extra,
    }


def matching_key(name: str, candidates) -> str:
    token = re.sub(r"[^a-z0-9]+", "", str(name).lower())
    matches = [item for item in candidates
               if re.sub(r"[^a-z0-9]+", "", str(item).lower()) == token]
    if len(matches) != 1:
        raise ValueError(f"cannot uniquely map {name!r}: {matches}")
    return matches[0]


def skill_source(name: str) -> dict:
    identifier = f"skill:{name}:instructions"
    return {
        "id": identifier,
        "description": f"Loaded {name} SKILL.md prose visible to the target Agent.",
        "plantable": True,
        "carrier": "observation",
        "identity_paths": [],
    }


def skill_overlay(items) -> dict:
    skills, sources = {}, {}
    for item in items:
        name = str(item["name"])
        skills[name] = {
            "name": name,
            "description": str(item.get("description") or ""),
            "tools": list(item.get("tools") or ()),
            "constraints": list(item.get("constraints") or ()),
        }
        source = skill_source(name)
        sources[source["id"]] = source
    return {"skills": skills, "sources": sources}


def asb(source: Path, data: Path):
    raw = read(source / "asb_opi/manifest.json")
    indexed = {item["name"]: item for item in raw["capabilities"]}
    environments = {
        agent: environment(
            f"asb_opi:{agent}", [indexed[name] for name in names])
        for agent, names in raw["by_agent"].items()
    }
    bindings = {}
    for condition, filename in (("clean", "clean_cases.json"),
                                ("attack", "attack_cases.json")):
        for row in read(data / "asb_opi" / filename):
            bindings[str(row["case_id"])] = binding(
                str(row["agent_name"]), condition)
    manifest = source / "asb_opi/manifest.json"
    return bundle(
        "ASB-OPI", environments, case_bindings=bindings,
        registry_source="frozen active_defense capability registration",
        registry_source_sha256=file_sha256(manifest))


def mcptox(source: Path, data: Path):
    from benchmark.adapter.mcptox import _clean_id

    raw = read(source / "mcptox/manifest.json")
    grouped = {}
    for name, item in raw["capabilities"].items():
        server = name.split("__", 1)[0]
        grouped.setdefault(server, []).append(item)
    environments = {
        server: environment(f"mcptox:{server}", tools)
        for server, tools in grouped.items()
    }
    bindings = {}
    cases = read(data / "mcptox" / "cases.json")
    for server in cases["servers"].values():
        name = str(server["server_name"])
        environment_name = matching_key(name, environments)
        for query in server["clean_querys"]:
            bindings[_clean_id(name, str(query))] = binding(
                environment_name, "clean")
        for instance in server["malicious_instance"]:
            for row in instance.get("datas", ()):
                bindings[f"{name}:{row['id']}"] = binding(
                    environment_name, "attack")
    manifest = source / "mcptox/manifest.json"
    return bundle(
        "MCPTox", environments, case_bindings=bindings,
        registry_source="frozen active_defense capability registration",
        registry_source_sha256=file_sha256(manifest))


def msb(source: Path, data: Path):
    from benchmark.registry.msb import attest_tools
    from benchmark.registry.mcptox import registration

    raw = read(source / "msb/tools.json")
    environments = {}
    for server_name, server in raw["servers"].items():
        tools = attest_tools(server_name, server["tools"])
        registered = []
        for tool in tools:
            item = registration(
                server_name, tool, effect=bool(tool["x-effect"]),
                observation=True)
            item["outputSchema"] = tool.get("outputSchema") or {"type": "string"}
            registered.append(item)
        environments[server_name] = environment(
            f"msb:{server_name}", registered)
    cases = read(data / "msb" / "cases.json")
    bindings = {
        str(row["case_id"]): binding(str(row["tool"]), "attack")
        for row in cases["cases"]
    }
    manifest = source / "msb/tools.json"
    return bundle(
        "MSB", environments, case_bindings=bindings,
        registry_source="frozen active_defense Tool catalog and attestations",
        registry_source_sha256=file_sha256(manifest))


def scr(source: Path, data: Path):
    from benchmark.registry.scr import (
        AUTHBLUR_VISIBLE_TOOLS, TRUSTLIFT_TOOLS, authblur_tools)

    raw = read(source / "scr/capflow.json")
    index = read(data / "scr/cases.json")
    environments = {}

    def add_environment(prefix: str, tools, **kwargs) -> str:
        candidate = environment("pending", tools, **kwargs)
        canonical = {key: value for key, value in candidate.items()
                     if key != "id"}
        digest = hashlib.sha256(json.dumps(
            canonical, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode()).hexdigest()[:12]
        key = f"{prefix}:{digest}"
        candidate["id"] = f"scr:{key}"
        existing = environments.setdefault(key, candidate)
        if existing != candidate:
            raise ValueError(f"SCR environment collision: {key}")
        return key

    bindings = {}
    for case_id, row in raw["cases"].items():
        by_role = {tool["x-scr-role"]: tool for tool in row["tools"]}
        env = {
            "control": add_environment("capflow", []),
            "A_only": add_environment("capflow", [by_role["A"]]),
            "B_only": add_environment("capflow", [by_role["B"]]),
            "A+B_neutral": add_environment(
                "capflow", [by_role["A"], by_role["B"]]),
            "A+B_explicit": add_environment(
                "capflow", [by_role["A"], by_role["B"]]),
        }
        metadata = index["suites"]["capflow"]["cases"][case_id]
        skills = {item["name"]: item for item in metadata["skills"].values()}
        overlays = {}
        for condition, selected in metadata["conditions"].items():
            overlays[condition] = {
                "environment": env[condition],
                **skill_overlay(skills[name] for name in selected),
            }
        overlays["clean"] = dict(overlays["B_only"])
        overlays["attack"] = dict(overlays["A+B_neutral"])
        bindings[f"capflow:{case_id}"] = {
            "environment": env["A+B_neutral"],
            "conditions": overlays,
        }

    auth_visible = {
        "tools": AUTHBLUR_VISIBLE_TOOLS,
        "skills": [],
        "phases": {
            "advisor": {"tools": [], "skills": ["advisor"]},
            "control": {
                "tools": [tool["name"] for tool in AUTHBLUR_VISIBLE_TOOLS],
                "skills": ["control"],
            },
        },
    }
    auth_environment = add_environment(
        "authblur", authblur_tools(), agent_visible_surface=auth_visible)
    for case_id, metadata in index["suites"]["authblur"]["cases"].items():
        roles = metadata["skills"]
        condition_roles = {
            "level2_findings": ("plain_advisor", "control"),
            "level3_fullauth": ("full_advisor", "control"),
            "clean": ("plain_advisor", "control"),
            "attack": ("full_advisor", "control"),
        }
        overlays = {}
        for condition, selected_roles in condition_roles.items():
            items = []
            for role in selected_roles:
                item = dict(roles[role])
                item["tools"] = ([] if "advisor" in role else
                                 ["control_decision", "authorize_control"])
                items.append(item)
            overlays[condition] = skill_overlay(items)
        bindings[f"authblur:{case_id}"] = {
            "environment": auth_environment,
            "eligible": bool(metadata.get("eligible", True)),
            "missing_skills": list(metadata.get("missing_skills") or ()),
            "conditions": overlays,
        }

    trust_skill = {
        "name": "secure-skill",
        "description": "Scan project Skills and install named entries from a local registry.",
        "constraints": ["Installation is confined to the case-local registry."],
    }
    trust_source = skill_overlay([{**trust_skill,
                                   "tools": ["scan_skills", "install_skills"]}])
    trust_full = add_environment(
        "trustlift", TRUSTLIFT_TOOLS, **trust_source)
    trust_clean = add_environment(
        "trustlift", [TRUSTLIFT_TOOLS[0]],
        **skill_overlay([{**trust_skill, "tools": ["scan_skills"]}]))
    for case_id, metadata in index["suites"]["trustlift"]["cases"].items():
        bindings[f"trustlift:{case_id}"] = {
            "environment": trust_full,
            "metadata": {
                "target_skill": metadata["target_skill"],
                "registry_entries": metadata["registry_entries"],
            },
            "conditions": {
                "clean": {"environment": trust_clean},
                "attack": {"environment": trust_full},
            },
        }

    return bundle(
        "SCR", environments, case_bindings=bindings,
        source_repository=index["source_repository"],
        source_commit=index["source_commit"],
        registry_source="frozen active_defense registrations plus SCR case index",
        registry_source_sha256=file_sha256(source / "scr/capflow.json"),
        suite_counts={
            suite: {
                "available": value["case_count"],
                "eligible": sum(bool(case.get("eligible", True))
                                for case in value["cases"].values()),
            }
            for suite, value in index["suites"].items()
        },
    )


def skillinject(source: Path, data: Path):
    from benchmark.registry.skillinject import (
        TOOLS, _NATIVE_SKILL_TOOLS, _TASK_SKILL_ALIASES)

    raw = read(source / "skillinject/catalog.json")
    environments = {}
    for name, skill in raw["skills"].items():
        native = list(_NATIVE_SKILL_TOOLS.get(name, ()))
        tools = [*TOOLS, *skill.get("helpers", ()), *native]
        manifest = {key: skill[key] for key in
                    ("name", "description", "tools", "constraints")}
        manifest["tools"] = list(dict.fromkeys([
            *manifest["tools"], *(tool["name"] for tool in native)]))
        environments[name] = environment(
            f"skillinject:{name}", tools, skills={name: manifest})
    bindings = {}
    for row in read(data / "skillinject" / "cases.json"):
        for index, task in enumerate(row.get("tasks") or ()):
            skill = _TASK_SKILL_ALIASES.get(
                str(task["skill"]), str(task["skill"]))
            case_id = f"id{int(row['id']):03d}:task{index}"
            bindings[case_id] = binding(skill, "clean", "attack")
    catalog = source / "skillinject/catalog.json"
    return bundle(
        "SkillInject", environments, case_bindings=bindings,
        registry_source="frozen active_defense clean-Skill catalog",
        registry_source_sha256=file_sha256(catalog))


def agentdojo(data: Path):
    from importlib.metadata import version
    from pydantic import TypeAdapter
    from agentdojo.task_suite.load_suites import get_suites
    from benchmark.registry.agentdojo import (
        COMMIT_ENTRIES, MEDIATED_OBSERVATIONS, NATURAL_LANGUAGE_ARGUMENTS,
        OUTPUT_TYPES)

    environments = {}
    for suite_name, suite in get_suites("v1.2.2").items():
        effects = COMMIT_ENTRIES[suite_name]
        mediated = MEDIATED_OBSERVATIONS.get(suite_name, frozenset())
        tools = []
        for tool in suite.tools:
            name = str(tool.name)
            input_schema = tool.parameters.model_json_schema()
            input_schema.setdefault("required", [])
            output_schema = TypeAdapter(tool.return_type).json_schema()
            if output_schema.get("type") == "null":
                output_schema = {"type": "string"}
            is_effect = name in effects
            is_observation = not is_effect or name in mediated
            tools.append({
                "name": name,
                "description": str(tool.description),
                "inputSchema": input_schema,
                "outputSchema": output_schema,
                "effect": is_effect,
                "observation": is_observation,
                "effect_return": bool(is_effect and is_observation),
                "receipt_role": "data",
                "argument_types": {
                    argument: "natural_language" for argument in
                    NATURAL_LANGUAGE_ARGUMENTS.get(
                        suite_name, {}).get(name, ())
                },
                "output_types": OUTPUT_TYPES.get(suite_name, {}).get(name, {}),
            })
        environments[suite_name] = environment(
            f"agentdojo:{suite_name}", tools)
    bindings = {}
    clean = read(data / "agentdojo" / "clean_tasks.json")
    for suite_name, tasks in clean.items():
        for task in tasks:
            bindings[f"{suite_name}:{task}"] = binding(suite_name, "clean")
    for path in sorted((data / "agentdojo").glob("*_pairs.json")):
        suite_name = path.stem.removesuffix("_pairs")
        for task, injection in read(path):
            bindings[f"{suite_name}:{task}:{injection}"] = binding(
                suite_name, "attack")
    return bundle(
        "AgentDojo", environments, case_bindings=bindings,
        benchmark_version="v1.2.2",
        package_version=version("agentdojo"),
        registry_source=(
            "active_defense boundary attestations plus AgentDojo suite "
            "tool definitions"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root", type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmark/registry/source")
    parser.add_argument(
        "--output-root", type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmark/registry/data")
    parser.add_argument(
        "--data-root", type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmark/data")
    args = parser.parse_args()
    builders = {
        "agentdojo": lambda source, data: agentdojo(data),
        "asb_opi": asb,
        "mcptox": mcptox,
        "msb": msb,
        "scr": scr,
        "skillinject": skillinject,
    }
    for name, builder in builders.items():
        write(args.output_root / name / "manifest.json",
              builder(args.source_root, args.data_root))


if __name__ == "__main__":
    main()
