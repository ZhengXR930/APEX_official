from __future__ import annotations

import json

from .base import BenchmarkCase, DatasetAdapter


class ASBOPIAdapter(DatasetAdapter):
    benchmark = "ASB-OPI"
    slug = "asb_opi"

    def cases(self, split=None):
        for condition, filename in (("clean", "clean_cases.json"),
                                    ("attack", "attack_cases.json")):
            if split not in (None, condition):
                continue
            rows = json.loads(self.json_path(filename).read_text(encoding="utf-8"))
            for row in rows:
                yield BenchmarkCase(self.benchmark, str(row["case_id"]),
                                    condition, str(row["agent_name"]), row)
