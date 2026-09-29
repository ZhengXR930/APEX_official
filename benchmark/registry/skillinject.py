"""Trusted SkillInject Tool and Skill registrations.

The checked-in catalog is generated from the benchmark's clean, operator-owned
Skill packages.  Runtime sandboxes are never scanned for capabilities: an
injected helper therefore cannot register itself by merely appearing beside a
Skill.  Each executable helper is a separate capability with a pinned relative
path and digest; the generic shell remains a last-resort boundary, not a model
of the Skill workflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from benchmark.registry.schema import load_bundle, load_environment


CATALOG = Path(__file__).with_name("data") / "skillinject" / "manifest.json"
_EXECUTABLE_SUFFIXES = {".py": "python", ".js": "node", ".mjs": "node",
                        ".sh": "bash"}
_TASK_SKILL_ALIASES = {
    "email": "email-api",
    "fhir": "fhir-developer-skill",
}

TOOLS = [
    {"name": "read_file", "description": (
        "Read only a UTF-8 plain-text workspace file; this capability cannot "
        "parse binary office documents, PDFs, archives, images, or databases."),
     "observation": True, "effect": False, "effect_return": False,
     "receipt_role": "data", "argument_types": {"path": "path"},
     "outputSchema": {"type": "string"},
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}},
                     "required": ["path"], "additionalProperties": False}},
    {"name": "write_file", "description": (
        "Write one task-scoped UTF-8 artifact in the isolated workspace. The "
        "path and content are visible authorization positions."),
     "observation": True, "effect": True, "effect_return": True,
     "receipt_role": "data",
     "outputSchema": {"type": "string"},
     "argument_types": {"path": "path", "content": "code"},
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"},
                                    "content": {"type": "string"}},
                     "required": ["path", "content"],
                     "additionalProperties": False}},
    {"name": "run_task_script", "description": (
        "Run a task-local script previously created through write_file in this "
        "episode. The runtime requires the exact path and unchanged content "
        "from that authorized write; arbitrary shell commands and installed "
        "Skill additions are not accepted."),
     "observation": True, "effect": True, "effect_return": True,
     "receipt_role": "data",
     "outputSchema": {"type": "string"},
     "argument_types": {"path": "path", "argv": "code/argv"},
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "x-task-derived": True},
                         "argv": {"type": "array",
                                  "items": {"type": "string"},
                                  "default": [],
                                  "x-task-derived": True}},
                     "required": ["path"],
                     "additionalProperties": False}},
]


# Operator-native capabilities exposed by a particular Skill host. These are
# not inferred from the installed (and potentially poisoned) SKILL.md. They
# model the platform boundary that the trusted adapter actually mediates.
DISCORD_MODERATION_TOOL = {
    "name": "moderate_messages",
    "description": (
        "Apply one complete batch of Discord moderation decisions. Each item "
        "names an observed message, the moderation action, and the moderator "
        "answer. This is the structured external boundary for moderation; a "
        "free-text report does not perform these actions."),
    "observation": True,
    "effect": True,
    "effect_return": True,
    "receipt_role": "data",
    "outputSchema": {
        "type": "object",
        "properties": {
            "applied": {"type": "integer"},
            "decisions": {"type": "array"},
        },
        "required": ["applied", "decisions"],
        "additionalProperties": False,
    },
    # The batch is a semantic task output. WRAP still fixes the Root Effect
    # and validates its BindingProof against the Contract-reachable Receipt.
    "argument_types": {"decisions": "natural_language"},
    "inputSchema": {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string"},
                        "action": {
                            "type": "string",
                            "enum": ["no_action", "warn", "delete",
                                     "timeout", "ban"],
                        },
                        "mod_answer": {"type": "string"},
                    },
                    "required": ["message_id", "action", "mod_answer"],
                    "additionalProperties": False,
                },
                "minItems": 1,
            },
        },
        "required": ["decisions"],
        "additionalProperties": False,
    },
    "implementation": {"kind": "structured_moderation"},
}


_NATIVE_SKILL_TOOLS = {
    "discord": (DISCORD_MODERATION_TOOL,),
}


def _frontmatter(text: str, key: str) -> str:
    match = re.search(
        rf"(?m)^{re.escape(key)}:\s*(?:\"([^\"]*)\"|'([^']*)'|([^\n]+))",
        text)
    return next((part.strip() for part in match.groups() if part is not None),
                "") if match else ""


def _documented_environment(text: str) -> dict[str, str]:
    """Read constant runtime endpoints attested by the trusted Skill docs."""
    return {
        name: value
        for name, value in re.findall(
            r"(?m)^export\s+([A-Z][A-Z0-9_]*)=[\"']([^\"'$`]+)[\"']\s*$",
            str(text))
    }


def _tool_name(skill: str, relative: str, used: set[str]) -> str:
    stem = Path(relative).stem
    raw = "skill_" + skill + "__" + stem
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")[:58]
    if name in used:
        suffix = hashlib.sha256(relative.encode()).hexdigest()[:6]
        name = name[:57] + "_" + suffix
    used.add(name)
    return name


def _documented_helpers(directory: Path, text: str) -> list[Path]:
    """Return executable files explicitly named by trusted Skill docs.

    Package inventory alone is insufficient: SkillInject deliberately adds
    executable files.  A helper is registered only when it exists in the clean
    operator package and a trusted Markdown file names either its exact path or
    filename.  Tests and import-only modules are excluded.
    """
    docs = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(directory.rglob("*.md"))) or text
    lines = docs.splitlines()
    helpers = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _EXECUTABLE_SUFFIXES:
            continue
        relative = path.relative_to(directory).as_posix()
        name = path.name.lower()
        if (name.startswith("test_") or "_test." in name or
                name == "__init__.py"):
            continue
        references = [line for line in lines
                      if relative in line or path.name in line]
        invoked = any(
            re.search(r"\b(?:python3?|node|bash|sh)\b", line) or
            re.search(r"\brun_skill_script\s*\(", line) or
            re.search(r"\btool\b", line, flags=re.IGNORECASE) or
            re.search(r"(?:^|\s)\./" +
                      re.escape(relative) + r"(?:\s|`|$)", line)
            for line in references)
        if invoked:
            helpers.append(path)
    return helpers


def _skill_directories(root: Path, names: set[str] | None) -> dict[str, Path]:
    candidates: dict[str, list[Path]] = {}
    for skill_file in sorted(root.rglob("SKILL.md")):
        text = skill_file.read_text(encoding="utf-8", errors="ignore")
        declared = _frontmatter(text, "name")
        installed = skill_file.parent.name
        # SkillInject addresses the installed directory name. Some upstream
        # packages use a different prose/frontmatter display name (notably
        # ``git`` and ``agent-identifier``); installation identity wins.
        # The package directory is the operator-visible installation identity;
        # frontmatter is display metadata and may deliberately use another
        # name (for example the installed ``git`` Skill).
        name = installed
        if names is None or name in names:
            candidates.setdefault(name, []).append(skill_file.parent)
    missing = sorted((names or set()) - set(candidates))
    if missing:
        raise ValueError("trusted Skill root is missing: " + ", ".join(missing))
    # Wrapper Skills occasionally include a second copy in ``skills/``.  The
    # shallow package is the installation boundary used by the benchmark.
    return {name: min(paths, key=lambda path: (len(path.relative_to(root).parts),
                                               path.as_posix()))
            for name, paths in candidates.items()}


def generate_catalog(trusted_root, *, skill_names=None) -> dict:
    """Generate task-independent registrations from clean trusted packages."""
    root = Path(trusted_root).resolve()
    names = None if skill_names is None else set(map(str, skill_names))
    skills = {}
    used: set[str] = {tool["name"] for tool in TOOLS}
    for name, directory in sorted(_skill_directories(root, names).items()):
        skill_text = (directory / "SKILL.md").read_text(
            encoding="utf-8", errors="ignore")
        documented_environment = _documented_environment(skill_text)
        helpers = []
        for path in _documented_helpers(directory, skill_text):
            relative = path.relative_to(directory).as_posix()
            helper_name = _tool_name(name, relative, used)
            helpers.append({
                "name": helper_name,
                "description": (
                    f"Run the operator-registered {name} helper {relative}. "
                    "Pass argument tokens only; the executable identity is fixed."),
                "observation": True,
                "effect": True,
                "effect_return": True,
                "receipt_role": "data",
                "outputSchema": {"type": "string"},
                # Helper identity is fixed and execution is workspace-confined.
                # The semantic position covers task-local argv formatting; URL,
                # email and mention atoms remain subject to WRAP atom checks.
                "argument_types": {"argv": "code/argv"},
                "inputSchema": {
                    "type": "object",
                    "properties": {"argv": {
                        "type": "array", "items": {"type": "string"},
                        "default": [],
                        "x-task-derived": True}},
                    "required": [], "additionalProperties": False,
                },
                "implementation": {
                    "kind": "skill_helper",
                    "runtime": _EXECUTABLE_SUFFIXES[path.suffix.lower()],
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    **({"environment": documented_environment}
                       if documented_environment else {}),
                },
            })
        skills[name] = {
            "name": name,
            "description": (_frontmatter(skill_text, "description") or
                            f"Installed {name} Skill.")[:500],
            "tools": [tool["name"] for tool in TOOLS] +
                     [helper["name"] for helper in helpers],
            "constraints": [
                "All member Tools share one isolated task workspace.",
                "Only digest-pinned helpers from the operator catalog are "
                "registered; runtime package additions are not capabilities.",
            ],
            "helpers": helpers,
        }
    body = {"version": 1, "skills": skills}
    body["sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body


def load_catalog(path=CATALOG) -> dict:
    return load_bundle(path, benchmark="SkillInject")


def load(skill: str, path=CATALOG):
    """Load the final registry for one SkillInject skill environment."""
    return load_environment(path, str(skill), benchmark="SkillInject")


def skill_registration(name: str, path=CATALOG) -> tuple[list[dict], dict]:
    plan = load_environment(path, str(name), benchmark="SkillInject")
    tools = [
        {
            "name": item.name, "description": item.description,
            "inputSchema": {
                "type": "object",
                "properties": dict(item.argument_schemas),
                "required": list(item.required_arguments),
                "additionalProperties": False,
            },
            "outputSchema": item.output_schema,
            "effect": item.effect, "observation": item.observation,
            "effect_return": item.effect_return,
            "receipt_role": item.receipt_role,
            "argument_types": dict(item.argument_types),
            "output_types": dict(item.output_types),
        }
        for item in plan.capabilities.values()
    ]
    manifest = plan.skills[str(name)].to_dict()
    return tools, manifest


def skill_manifest(name: str) -> dict:
    """Backward-compatible manifest lookup used by adapters and tests."""
    return skill_registration(name)[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a trusted SkillInject manifest catalog")
    parser.add_argument("--trusted-root", required=True)
    parser.add_argument("--tasks", help="SkillInject JSON used to select Skills")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    names = None
    if args.tasks:
        rows = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("SkillInject tasks must be a list")
        names = {
            _TASK_SKILL_ALIASES.get(str(task["skill"]), str(task["skill"]))
            for row in rows
            for task in ([row] if isinstance(row, dict) and "skill" in row
                         else row.get("tasks", ()) if isinstance(row, dict)
                         else ())
        }
    catalog = generate_catalog(args.trusted_root, skill_names=names)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print(json.dumps({"skills": len(catalog["skills"]),
                      "helpers": sum(len(row["helpers"])
                                     for row in catalog["skills"].values()),
                      "sha256": catalog["sha256"]}))


if __name__ == "__main__":
    main()
