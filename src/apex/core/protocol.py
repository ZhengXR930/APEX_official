"""Frozen benchmark protocol loading and integrity checks."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkProtocol:
    path: Path
    raw: dict[str, Any]

    @property
    def benchmark(self) -> str:
        return str(self.raw["benchmark"])

    @property
    def methods(self) -> tuple[str, ...]:
        return tuple(self.raw["methods"])

    @property
    def denominators(self) -> dict[str, Any]:
        return dict(self.raw["denominators"])

    def require_method(self, method: str) -> None:
        if method not in self.methods:
            raise ProtocolError(
                f"{method!r} is not part of frozen {self.benchmark} protocol")

    def validate_data(self) -> None:
        base = self.path.parent
        for relative, expected in self.raw.get("data_sha256", {}).items():
            target = base / relative
            if not target.is_file():
                raise ProtocolError(f"missing frozen data file: {target}")
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != expected:
                raise ProtocolError(
                    f"data drift for {target}: expected {expected}, got {actual}")


def load_protocol(path: str | Path) -> BenchmarkProtocol:
    target = Path(path).resolve()
    raw = json.loads(target.read_text(encoding="utf-8"))
    required = {"schema", "benchmark", "methods", "denominators", "data_sha256"}
    missing = required.difference(raw)
    if missing:
        raise ProtocolError(f"{target} misses protocol fields: {sorted(missing)}")
    if raw["schema"] != "benchmark-evaluation-protocol-v1":
        raise ProtocolError(f"unsupported protocol schema in {target}")
    protocol = BenchmarkProtocol(target, raw)
    protocol.validate_data()
    return protocol


def validate_result_coverage(
    protocol: BenchmarkProtocol,
    result_root: str | Path,
    configuration: str = "DeepSeek",
) -> dict[str, Any]:
    """Check canonical result coverage against the frozen protocol.

    Result metadata must expose an explicit ``coverage`` object.  We do not
    infer denominators from filenames, prose notes, or detector-only counts.
    This keeps a partial run from silently becoming a full benchmark result.
    """
    root = Path(result_root)
    benchmark_root = root / protocol.benchmark
    metadata_by_method: dict[str, Path] = {}
    if benchmark_root.is_dir():
        for metadata in benchmark_root.glob(f"*/{configuration}/METADATA.json"):
            try:
                value = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            method = _method_key(str(value.get("method", metadata.parent.parent.name)))
            metadata_by_method[method] = metadata

    suite_methods = protocol.raw.get("suite_methods")
    methods: dict[str, Any] = {}
    for method in protocol.methods:
        metadata = metadata_by_method.get(_method_key(method))
        if metadata is None:
            methods[method] = {"status": "MISSING", "metadata": None}
            continue
        value = json.loads(metadata.read_text(encoding="utf-8"))
        observed = value.get("coverage")
        if not isinstance(observed, dict):
            methods[method] = {
                "status": "MISSING_COVERAGE",
                "metadata": str(metadata),
            }
            continue

        if suite_methods:
            expected = {
                suite: protocol.denominators[suite]
                for suite, allowed in suite_methods.items()
                if method in allowed
            }
        else:
            expected = protocol.denominators
        mismatches = _coverage_mismatches(expected, observed)
        methods[method] = {
            "status": "PASS" if not mismatches else "DENOMINATOR_MISMATCH",
            "metadata": str(metadata),
            "expected": expected,
            "observed": observed,
            "mismatches": mismatches,
        }
    return {
        "benchmark": protocol.benchmark,
        "configuration": configuration,
        "status": "PASS" if all(x["status"] == "PASS" for x in methods.values()) else "FAIL",
        "methods": methods,
    }


def _method_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _coverage_mismatches(expected: dict[str, Any], observed: dict[str, Any]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []

    def walk(prefix: tuple[str, ...], wanted: Any, actual: Any) -> None:
        if isinstance(wanted, dict):
            if not isinstance(actual, dict):
                mismatches.append({"field": ".".join(prefix), "expected": wanted, "observed": actual})
                return
            for key, value in wanted.items():
                walk((*prefix, key), value, actual.get(key))
            return
        if wanted != actual:
            mismatches.append({"field": ".".join(prefix), "expected": wanted, "observed": actual})

    walk((), expected, observed)
    return mismatches
