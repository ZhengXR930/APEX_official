"""Compatibility bridge for legacy helpers that bypass UnitBroker.

This module is not part of the UnitBroker contract.  New Tool, MCP, and Skill
frontends must emit UnitInvocation directly; this adapter translates an older
subprocess ABI at the integration boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Callable

from apex.defender.broker import UnitBroker


@dataclass(frozen=True)
class RegisteredCommand:
    action: str
    path: Path
    decode: Callable[[tuple[str, ...]], dict]


@dataclass(frozen=True)
class ResolvedNestedEffect:
    action: str
    arguments: dict
    helper: str


class LegacyNestedEffectAdapter:
    """Translate an exact registered subprocess ABI into UnitInvocation."""

    def __init__(self, broker: UnitBroker, commands, *, on_decision=None,
                 identities=None):
        self.broker = broker
        self.on_decision = on_decision
        self.identities = identities
        entries = tuple(commands)
        paths = [str(Path(entry.path).resolve()) for entry in entries]
        if len(paths) != len(set(paths)):
            raise ValueError("nested helper paths must be unique")
        self.commands = {
            path: RegisteredCommand(str(entry.action), Path(path), entry.decode)
            for path, entry in zip(paths, entries)
        }

    def resolve(self, command) -> ResolvedNestedEffect | None:
        if not isinstance(command, (list, tuple)):
            return None
        argv = tuple(str(item) for item in command)
        matches = []
        for index, item in enumerate(argv):
            try:
                registration = self.commands.get(str(Path(item).resolve()))
            except (OSError, RuntimeError, ValueError):
                continue
            if registration is not None:
                matches.append((index, registration))
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("one child command resolved to multiple helpers")
        index, registration = matches[0]
        arguments = registration.decode(argv[index + 1:])
        if not isinstance(arguments, dict):
            raise TypeError("nested helper ABI decoder must return an object")
        return ResolvedNestedEffect(
            registration.action, dict(arguments), str(registration.path))

    def run(self, native_run, command, *args, **kwargs):
        resolved = self.resolve(command)
        if resolved is None:
            return native_run(command, *args, **kwargs)
        identities = (() if self.identities is None else
                      tuple(self.identities(resolved) or ()))
        result = self.broker.invoke(
            resolved.action, resolved.arguments,
            lambda: native_run(command, *args, **kwargs),
            identities=identities)
        if self.on_decision is not None:
            self.on_decision(resolved, result.decision)
        if result.executed:
            return result.value
        denied = subprocess.CompletedProcess(
            command, 126, stdout="",
            stderr="Blocked by the active defense: " + result.decision.reason)
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(
                denied.returncode, command, output=denied.stdout,
                stderr=denied.stderr)
        return denied

    def subprocess_proxy(self, native_module=subprocess):
        adapter = self

        class Proxy:
            def run(self, command, *args, **kwargs):
                return adapter.run(native_module.run, command, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(native_module, name)

        return Proxy()
