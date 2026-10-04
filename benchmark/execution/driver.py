"""Common case-to-runtime driver contract for all six benchmarks."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from importlib import import_module
import json
from pathlib import Path
from threading import Lock
from typing import Callable

from apex.core.result_io import as_dict, parse_result
from apex.core.types import EpisodeResult, RunRequest
from apex.defender.engine import Engine
from apex.runtime import ProtectedRuntime
from benchmark.adapter.base import BenchmarkCase
from benchmark.registry import module_for


def load_case_plan(benchmark: str, case: BenchmarkCase):
    """Resolve a case only through its operator-owned registry binding."""
    key = benchmark.lower()
    registry = module_for(key)
    if key in {"agentdojo", "asb_opi", "mcptox"}:
        return registry.load(case.suite)
    if key == "msb":
        return registry.load(str(case.payload["tool"]))
    if key == "skillinject":
        return registry.load(str(case.payload["task"]["skill"]))
    if key == "scr":
        identifier = case.payload["upstream_case"]
        return registry.load(
            case.suite, identifier, condition=case.condition)
    raise ValueError(f"unknown benchmark registry: {benchmark}")


@dataclass(frozen=True)
class CaseContext:
    """Stable input handed to a benchmark-native execution driver."""

    benchmark: str
    method: str
    case: BenchmarkCase
    plan: object
    request: RunRequest
    driver_args: tuple[str, ...] = ()

    def protected_runtime(self, task: str, *, engine: Engine | None = None,
                          task_id: str | None = None, effect_entries=None,
                          approver=None) -> ProtectedRuntime:
        """Create APEX with a newly generated Contract for this case."""
        runtime_engine = engine or Engine(
            self.request.defense_model or self.request.target_model)
        return ProtectedRuntime.for_task(
            runtime_engine, self.plan, task,
            task_id=task_id or self.case.case_id,
            effect_entries=effect_entries, approver=approver)


def _load_driver(spec: str) -> Callable[[CaseContext], EpisodeResult | dict]:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("driver must use the form 'package.module:callable'")
    target = getattr(import_module(module_name), attribute)
    if not callable(target):
        raise TypeError(f"benchmark driver is not callable: {spec}")
    return target


def _normalize(value, context: CaseContext) -> EpisodeResult:
    if isinstance(value, EpisodeResult):
        result = value
    elif isinstance(value, dict):
        result = parse_result(value)
    else:
        raise TypeError("benchmark driver must return EpisodeResult or dict")
    expected = (
        context.case.benchmark, context.method,
        context.case.case_id, context.case.condition)
    actual = (result.benchmark, result.method, result.case_id, result.split)
    if actual != expected:
        raise ValueError(
            f"driver changed canonical result identity: {actual} != {expected}")
    return result


def _read_checkpoint(path: Path) -> tuple[dict, list[EpisodeResult]]:
    if not path.is_file():
        return {}, []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
        raise ValueError(f"invalid benchmark checkpoint: {path}")
    return dict(raw.get("config") or {}), [
        parse_result(item) for item in raw["results"]]


def _write_checkpoint(path: Path, config: dict,
                      results: list[EpisodeResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "apex-benchmark-results-v1",
        "config": config,
        "results": [as_dict(row) for row in sorted(
            results, key=lambda row: (row.split, row.case_id))],
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    temporary.replace(path)


def run_driver(*, benchmark: str, method: str, adapter,
               request: RunRequest, driver: str,
               driver_args=()) -> list[EpisodeResult]:
    """Run selected canonical cases through a benchmark-native callable."""
    target = Path(request.output)
    cases = list(adapter.cases(request.split))
    if request.limit is not None:
        cases = cases[:request.limit]
    config = {
        "benchmark": benchmark, "method": method,
        "target_model": request.target_model,
        "defense_model": request.defense_model,
        "judge_model": request.judge_model,
        "split": request.split, "limit": request.limit,
        "driver": driver,
    }
    old_config, results = _read_checkpoint(target) if request.resume else ({}, [])
    if old_config and old_config != config:
        raise ValueError("resume configuration differs from checkpoint")
    completed = {(row.split, row.case_id) for row in results}
    selected = [case for case in cases
                if (case.condition, case.case_id) not in completed]
    callable_driver = _load_driver(driver)
    write_lock = Lock()

    def execute(case):
        context = CaseContext(
            benchmark, method, case, load_case_plan(benchmark, case), request,
            tuple(driver_args))
        return _normalize(callable_driver(context), context)

    with ThreadPoolExecutor(max_workers=max(1, request.workers)) as pool:
        futures = {pool.submit(execute, case): case for case in selected}
        for future in as_completed(futures):
            row = future.result()
            with write_lock:
                results.append(row)
                _write_checkpoint(target, config, results)
    if not selected:
        _write_checkpoint(target, config, results)
    return results
