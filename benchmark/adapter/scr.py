from __future__ import annotations

from .base import BenchmarkCase, DatasetAdapter


class SCRAdapter(DatasetAdapter):
    benchmark = "SCR"
    slug = "scr"

    def cases(self, split=None):
        import json

        index = json.loads(self.json_path("cases.json").read_text())
        condition_map = {
            "capflow": {"clean": "B_only", "attack": "A+B_neutral"},
            "authblur": {
                "clean": "level2_findings", "attack": "level3_fullauth"},
            "trustlift": {"clean": "clean", "attack": "attack"},
        }
        for suite, suite_data in index["suites"].items():
            for identifier, metadata in suite_data["cases"].items():
                for condition in ("clean", "attack"):
                    if split not in (None, condition):
                        continue
                    yield BenchmarkCase(
                        self.benchmark, f"{suite}:{identifier}", condition,
                        suite,
                        {"upstream_case": identifier,
                         "native_condition": condition_map[suite][condition]},
                        bool(metadata.get("eligible", True)),
                    )
