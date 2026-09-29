"""Pipelock command-line scanner integration."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess


class Pipelock:
    def __init__(self, executable: str | Path):
        self.executable = Path(executable).expanduser().resolve()
        if not self.executable.is_file():
            raise FileNotFoundError(self.executable)

    def scan(self, catalog: dict) -> dict:
        process = subprocess.run(
            [str(self.executable), "scan", "--format", "json"],
            input=json.dumps(catalog, ensure_ascii=False), text=True,
            capture_output=True, check=False)
        if process.returncode:
            raise RuntimeError(process.stderr.strip() or "Pipelock scan failed")
        result = json.loads(process.stdout)
        if not isinstance(result, dict):
            raise ValueError("Pipelock result must be a JSON object")
        return result
