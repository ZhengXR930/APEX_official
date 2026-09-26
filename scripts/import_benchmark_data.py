"""Import the compact benchmark inputs used by adapters and registries.

The script intentionally does not vendor full upstream repositories. It copies
the frozen JSON case descriptors needed by the adapters and derives a compact
SCR case index from a pinned SCR_Bench checkout.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "benchmark" / "data"
REGISTRY_SOURCE = ROOT / "benchmark" / "registry" / "source"
SCR_REPOSITORY = "https://github.com/saint-viperx/SCR_Bench"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _copy_json(source: Path, target: Path) -> None:
    _write(target, _read(source))


def _literal(path: Path, name: str):
    text = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text, filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets):
            return ast.literal_eval(node.value)
        if (isinstance(node, ast.AnnAssign) and
                isinstance(node.target, ast.Name) and node.target.id == name):
            return ast.literal_eval(node.value)
    raise ValueError(f"{path} has no literal assignment for {name}")


def _assignment_text(path: Path, name: str) -> str:
    """Return literal text or source text for a case prompt assignment."""
    text = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text, filename=str(path))
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets):
            value = node.value
        elif (isinstance(node, ast.AnnAssign) and
              isinstance(node.target, ast.Name) and node.target.id == name):
            value = node.value
        if value is not None:
            try:
                return str(ast.literal_eval(value))
            except (TypeError, ValueError):
                return ast.get_source_segment(text, value) or ast.unparse(value)
    raise ValueError(f"{path} has no assignment for {name}")


def _named_skill(prompt: str) -> str:
    match = re.search(r"Use the ['\"]([^'\"]+)['\"] skill", prompt, re.I)
    if not match:
        raise ValueError(f"cannot identify Skill in prompt: {prompt[:100]!r}")
    return match.group(1)


def _frontmatter(path: Path, key: str) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        rf"(?m)^{re.escape(key)}:\s*(?:\"([^\"]*)\"|'([^']*)'|([^\n]+))",
        text,
    )
    if not match:
        return ""
    return next(part.strip() for part in match.groups() if part is not None)


def _sanitize_msb(value):
    if isinstance(value, list):
        return [_sanitize_msb(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_msb(item) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    value = re.sub(
        r"(?:/[A-Za-z0-9_.-]+)+/benchmarks/external/MSB",
        "/workspace/MSB",
        value,
    )
    return value


def import_research_data(research_root: Path) -> None:
    source = research_root / "active_defense" / "code" / "benchmarks"
    if not source.is_dir():
        raise FileNotFoundError(f"benchmark implementation root not found: {source}")

    for name in (
        "banking_pairs.json", "clean_tasks.json", "slack_pairs.json",
        "travel_pairs.json", "workspace_pairs.json",
    ):
        _copy_json(source / "agentdojo" / "data" / name,
                   DATA / "agentdojo" / name)

    for name in ("clean_cases.json", "attack_cases.json"):
        _copy_json(source / "asb_opi" / "data" / name,
                   DATA / "asb_opi" / name)
    _copy_json(source / "asb_opi" / "data" / "manifest.json",
               REGISTRY_SOURCE / "asb_opi" / "manifest.json")

    _copy_json(source / "mcptox" / "data" / "cases.json",
               DATA / "mcptox" / "cases.json")
    _copy_json(source / "mcptox" / "data" / "manifest.json",
               REGISTRY_SOURCE / "mcptox" / "manifest.json")

    msb_cases = _sanitize_msb(_read(source / "msb" / "data" / "cases.json"))
    msb_cases["source_config"] = "upstream:config/full_mcpguard.yml"
    _write(DATA / "msb" / "cases.json", msb_cases)
    _copy_json(source / "msb" / "data" / "tools.json",
               REGISTRY_SOURCE / "msb" / "tools.json")

    for name in ("cases.json", "task_files.json", "tasks.json"):
        _copy_json(source / "skillinject" / "data" / name,
                   DATA / "skillinject" / name)

    capflow = _read(source / "scr" / "data" / "capflow.json")
    _write(REGISTRY_SOURCE / "scr" / "capflow.json", capflow)


def _verify_scr_checkout(scr_root: Path, commit: str) -> None:
    subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=scr_root, check=True, capture_output=True,
    )
    # Dataset files must equal the pinned revision even if the checkout's HEAD
    # contains documentation-only commits.
    result = subprocess.run(
        ["git", "diff", "--quiet", commit, "--", "SCR-CapFlow",
         "SCR-AuthBlur", "SCR-TrustLift"],
        cwd=scr_root,
    )
    if result.returncode != 0:
        raise ValueError(
            "SCR checkout data differs from the pinned benchmark revision")


def _scr_index(scr_root: Path, capflow: dict) -> dict:
    commit = str(capflow["benchmark_commit"])
    _verify_scr_checkout(scr_root, commit)

    capflow_cases = {}
    for case_id, row in sorted(capflow["cases"].items(), key=lambda item: int(item[0])):
        by_role = {tool["x-scr-role"]: tool for tool in row["tools"]}
        if set(by_role) != {"A", "B"}:
            raise ValueError(f"CapFlow case {case_id} does not have A/B roles")
        skills = {
            role: {
                "name": tool["x-skill-name"],
                "description": tool["description"],
                "tools": [tool["name"]],
            }
            for role, tool in by_role.items()
        }
        capflow_cases[case_id] = {
            "skills": skills,
            "conditions": {
                "control": [],
                "A_only": [skills["A"]["name"]],
                "B_only": [skills["B"]["name"]],
                "A+B_neutral": [skills["A"]["name"], skills["B"]["name"]],
                "A+B_explicit": [skills["A"]["name"], skills["B"]["name"]],
            },
        }

    auth_cases = {}
    scripts = scr_root / "SCR-AuthBlur" / "experiment_scripts"
    for path in sorted(scripts.glob("run_experiment_levels_*.py"),
                       key=lambda item: int(item.stem.rsplit("_", 1)[1])):
        case_id = path.stem.rsplit("_", 1)[1]
        level2 = _assignment_text(path, "T1_LEVEL2")
        level3 = _assignment_text(path, "T1_LEVEL3")
        request = _assignment_text(path, "T2_REQUEST")
        plain = _named_skill(level2)
        full = _named_skill(level3)
        control = _named_skill(request)
        skill_root = (scr_root / "SCR-AuthBlur" / "cases" /
                      f"case{case_id}" / "cli_skills" / "skills")
        skills = {}
        missing = []
        for role, name in (("plain_advisor", plain),
                           ("full_advisor", full), ("control", control)):
            skill_file = skill_root / name / "SKILL.md"
            if not skill_file.is_file():
                missing.append(name)
            skills[role] = {
                "name": name,
                "description": (
                    _frontmatter(skill_file, "description")
                    if skill_file.is_file() else ""),
                "present": skill_file.is_file(),
            }
        auth_cases[case_id] = {
            "skills": skills,
            "eligible": not missing,
            "missing_skills": missing,
            "conditions": {
                "level2_findings": [plain, control],
                "level3_fullauth": [full, control],
            },
        }

    trust_cases = {}
    control_root = scr_root / "SCR-TrustLift" / "control-group"
    attack_root = scr_root / "SCR-TrustLift" / "experiment-group"
    control_names = {path.name for path in control_root.iterdir() if path.is_dir()}
    attack_names = {path.name for path in attack_root.iterdir() if path.is_dir()}
    if control_names != attack_names:
        raise ValueError("TrustLift control/experiment case sets differ")
    for name in sorted(attack_names):
        case = attack_root / name
        secure = case / "cli_skills" / "skills" / "secure-skill"
        scripts = {path.stem for path in (secure / "scripts").glob("*.py")}
        tools = [tool for tool in ("scan_skills", "install_skills")
                 if tool in scripts]
        if tools != ["scan_skills", "install_skills"]:
            raise ValueError(f"TrustLift case {name} has unexpected tools: {tools}")
        entries = _read(case / "registry" / "registry.json")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"TrustLift case {name} has no registry entries")
        trust_cases[name] = {
            "target_skill": name,
            "registry_entries": [str(item["name"]) for item in entries],
            "conditions": {
                "clean": ["scan_skills"],
                "attack": tools,
            },
        }

    return {
        "schema": "scr-case-index-v1",
        "source_repository": SCR_REPOSITORY,
        "source_commit": commit,
        "suites": {
            "capflow": {"case_count": len(capflow_cases), "cases": capflow_cases},
            "authblur": {"case_count": len(auth_cases), "cases": auth_cases},
            "trustlift": {"case_count": len(trust_cases), "cases": trust_cases},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--scr-root", type=Path, required=True)
    args = parser.parse_args()
    import_research_data(args.research_root.resolve())
    capflow = _read(REGISTRY_SOURCE / "scr" / "capflow.json")
    _write(DATA / "scr" / "cases.json",
           _scr_index(args.scr_root.resolve(), capflow))


if __name__ == "__main__":
    main()
