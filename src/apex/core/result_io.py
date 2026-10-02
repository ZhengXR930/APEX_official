"""Validated loading and conflict-safe merging of normalized result shards."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from apex.core.types import EpisodeResult


def _records(path: Path) -> Iterator[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        for line_number, line in enumerate(text.splitlines(), 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{line_number}: expected an object")
                yield value
        return
    value = json.loads(text)
    if isinstance(value, dict):
        value = value.get("results", [value])
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError(f"{path}: expected an object, array, or results array")
    yield from value


def parse_result(raw: Mapping) -> EpisodeResult:
    required = ("benchmark", "method", "case_id", "split")
    missing = [name for name in required if name not in raw]
    if missing:
        raise ValueError(f"result misses required fields: {missing}")
    split = str(raw["split"])
    if split not in {"clean", "attack"}:
        raise ValueError(f"unsupported result split: {split!r}")
    for name in ("utility", "attack_success"):
        if raw.get(name) is not None and not isinstance(raw[name], bool):
            raise ValueError(f"{name} must be boolean or null")
    technical = raw.get("technical_failure", False)
    if not isinstance(technical, bool):
        raise ValueError("technical_failure must be boolean")
    evidence = raw.get("evidence") or {}
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    return EpisodeResult(
        benchmark=str(raw["benchmark"]), method=str(raw["method"]),
        case_id=str(raw["case_id"]), split=split,
        utility=raw.get("utility"), attack_success=raw.get("attack_success"),
        technical_failure=technical, evidence=evidence)


def load_results(paths: Iterable[str | Path]) -> list[EpisodeResult]:
    return [parse_result(row) for path in paths for row in _records(Path(path))]


def merge_results(rows: Iterable[EpisodeResult]) -> list[EpisodeResult]:
    """Deduplicate identical shards and reject conflicting case outcomes."""
    merged: dict[tuple[str, str, str, str], EpisodeResult] = {}
    for row in rows:
        key = (row.benchmark, row.method, row.split, row.case_id)
        previous = merged.get(key)
        if previous is not None and previous != row:
            raise ValueError(f"conflicting duplicate result: {key}")
        merged[key] = row
    return [merged[key] for key in sorted(merged)]


def as_dict(row: EpisodeResult) -> dict:
    return {
        "benchmark": row.benchmark, "method": row.method,
        "case_id": row.case_id, "split": row.split,
        "utility": row.utility, "attack_success": row.attack_success,
        "technical_failure": row.technical_failure,
        "evidence": dict(row.evidence),
    }
