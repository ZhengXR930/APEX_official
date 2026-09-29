"""MCPGuard scanner integration with content-addressed caching."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable


class MCPGuard:
    def __init__(self, scanner: Callable[[dict[str, Any]], Any],
                 cache_path: str | Path | None = None):
        self.scanner = scanner
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache = (
            json.loads(self.cache_path.read_text(encoding="utf-8"))
            if self.cache_path and self.cache_path.is_file() else {}
        )

    def scan(self, tool: dict[str, Any]) -> dict[str, Any]:
        canonical = json.dumps(tool, sort_keys=True, ensure_ascii=False, default=str)
        key = hashlib.sha256(canonical.encode()).hexdigest()
        if key not in self.cache:
            raw = self.scanner(tool)
            self.cache[key] = raw if isinstance(raw, dict) else {"result": raw}
            if self.cache_path:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(
                    json.dumps(self.cache, ensure_ascii=False, indent=2),
                    encoding="utf-8")
        return dict(self.cache[key])
