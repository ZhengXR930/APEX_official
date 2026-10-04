"""Common interface for converting benchmark releases into evaluation cases."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any, Iterator

from apex.core.adapter import BenchmarkAdapter
from apex.core.protocol import load_protocol
from apex.core.types import RunRequest


@dataclass(frozen=True)
class BenchmarkCase:
    benchmark: str
    case_id: str
    condition: str
    suite: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    eligible: bool = True


class DatasetAdapter(BenchmarkAdapter, ABC):
    """Data and execution boundary shared by every public benchmark adapter."""

    benchmark: str
    slug: str

    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root).expanduser().resolve()
        slug = getattr(self, "slug", self.__class__.__module__.rsplit(".", 1)[-1])
        self.protocol_path = (
            Path(__file__).resolve().parents[1] /
            "protocol" / slug / "protocol.json")
        self.protocol = load_protocol(self.protocol_path)

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

    def require_method(self, method: str) -> None:
        self.protocol.require_method(method)

    def command(self, method: str, request: RunRequest) -> list[str]:
        """Build the public, benchmark-neutral execution command."""
        self.require_method(method)
        command = [
            sys.executable, "-m", "benchmark.execution", "run",
            "--benchmark", self.slug,
            "--method", method,
            "--target-model", request.target_model,
            "--output", str(request.output),
            "--workers", str(request.workers),
            "--data-root", str(self.data_root),
        ]
        if request.defense_model:
            command.extend(["--defense-model", request.defense_model])
        if request.judge_model:
            command.extend(["--judge-model", request.judge_model])
        if request.split:
            command.extend(["--split", request.split])
        if request.limit is not None:
            command.extend(["--limit", str(request.limit)])
        if request.driver:
            command.extend(["--driver", request.driver])
        if request.resume:
            command.append("--resume")
        if request.preflight:
            command.append("--preflight")
        return [*command, *request.extra]
