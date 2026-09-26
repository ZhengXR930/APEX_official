"""Lazy integration boundary for AgentDojo pipeline defenses."""
from __future__ import annotations

from typing import Any


def build_pipeline(defense: str, *, llm: Any, tools: list[Any], **kwargs):
    """Build a pipeline while keeping AgentDojo an optional dependency."""
    try:
        from agentdojo.agent_pipeline import AgentPipeline
    except ImportError as exc:
        raise RuntimeError(
            "Install the AgentDojo benchmark dependency to build this pipeline"
        ) from exc
    if hasattr(AgentPipeline, "from_config"):
        return AgentPipeline.from_config(
            llm=llm, tools=tools, defense=defense, **kwargs
        )
    raise RuntimeError("The installed AgentDojo version lacks from_config()")
