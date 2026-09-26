"""Small immutable types shared by every benchmark and method."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class EvalCase:
    benchmark: str
    case_id: str
    split: str
    suite: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    utility_eligible: bool = True


@dataclass(frozen=True)
class RunRequest:
    target_model: str
    defense_model: str | None
    judge_model: str | None
    output: Path
    workers: int = 1
    resume: bool = False
    extra: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpisodeResult:
    benchmark: str
    method: str
    case_id: str
    split: str
    utility: bool | None
    attack_success: bool | None
    technical_failure: bool = False
    evidence: Mapping[str, Any] = field(default_factory=dict)
