"""Trusted MCPTox manifest location and loader."""

from pathlib import Path
import re

from benchmark.registry.schema import load_environment

DEFAULT_PATH = Path(__file__).with_name("data") / "mcptox" / "manifest.json"


def load(server: str, path=DEFAULT_PATH):
    return load_environment(path, server, benchmark="MCPTox")


def canonical(server: str, name: str) -> str:
    clean = lambda text: re.sub(
        r"[^A-Za-z0-9_-]+", "_", str(text)).strip("_")
    return clean(server) + "__" + clean(name)


def registration(server: str, tool: dict, *, effect=True,
                 observation=True) -> dict:
    """Register one complete mediated MCP boundary."""
    return {
        "name": canonical(server, tool["name"]),
        "description": tool["description"],
        "effect": bool(effect), "observation": bool(observation),
        "inputSchema": tool["inputSchema"],
        "outputSchema": {"type": "string"},
        "argument_types": dict(tool.get("argument_types") or {}),
        "output_types": dict(tool.get("output_types") or {}),
        "receipt_role": str(tool.get("receipt_role", "data")),
        "effect_return": bool(effect and observation),
    }
