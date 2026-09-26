"""Adapter for running reference ClawGuard as a benchmark baseline.

The adapter intentionally contains no benchmark labels. It loads the reference
ClawGuard RuleEngine/Sanitizer/SkillChecker and exposes two deterministic
checks used by our MCP/tool benchmark glue.

In non-interactive experiments, ClawGuard ``approve`` outcomes are treated as
blocked because no human approval channel is available.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from pathlib import Path
from typing import Any

import yaml


REPO = Path(__file__).resolve().parents[3]
if not (REPO / "baseline").exists():
    REPO = Path(__file__).resolve().parents[2]
REFERENCE_ROOT = REPO / "baseline/ClawGuard"
RULES = REFERENCE_ROOT / "config/rules.yaml"

_URL_RE = re.compile(r"https?://[^\s'\"<>),]+|(?:[a-z][a-z0-9+.-]*://[^\s'\"<>),]+)", re.I)
_PATH_RE = re.compile(r"(?<![\w.-])(?:~|/)[^\s'\"<>),;]+")
_COMMAND_KEY_RE = re.compile(r"command|cmd|shell|script|process|terminal|exec|query", re.I)
_PATH_KEY_RE = re.compile(r"path|file|folder|directory|dir|filename", re.I)
_WRITE_KEY_RE = re.compile(r"write|create|delete|remove|modify|update|edit|upload|move|copy|install|save", re.I)


def _load_reference():
    if not REFERENCE_ROOT.is_dir():
        raise RuntimeError(f"missing reference ClawGuard checkout: {REFERENCE_ROOT}")
    if str(REFERENCE_ROOT) not in sys.path:
        sys.path.insert(0, str(REFERENCE_ROOT))
    from clawguard.rules import ActionType, RuleEngine
    from clawguard.sanitizer import Sanitizer
    from clawguard.skill_check import SkillChecker
    config = yaml.safe_load(RULES.read_text(encoding="utf-8")) or {}
    sanitizer_cfg = config.get("sanitizer_rules", {})
    return {
        "ActionType": ActionType,
        "rules": RuleEngine(config),
        "sanitizer": Sanitizer(
            patterns=sanitizer_cfg.get("patterns"),
            skill_whitelist=sanitizer_cfg.get("skill_whitelist", []),
            enabled=sanitizer_cfg.get("enabled", True),
        ),
        "skills": SkillChecker(),
        "config_sha256": hashlib.sha256(RULES.read_bytes()).hexdigest(),
    }


def _compact(value: Any, limit: int = 240) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _flatten(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = f"{prefix}.{key}" if prefix else str(key)
            yield from _flatten(item, key_text)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _flatten(item, f"{prefix}[{index}]")
    else:
        yield prefix, "" if value is None else str(value)


class ClawGuardScanner:
    def __init__(self, cache_path: Path | None = None,
                 *, approval_is_deny: bool = True) -> None:
        loaded = _load_reference()
        self.ActionType = loaded["ActionType"]
        self.rules = loaded["rules"]
        self.sanitizer = loaded["sanitizer"]
        self.skills = loaded["skills"]
        self.config_sha256 = loaded["config_sha256"]
        self.approval_is_deny = approval_is_deny
        self.cache_path = cache_path
        self.cache = (
            json.loads(cache_path.read_text(encoding="utf-8"))
            if cache_path and cache_path.exists() else {}
        )
        self.lock = threading.Lock()
        self.new = 0

    def _allowed(self, action: Any) -> bool:
        if action == self.ActionType.DENY:
            return False
        if action == self.ActionType.APPROVE:
            return not self.approval_is_deny
        return True

    def _rule_dict(self, result: Any, kind: str, value: str) -> dict:
        action = getattr(result, "action", None)
        return {
            "kind": kind,
            "value": value[:300],
            "matched": bool(getattr(result, "matched", False)),
            "action": getattr(action, "value", str(action)),
            "allowed": self._allowed(action),
            "reason": str(getattr(result, "reason", "")),
            "rule_type": str(getattr(result, "rule_type", "")),
            "pattern": getattr(result, "pattern", None),
        }

    def _safe(self, kind: str, fn, value: str, *args) -> dict:
        try:
            return self._rule_dict(fn(value, *args), kind, value)
        except Exception as exc:
            return {
                "kind": kind,
                "value": value[:300],
                "matched": False,
                "action": "error_allow",
                "allowed": True,
                "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
                "rule_type": f"{kind}_adapter_error",
            }

    def _cached(self, namespace: str, payload: Any, compute) -> dict:
        digest = hashlib.sha256(
            (namespace + "\0" + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)).encode()
        ).hexdigest()
        with self.lock:
            hit = self.cache.get(digest)
        if hit is not None:
            return dict(hit, cached=True)
        result = compute()
        should_save = False
        with self.lock:
            self.cache[digest] = result
            self.new += 1
            should_save = bool(self.cache_path and self.new % 50 == 0)
        if should_save:
            self.save()
        return result

    def save(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        with self.lock:
            data = dict(self.cache)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)

    def close(self) -> None:
        self.save()

    def scan_catalog_tool(self, tool_name: str, block: str, schema: dict | None = None) -> dict:
        payload = {"tool": tool_name, "block": block, "schema": schema or {}}

        def compute() -> dict:
            sanitized = self.sanitizer.sanitize_input(block, tool_name)
            secret_hits = self.sanitizer.detect_secrets(block)
            checks = [self._safe("command_metadata", self.rules.check_command, block)]
            allowed = all(check.get("allowed", True) for check in checks)
            return {
                "allowed": allowed,
                "sanitized_changed": sanitized != block,
                "sanitized_text": sanitized,
                "secret_hits": [hit.get("type") for hit in secret_hits],
                "checks": checks,
            }

        return self._cached("catalog", payload, compute)

    def scan_observation(self, tool_name: str, text: str, location: str = "observation") -> dict:
        payload = {"tool": tool_name, "location": location, "text": text}

        def compute() -> dict:
            sanitized = self.sanitizer.sanitize_output(text, tool_name)
            checks = [self._safe("command_observation", self.rules.check_command, text)]
            return {
                "allowed": all(check.get("allowed", True) for check in checks),
                "sanitized_changed": sanitized != text,
                "secret_hits": [hit.get("type") for hit in self.sanitizer.detect_secrets(text)],
                "checks": checks,
            }

        return self._cached("observation", payload, compute)

    def scan_call(self, tool_name: str, arguments: dict | None,
                  tool_block: str = "") -> dict:
        arguments = arguments or {}
        payload = {"tool": tool_name, "arguments": arguments, "tool_block": tool_block}

        def compute() -> dict:
            checks = []
            call_text = f"tool={tool_name}\narguments={_compact(arguments, 4000)}"
            if _COMMAND_KEY_RE.search(tool_name):
                checks.append(self._safe("command_call", self.rules.check_command, call_text))
            for key, text in _flatten(arguments):
                key_text = key or "argument"
                if _COMMAND_KEY_RE.search(key_text) or _COMMAND_KEY_RE.search(tool_name):
                    checks.append(self._safe("command_arg", self.rules.check_command, text))
                if _PATH_KEY_RE.search(key_text):
                    for path in _PATH_RE.findall(text):
                        op = "write" if (_WRITE_KEY_RE.search(key_text) or _WRITE_KEY_RE.search(tool_name)) else "read"
                        checks.append(self._safe(f"file_{op}", self.rules.check_file_path, path, op))
                for url in _URL_RE.findall(text):
                    checks.append(self._safe("network_arg", self.rules.check_network, url))
            return {
                "allowed": all(check.get("allowed", True) for check in checks),
                "checks": checks,
            }

        return self._cached("call", payload, compute)
