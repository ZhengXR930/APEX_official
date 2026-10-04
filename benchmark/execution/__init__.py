"""Public benchmark execution bridge.

The package owns orchestration only. Benchmark-native drivers own environment
setup, agent turns, tool implementations, and official scorers.
"""

from .driver import CaseContext, load_case_plan, run_driver
from .preflight import preflight

__all__ = ["CaseContext", "load_case_plan", "preflight", "run_driver"]
