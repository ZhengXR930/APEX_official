"""Verify that packaged cases resolve to final registry environments."""
from __future__ import annotations

import json
from pathlib import Path

from benchmark.adapter import ADAPTERS, adapter_for
from benchmark.registry.schema import load_bundle


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    report = {}
    for name in ADAPTERS:
        cases = list(adapter_for(name, None).cases())
        manifest = ROOT / "benchmark" / "registry" / "data" / name / "manifest.json"
        bundle = load_bundle(manifest)
        case_ids = {case.case_id for case in cases}
        bindings = set(bundle["case_bindings"])
        missing = sorted(case_ids - bindings)
        stale = sorted(bindings - case_ids)
        if missing or stale:
            raise ValueError(
                f"{name}: missing bindings={missing[:5]}, stale bindings={stale[:5]}")
        report[name] = {
            "rows": len(cases),
            "logical_cases": len(case_ids),
            "eligible_rows": sum(case.eligible for case in cases),
            "capability_units": len(bundle["capability_units"]),
            "environments": len(bundle["environments"]),
            "case_bindings": len(bindings),
        }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
