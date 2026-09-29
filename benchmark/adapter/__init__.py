"""Dataset adapter registry."""
from __future__ import annotations

from .agentdojo import AgentDojoAdapter
from .asb_opi import ASBOPIAdapter
from .mcptox import MCPToxAdapter
from .msb import MSBAdapter
from .scr import SCRAdapter
from .skillinject import SkillInjectAdapter

ADAPTERS = {
    "agentdojo": AgentDojoAdapter,
    "asb_opi": ASBOPIAdapter,
    "mcptox": MCPToxAdapter,
    "msb": MSBAdapter,
    "scr": SCRAdapter,
    "skillinject": SkillInjectAdapter,
}


def adapter_for(name, data_root):
    key = name.lower()
    try:
        adapter = ADAPTERS[key]
    except KeyError as exc:
        raise ValueError(f"unknown benchmark adapter: {name}") from exc
    if data_root is None:
        from pathlib import Path
        data_root = Path(__file__).resolve().parents[1] / "data" / key
    return adapter(data_root)


__all__ = ["ADAPTERS", "adapter_for"]
