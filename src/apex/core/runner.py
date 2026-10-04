"""Method runner contract; benchmark adapters remain the sole data owners."""
from __future__ import annotations

import subprocess

from apex.core.adapter import BenchmarkAdapter
from apex.core.types import RunRequest


class MethodRunner:
    def __init__(self, method: str):
        self.method = method

    def command(self, adapter: BenchmarkAdapter, request: RunRequest) -> list[str]:
        adapter.require_method(self.method)
        return adapter.command(self.method, request)

    def run(self, adapter: BenchmarkAdapter, request: RunRequest) -> None:
        subprocess.run(self.command(adapter, request), check=True)


# Kept for callers of the first public artifact. The runner is method-neutral;
# each benchmark protocol decides whether a method is applicable.
BaselineRunner = MethodRunner
