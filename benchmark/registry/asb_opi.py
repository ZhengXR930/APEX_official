"""ASB-OPI trusted tool-manifest loader."""
from __future__ import annotations

from pathlib import Path

from benchmark.registry.schema import load_environment

DEFAULT_PATH = Path(__file__).with_name("data") / "asb_opi" / "manifest.json"


def load(agent: str, path: str | Path = DEFAULT_PATH):
    return load_environment(path, agent, benchmark="ASB-OPI")
