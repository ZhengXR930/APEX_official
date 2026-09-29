from __future__ import annotations

import json

from .base import BenchmarkCase, DatasetAdapter


class MSBAdapter(DatasetAdapter):
    benchmark = "MSB"
    _excluded = frozenset({
        "false_error", "simulated_user", "prompt_injection-simulated_user",
        "prompt_injection-false_error",
    })

    def cases(self, split=None):
        if split not in (None, "attack"):
            return
        bundle = json.loads(self.json_path("cases.json").read_text())
        for row in bundle["cases"]:
            yield BenchmarkCase(
                self.benchmark, str(row["case_id"]), "attack",
                str(row["attack_type"]), row,
                row["attack_type"] not in self._excluded)
