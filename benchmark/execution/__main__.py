"""Command-line entry point for public benchmark integrations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from apex.core.types import RunRequest
from benchmark.adapter import ADAPTERS, adapter_for

from .driver import run_driver
from .preflight import preflight


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(prog="apex-benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect", help="validate packaged cases, protocols, and registries")
    inspect_parser.add_argument(
        "--benchmark", default="all", choices=("all", *ADAPTERS))
    inspect_parser.add_argument("--samples", type=int, default=2)
    inspect_parser.add_argument("--data-root", type=Path)
    inspect_parser.add_argument("--output", type=Path)

    run_parser = subparsers.add_parser(
        "run", help="run canonical cases through a benchmark-native driver")
    run_parser.add_argument("--benchmark", required=True, choices=tuple(ADAPTERS))
    run_parser.add_argument("--method", required=True)
    run_parser.add_argument("--target-model", required=True)
    run_parser.add_argument("--defense-model")
    run_parser.add_argument("--judge-model")
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--workers", type=int, default=1)
    run_parser.add_argument("--split", choices=("clean", "attack"))
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--data-root", type=Path)
    run_parser.add_argument("--driver")
    run_parser.add_argument("--resume", action="store_true")
    run_parser.add_argument(
        "--preflight", action="store_true",
        help="run the offline APEX boundary check instead of model episodes")

    args, driver_args = parser.parse_known_args()
    if args.command == "inspect":
        names = tuple(ADAPTERS) if args.benchmark == "all" else (args.benchmark,)
        if args.data_root is not None and len(names) != 1:
            parser.error("--data-root requires one benchmark")
        value = [preflight(
            name, data_root=args.data_root, samples=args.samples)
                 for name in names]
        if args.output:
            _write(args.output, value)
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return

    adapter = adapter_for(args.benchmark, args.data_root)
    adapter.require_method(args.method)
    if args.preflight:
        value = preflight(
            args.benchmark, data_root=args.data_root,
            samples=args.limit or 2)
        _write(args.output, value)
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return
    if not args.driver:
        parser.error(
            "native execution requires --driver package.module:callable; "
            "use --preflight for the dependency-free integration check")
    request = RunRequest(
        target_model=args.target_model,
        defense_model=args.defense_model,
        judge_model=args.judge_model,
        output=args.output,
        workers=args.workers,
        resume=args.resume,
        split=args.split,
        limit=args.limit,
        driver=args.driver,
        extra=tuple(driver_args),
    )
    results = run_driver(
        benchmark=args.benchmark, method=args.method, adapter=adapter,
        request=request, driver=args.driver, driver_args=driver_args)
    print(json.dumps({"status": "PASS", "results": len(results)}))


if __name__ == "__main__":
    main()
