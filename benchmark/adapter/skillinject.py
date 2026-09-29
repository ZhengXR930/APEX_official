from __future__ import annotations

import json

from .base import BenchmarkCase, DatasetAdapter


class SkillInjectAdapter(DatasetAdapter):
    benchmark = "SkillInject"

    def cases(self, split=None):
        rows = json.loads(self.json_path("cases.json").read_text())
        for row in rows:
            for index, task in enumerate(row.get("tasks") or ()):
                for condition in ("clean", "attack"):
                    if split not in (None, condition):
                        continue
                    yield BenchmarkCase(
                        self.benchmark,
                        f"id{int(row['id']):03d}:task{index}", condition,
                        payload={"row": row, "task": task})
