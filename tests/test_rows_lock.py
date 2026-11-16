"""The listings lock: threads and processes never lose an update."""

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from deal_scout import storage

ROUNDS = 40
WORKERS = 4


def increment(listings: str, counter: str, rounds: int) -> None:
    """Read-modify-write a counter file under the lock, as a worker process."""
    for _ in range(rounds):
        with storage.rows_lock(Path(listings)):
            path = Path(counter)
            value = int(path.read_text()) if path.exists() else 0
            path.write_text(str(value + 1))


class RowsLockTests(unittest.TestCase):
    def test_processes_never_lose_an_update(self):
        with tempfile.TemporaryDirectory() as d:
            listings, counter = Path(d) / "listings", Path(d) / "counter.txt"
            ctx = multiprocessing.get_context("spawn")
            procs = [ctx.Process(target=increment, args=(str(listings), str(counter), ROUNDS)) for _ in range(WORKERS)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(60)
                self.assertEqual(p.exitcode, 0)
            self.assertEqual(int(counter.read_text()), ROUNDS * WORKERS)
            self.assertTrue((listings / storage.LOCK_FILE).exists())

    def test_the_lock_is_released_after_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            listings = Path(d)
            with self.assertRaises(ValueError), storage.rows_lock(listings):
                raise ValueError("boom")
            with storage.rows_lock(listings):
                pass


if __name__ == "__main__":
    unittest.main()
