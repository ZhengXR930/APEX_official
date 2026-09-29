"""Deterministically summarize one installed Skill's deployable surfaces.

The compiler is intentionally syntactic.  It reads ``SKILL.md`` and resolves
only files that the document names explicitly.  It does not infer dataflow,
authority, or a task workflow.  Runtime adapters separately declare which
candidate identities they can actually deploy and monitor.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re


_EXECUTABLES = {".py", ".sh", ".js", ".mjs"}
_STRUCTURED = {".json", ".yaml", ".yml"}
_FILE_REF = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.(?:py|sh|js|mjs|json|yaml|yml))"
    r"(?![A-Za-z0-9_.-])",
    flags=re.IGNORECASE,
)


def _frontmatter(text: str, key: str) -> str:
    match = re.search(
        rf"(?m)^{re.escape(key)}:\s*(?:\"([^\"]*)\"|'([^']*)'|([^\n]+))",
        text,
    )
    if match is None:
        return ""
    return next((part.strip() for part in match.groups()
                 if part is not None), "")


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:/-]+", "-", value).strip("-")


@dataclass(frozen=True)
class SkillSurfaceCard:
    """One syntactically witnessed package surface.

    ``path`` is code-private evidence.  The Placement Agent receives only the
    compact id/kind/summary card, never the package tree or source code.
    """

    id: str
    kind: str  # helper | state
    summary: str
    path: str

    def prompt_dict(self, *, artifact: bool = False) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "modes": ["artifact"] if artifact else [],
        }


@dataclass(frozen=True)
class SkillLayout:
    name: str
    digest: str
    instructions: str
    instruction_source: str
    cards: tuple[SkillSurfaceCard, ...]

    def placement_cards(self, *, artifact_ids=()) -> tuple[dict, ...]:
        supported = frozenset(map(str, artifact_ids or ()))
        return tuple(card.prompt_dict(artifact=card.id in supported)
                     for card in self.cards)

    def card(self, card_id: str) -> SkillSurfaceCard:
        for card in self.cards:
            if card.id == str(card_id):
                return card
        raise KeyError(card_id)


class SkillSurfaceCompiler:
    """Compile a small, cacheable layout from trusted package structure."""

    def __init__(self):
        self._cache: dict[tuple[str, str], SkillLayout] = {}

    def compile(self, skill_file, *, environment_root=None) -> SkillLayout:
        skill_file = Path(skill_file).resolve()
        if not skill_file.is_file():
            raise FileNotFoundError(skill_file)
        instructions = skill_file.read_text(encoding="utf-8", errors="ignore")
        environment = (Path(environment_root).resolve()
                       if environment_root is not None else skill_file.parent)
        cache_key = (str(skill_file), hashlib.sha256(
            instructions.encode()).hexdigest())
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        name = _frontmatter(instructions, "name") or skill_file.parent.name
        references = tuple(dict.fromkeys(
            match.group(1) for match in _FILE_REF.finditer(instructions)))
        helpers = self._resolve_references(
            references, skill_file.parent, skill_file.parent, _EXECUTABLES)
        states = self._resolve_references(
            references, skill_file.parent, environment, _STRUCTURED)

        cards: list[SkillSurfaceCard] = []
        for path in helpers:
            relative = path.relative_to(skill_file.parent).as_posix()
            cards.append(SkillSurfaceCard(
                f"skill:{_safe_id(name)}:helper:{_safe_id(relative)}",
                "helper",
                f"Executable helper explicitly referenced by SKILL.md: {relative}.",
                str(path),
            ))
        for path in states:
            relative = self._relative(path, environment)
            shape = self._structured_shape(path)
            cards.append(SkillSurfaceCard(
                f"skill:{_safe_id(name)}:state:{_safe_id(relative)}",
                "state",
                f"Structured {shape} explicitly referenced by SKILL.md: {relative}.",
                str(path),
            ))

        evidence = hashlib.sha256()
        evidence.update(instructions.encode())
        for card in cards:
            evidence.update(card.id.encode())
            if card.kind == "helper":
                evidence.update(Path(card.path).read_bytes())
            else:
                evidence.update(card.summary.encode())
        layout = SkillLayout(
            name,
            "skill-" + evidence.hexdigest()[:16],
            instructions,
            f"skill:{_safe_id(name)}:instructions",
            tuple(cards),
        )
        self._cache[cache_key] = layout
        return layout

    @staticmethod
    def _relative(path: Path, root: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.name

    @staticmethod
    def _resolve_references(references, package: Path, search_root: Path,
                            suffixes: set[str]) -> tuple[Path, ...]:
        """Resolve only documented files; an unreferenced addition is ignored."""
        found: list[Path] = []
        by_name: dict[str, list[Path]] | None = None
        for reference in references:
            if Path(reference).suffix.lower() not in suffixes:
                continue
            direct = (package / reference).resolve()
            candidates = [direct] if direct.is_file() else []
            if not candidates:
                root_direct = (search_root / reference).resolve()
                if root_direct.is_file():
                    candidates = [root_direct]
            if not candidates:
                if by_name is None:
                    by_name = {}
                    for path in search_root.rglob("*"):
                        if path.is_file() and path.suffix.lower() in suffixes:
                            by_name.setdefault(path.name, []).append(path.resolve())
                matches = by_name.get(Path(reference).name, ())
                # A basename reference is accepted only when the environment
                # resolves it uniquely.  Ambiguity is not delegated to an LLM.
                if len(matches) == 1:
                    candidates = [matches[0]]
            for path in candidates:
                if path not in found:
                    found.append(path)
        return tuple(sorted(found, key=str))

    @staticmethod
    def _structured_shape(path: Path) -> str:
        if path.suffix.lower() != ".json":
            return "YAML state"
        try:
            value = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            return "JSON state"
        if isinstance(value, list):
            keys = sorted(set.intersection(*(
                set(item) for item in value[:16] if isinstance(item, dict)
            ))) if value and all(isinstance(item, dict) for item in value[:16]) else []
            suffix = f" collection with fields {', '.join(keys[:8])}" if keys else " collection"
            return "JSON" + suffix
        if isinstance(value, dict):
            keys = ", ".join(sorted(map(str, value))[:8])
            return "JSON object" + (f" with fields {keys}" if keys else "")
        return "JSON value"
