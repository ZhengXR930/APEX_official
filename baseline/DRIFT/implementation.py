"""DRIFT AgentDojo pipeline integration."""
from baseline.agentdojo_pipeline import build_pipeline


def build(*, llm, tools, **kwargs):
    return build_pipeline("drift", llm=llm, tools=tools, **kwargs)
