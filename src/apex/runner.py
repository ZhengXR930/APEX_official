"""APEX runner entry point."""
from apex.core.runner import BaselineRunner

RUNNER = BaselineRunner("ours")


def runner_for(method: str) -> BaselineRunner:
    if method != "ours":
        raise ValueError(f"unknown active-defense method: {method}")
    return BaselineRunner(method)
