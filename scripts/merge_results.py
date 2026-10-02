#!/usr/bin/env python3
"""Merge normalized JSON/JSONL shards and compute one canonical summary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from apex.core.aggregation import aggregate
from apex.core.result_io import as_dict, load_results, merge_results


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(text, encoding="utf-8")
    pending.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", help="normalized JSON or JSONL shards")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    rows = merge_results(load_results(args.inputs))
    combinations = {(row.benchmark, row.method) for row in rows}
    if len(combinations) != 1:
        raise ValueError(
            "one merge must contain exactly one benchmark/method pair; got "
            f"{sorted(combinations)}")
    body = "".join(json.dumps(as_dict(row), ensure_ascii=False) + "\n"
                   for row in rows)
    _atomic_write(args.output, body)
    summary_path = args.summary or args.output.with_suffix(".summary.json")
    _atomic_write(summary_path, json.dumps(
        aggregate(rows), ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
