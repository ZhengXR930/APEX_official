"""Concurrent, crash-resilient cache for semantic PLANT proposals."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Callable


class ConcurrentPersistentCache:
    """Deduplicate concurrent computations and optionally journal results.

    Values are addressed by a canonical JSON representation of the cache key.
    A per-key event lets unrelated keys compute concurrently while callers for
    the same key share exactly one result. Completed values are appended to a
    JSONL journal so a restarted process does not repeat model calls.
    """

    SCHEMA = "plant-proposal-cache-v1"

    def __init__(self, path: Path | None = None, *, namespace=""):
        self.path = Path(path) if path is not None else None
        self.namespace = namespace
        self._values: dict[str, object] = {}
        self._pending: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        if self.path is not None and self.path.is_file():
            self._load()

    def _token(self, key) -> str:
        return json.dumps(
            [self.namespace, key], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str)

    def _load(self) -> None:
        assert self.path is not None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # A process may have stopped during its final append.  Earlier
                # complete records remain valid and are deliberately retained.
                continue
            if (not isinstance(record, dict) or
                    record.get("schema") != self.SCHEMA or
                    not isinstance(record.get("key"), str) or
                    "value" not in record):
                continue
            self._values[record["key"]] = record["value"]

    def _append(self, token: str, value) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = json.dumps({
            "schema": self.SCHEMA, "key": token, "value": value,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(record + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)

    def __contains__(self, key) -> bool:
        token = self._token(key)
        with self._lock:
            return token in self._values

    def __getitem__(self, key):
        token = self._token(key)
        with self._lock:
            return self._values[token]

    def __setitem__(self, key, value) -> None:
        token = self._token(key)
        with self._lock:
            if token in self._values:
                self._values[token] = value
                return
            self._values[token] = value
            self._append(token, value)

    def get_or_compute(self, key, compute: Callable[[], object]):
        """Return ``(value, reused)`` with one owner for each missing key."""
        token = self._token(key)
        while True:
            with self._lock:
                if token in self._values:
                    return self._values[token], True
                event = self._pending.get(token)
                if event is None:
                    event = threading.Event()
                    self._pending[token] = event
                    owner = True
                else:
                    owner = False
            if owner:
                break
            event.wait()

        try:
            value = compute()
        except BaseException:
            with self._lock:
                self._pending.pop(token, None)
                event.set()
            raise
        with self._lock:
            self._values[token] = value
            self._append(token, value)
            self._pending.pop(token, None)
            event.set()
        return value, False
