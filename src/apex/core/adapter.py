"""The one benchmark-adapter interface used by every method."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from apex.core.protocol import BenchmarkProtocol, load_protocol
from apex.core.types import EvalCase, RunRequest


class BenchmarkAdapter(ABC):
    """Normalize one benchmark; never contain defense-specific policy."""

    protocol_path: Path

    def __init__(self) -> None:
        self.protocol: BenchmarkProtocol = load_protocol(self.protocol_path)

    @abstractmethod
    def cases(self, split: str | None = None) -> Iterable[EvalCase]:
        raise NotImplementedError

    @abstractmethod
    def command(self, method: str, request: RunRequest) -> list[str]:
        """Build the benchmark-native invocation for one registered method."""
        raise NotImplementedError

    def require_method(self, method: str) -> None:
        self.protocol.require_method(method)
