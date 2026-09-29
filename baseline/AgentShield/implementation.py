"""AgentShield parameter-level action validator."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


class AgentShield:
    """Validate action parameters against the trusted task and tool schema."""

    def __init__(self, allowed_domains: tuple[str, ...] = ()):
        self.allowed_domains = frozenset(domain.lower() for domain in allowed_domains)

    def check(self, task: str, tool: dict[str, Any], arguments: dict[str, Any]) -> Decision:
        schema = tool.get("inputSchema") or {}
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or ())
        missing = required - arguments.keys()
        unknown = arguments.keys() - properties.keys()
        if missing:
            return Decision(False, f"missing required parameters: {sorted(missing)}")
        if unknown and schema.get("additionalProperties") is False:
            return Decision(False, f"unknown parameters: {sorted(unknown)}")
        task_words = set(re.findall(r"[a-z0-9_.@/-]+", task.lower()))
        for name, value in arguments.items():
            text = str(value).strip()
            if properties.get(name, {}).get("type") == "string" and not text:
                return Decision(False, f"empty parameter: {name}")
            parsed = urlparse(text)
            if parsed.scheme in {"http", "https"}:
                host = (parsed.hostname or "").lower()
                if self.allowed_domains and host not in self.allowed_domains:
                    return Decision(False, f"unapproved URL domain: {host}")
            if properties.get(name, {}).get("x-task-derived"):
                value_words = set(re.findall(r"[a-z0-9_.@/-]+", text.lower()))
                if value_words and not value_words <= task_words:
                    return Decision(False, f"parameter not grounded in task: {name}")
        return Decision(True, "parameter policy satisfied")
