from __future__ import annotations

import json
from pathlib import Path

from .base import BenchmarkCase, DatasetAdapter


class AgentDojoAdapter(DatasetAdapter):
    benchmark = "AgentDojo"
    slug = "agentdojo"

    def __init__(self, data_root=None):
        packaged = Path(__file__).resolve().parents[1] / "data" / "agentdojo"
        super().__init__(data_root or packaged)

    def cases(self, split=None):
        if split in (None, "clean"):
            suites = json.loads(self.json_path("clean_tasks.json").read_text())
            for suite, tasks in suites.items():
                for task in tasks:
                    yield BenchmarkCase(self.benchmark, f"{suite}:{task}",
                                        "clean", suite, {"task": task})
        if split in (None, "attack"):
            for path in sorted(self.data_root.glob("*_pairs.json")):
                suite = (path.stem[:-6] if path.stem.endswith("_pairs")
                         else path.stem)
                for task, injection in json.loads(path.read_text()):
                    yield BenchmarkCase(
                        self.benchmark, f"{suite}:{task}:{injection}",
                        "attack", suite,
                        {"task": task, "injection": injection})
