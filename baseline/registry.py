"""Canonical baseline names and implementation modules."""
from __future__ import annotations

from importlib import import_module

BASELINES = {
    "agentshield": "baseline.AgentShield.implementation",
    "camel": "baseline.CaMeL.implementation",
    "clawguard": "baseline.ClawGuard.implementation",
    "drift": "baseline.DRIFT.implementation",
    "dynamic_guardian": "baseline.DynamicGuardian.implementation",
    "mcp_guard": "baseline.MCPGuard.implementation",
    "melon": "baseline.MELON.implementation",
    "pipelock": "baseline.Pipelock.implementation",
    "progent": "baseline.Progent.implementation",
    "spotlighting": "baseline.Spotlighting.implementation",
    "stackone": "baseline.StackOne.implementation",
    "taskshield": "baseline.TaskShield.implementation",
    "tool_filter": "baseline.ToolFilter.implementation",
}


def load(name: str):
    try:
        return import_module(BASELINES[name.lower()])
    except KeyError as exc:
        raise ValueError(f"unknown baseline: {name}") from exc
