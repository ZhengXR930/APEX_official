"""Trusted benchmark capability manifests."""
from __future__ import annotations

from importlib import import_module

REGISTRIES = {
    "agentdojo": "benchmark.registry.agentdojo",
    "asb_opi": "benchmark.registry.asb_opi",
    "mcptox": "benchmark.registry.mcptox",
    "msb": "benchmark.registry.msb",
    "scr": "benchmark.registry.scr",
    "skillinject": "benchmark.registry.skillinject",
}


def module_for(name: str):
    try:
        return import_module(REGISTRIES[name.lower()])
    except KeyError as exc:
        raise ValueError(f"unknown benchmark registry: {name}") from exc


__all__ = ["REGISTRIES", "module_for"]
