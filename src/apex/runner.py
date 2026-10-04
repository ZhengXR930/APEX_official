"""Method-neutral benchmark runner entry point."""
from apex.core.runner import MethodRunner

RUNNER = MethodRunner("ours")


def runner_for(method: str) -> MethodRunner:
    if not str(method).strip():
        raise ValueError("method name cannot be empty")
    return MethodRunner(str(method))
