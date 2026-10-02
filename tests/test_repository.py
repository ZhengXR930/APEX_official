from pathlib import Path
import json

from benchmark.adapter import ADAPTERS
from baseline.registry import BASELINES
from apex.core.protocol import load_protocol


ROOT = Path(__file__).resolve().parents[1]


def test_expected_benchmarks_are_registered():
    assert set(ADAPTERS) == {
        "agentdojo", "asb_opi", "mcptox", "msb", "scr", "skillinject"
    }


def test_bundled_agentdojo_adapter():
    from benchmark.adapter import adapter_for

    counts = {}
    for condition in ("clean", "attack"):
        counts[condition] = len(list(adapter_for("agentdojo", None).cases(condition)))
    assert counts == {"clean": 97, "attack": 629}


def test_all_benchmark_adapters_have_packaged_case_data():
    from benchmark.adapter import adapter_for

    expected = {
        "agentdojo": (726, 726),
        "asb_opi": (2091, 2091),
        "mcptox": (1705, 1705),
        "msb": (622, 415),
        "scr": (1338, 1334),
        "skillinject": (360, 360),
    }
    for name, (total, eligible) in expected.items():
        rows = list(adapter_for(name, None).cases())
        assert len(rows) == total
        assert sum(row.eligible for row in rows) == eligible


def test_each_baseline_has_its_own_folder_and_implementation():
    assert len(BASELINES) == 13
    for module_name in BASELINES.values():
        folder = module_name.split(".")[1]
        assert (ROOT / "baseline" / folder / "implementation.py").is_file()


def test_registry_manifests_are_present():
    data = ROOT / "benchmark" / "registry" / "data"
    expected = {
        "agentdojo/manifest.json", "asb_opi/manifest.json",
        "mcptox/manifest.json", "msb/manifest.json", "scr/manifest.json",
        "skillinject/manifest.json",
    }
    assert all((data / item).is_file() for item in expected)


def test_all_benchmark_protocols_validate_packaged_inputs():
    root = ROOT / "benchmark" / "protocol"
    expected = {"agentdojo", "asb_opi", "mcptox", "msb", "scr", "skillinject"}
    found = {path.parent.name for path in root.glob("*/protocol.json")}
    assert found == expected
    for path in root.glob("*/protocol.json"):
        load_protocol(path)


def test_registry_data_contains_only_final_manifests():
    root = ROOT / "benchmark" / "registry" / "data"
    assert sorted(path.name for path in root.rglob("*.json")) == [
        "manifest.json", "manifest.json", "manifest.json",
        "manifest.json", "manifest.json", "manifest.json",
    ]
    for path in root.glob("*/manifest.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema"] == "apex-benchmark-registry-v2"
        assert raw["capability_units"]
        assert raw["environments"]
        assert raw["case_bindings"]
        for capability in raw["capability_units"].values():
            assert isinstance(capability["inputSchema"], dict)
            assert isinstance(capability["outputSchema"], dict)
            assert type(capability["effect"]) is bool
            assert type(capability["observation"]) is bool
        for environment in raw["environments"].values():
            assert isinstance(environment["agent_visible_surface"]["tools"], list)
            assert all(unit in raw["capability_units"]
                       for unit in environment["capability_units"])


def test_scr_registry_contains_all_three_suites():
    path = ROOT / "benchmark" / "registry" / "data" / "scr" / "manifest.json"
    raw = json.loads(path.read_text())
    assert raw["suite_counts"] == {
        "capflow": {"available": 150, "eligible": 150},
        "authblur": {"available": 118, "eligible": 116},
        "trustlift": {"available": 401, "eligible": 401},
    }
    assert len(raw["case_bindings"]) == 669
    assert {key.split(":", 1)[0] for key in raw["case_bindings"]} == {
        "capflow", "authblur", "trustlift"
    }


def test_scr_case_bindings_materialize_condition_specific_surfaces():
    from benchmark.registry.schema import load_case_environment

    path = ROOT / "benchmark" / "registry" / "data" / "scr" / "manifest.json"
    clean = load_case_environment(
        path, "trustlift:amtrak", condition="clean", benchmark="SCR")
    attack = load_case_environment(
        path, "trustlift:amtrak", condition="attack", benchmark="SCR")
    auth = load_case_environment(
        path, "authblur:1", condition="attack", benchmark="SCR")
    assert set(clean.capabilities) == {"scan_skills"}
    assert set(attack.capabilities) == {"scan_skills", "install_skills"}
    assert set(auth.capabilities) == {
        "upstream_assessment", "control_decision", "authorize_control"
    }
