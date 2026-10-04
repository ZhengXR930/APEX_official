"""Trusted MCPTox manifest location and loader."""

from pathlib import Path
import re

from benchmark.registry.schema import load_bundle, load_environment

DEFAULT_PATH = Path(__file__).with_name("data") / "mcptox" / "manifest.json"


def load(server: str, path=DEFAULT_PATH):
    """Load one server, accepting the dataset's display-name spelling.

    The official MCPTox JSON uses labels such as ``Claude Post`` and
    ``OP.GG`` while the audited registry uses filesystem-safe environment
    identifiers. Resolution is unique and purely syntactic; no task or attack
    content participates.
    """
    raw = load_bundle(path, benchmark="MCPTox")
    requested = _environment_key(server)
    matches = [name for name in raw["environments"]
               if _environment_key(name) == requested]
    if len(matches) != 1:
        raise KeyError(
            f"MCPTox server {server!r} resolves to {len(matches)} registries")
    return load_environment(path, matches[0], benchmark="MCPTox")


def _environment_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")


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
