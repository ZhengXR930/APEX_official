"""Concurrency and restart behavior for the PLANT proposal cache."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest

from apex.defender.plant_cache import ConcurrentPersistentCache


class ConcurrentPersistentCacheTests(unittest.TestCase):
    def test_same_key_computes_once(self):
        cache = ConcurrentPersistentCache()
        calls = 0
        lock = threading.Lock()

        def compute():
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.05)
            return {"status": "abstain", "placements": [], "reason": "test"}

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _index: cache.get_or_compute(("same",), compute),
                range(8)))
        self.assertEqual(1, calls)
        self.assertEqual(1, sum(not reused for _value, reused in results))
        self.assertEqual(7, sum(reused for _value, reused in results))

    def test_different_keys_compute_concurrently(self):
        cache = ConcurrentPersistentCache()
        condition = threading.Condition()
        active = 0
        peak = 0

        def compute():
            nonlocal active, peak
            with condition:
                active += 1
                peak = max(peak, active)
                condition.notify_all()
                condition.wait_for(lambda: active == 2, timeout=1)
            time.sleep(0.02)
            with condition:
                active -= 1
            return {"status": "abstain", "placements": [], "reason": "test"}

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(cache.get_or_compute, (index,), compute)
                       for index in range(2)]
            for future in futures:
                future.result(timeout=2)
        self.assertEqual(2, peak)

    def test_completed_value_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plant-cache.jsonl"
            first = ConcurrentPersistentCache(path, namespace="model-a")
            value, reused = first.get_or_compute(
                ("key",), lambda: {
                    "status": "abstain", "placements": [], "reason": "saved"})
            self.assertFalse(reused)
            self.assertEqual("saved", value["reason"])

            second = ConcurrentPersistentCache(path, namespace="model-a")
            value, reused = second.get_or_compute(
                ("key",), lambda: self.fail("persistent value was recomputed"))
            self.assertTrue(reused)
            self.assertEqual("saved", value["reason"])


if __name__ == "__main__":
    unittest.main()
