"""Common interface for converting benchmark releases into evaluation cases."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class BenchmarkCase:
    benchmark: str
    case_id: str
    condition: str
    suite: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    eligible: bool = True


class DatasetAdapter(ABC):
    """Read-only view over one externally supplied benchmark data directory."""

    benchmark: str

    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root).expanduser().resolve()

    def json_path(self, name: str) -> Path:
        path = self.data_root / name
        if not path.is_file():
            raise FileNotFoundError(
                f"{self.benchmark}: expected dataset file {path}"
            )
        return path

    @abstractmethod
    def cases(self, split: str | None = None) -> Iterator[BenchmarkCase]:
        """Yield canonical cases for ``clean``, ``attack``, or both."""
