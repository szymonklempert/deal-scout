"""The listings CSV."""
import tempfile
import unittest
from pathlib import Path

from deal_scout import storage


class CsvTests(unittest.TestCase):
    def test_round_trip_keeps_utf8(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "listings.csv"
            storage.write_rows(path, {1: {"number": 1, "title": "Łódź Żuk"}}, ["number", "title"])
            self.assertEqual(storage.read_rows(path)[1]["title"], "Łódź Żuk")


if __name__ == "__main__":
    unittest.main()
