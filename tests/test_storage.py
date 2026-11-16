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


class OpenTests(unittest.TestCase):
    def test_closed_statuses_in_any_case_are_not_open(self):
        for status in ("rejected", "SOLD", " Bought ", "skip", "closed"):
            self.assertFalse(storage.is_open({"status": status}), status)
        for status in ("new", "contacted", "", None):
            self.assertTrue(storage.is_open({"status": status}), status)


if __name__ == "__main__":
    unittest.main()
