"""Make the source layout importable without requiring installation."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)
