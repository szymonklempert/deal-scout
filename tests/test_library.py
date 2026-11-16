"""Reading the stored listings for /show, /top and /compare."""

import json
import tempfile
import unittest
from pathlib import Path

from fakes import RESULT, SETTINGS, FakeRunner

from deal_scout import domain, library, process
from deal_scout.errors import ProcessError
from deal_scout.storage import read_rows, write_rows

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.domain = domain.load("gadget", roots=[FIXTURES])
        for n, price in ((1, 1000), (2, 1200), (3, 900)):
            (self.root / "inbox" / f"{n:03d}").mkdir(parents=True)
            data = dict(RESULT, price_pln=price, model=f"Model {n}", year=2000 + n)
            process.process_listing(
                self.root, n, settings=SETTINGS, domain=self.domain, model="opus", runner=FakeRunner(data)
            )

    def close(self, n):
        path = self.root / "listings" / "listings.csv"
        rows = read_rows(path)
        rows[n]["status"] = "sold"
        write_rows(path, rows, list(rows[n]))

    def run_report(self, cmd, *args):
        return library.reports_for(self.root, cmd, args, settings=SETTINGS, domain=self.domain)

    def test_show_rebuilds_the_summary_with_the_stored_wording(self):
        (msg,) = self.run_report("/show", "#2")
        self.assertIn("<b>#2 ", msg.text)
        self.assertIn("adds the seller's number to the WhatsApp button", msg.text)
        self.assertNotIn("no phone on the screenshots", msg.text)

    def test_show_applies_the_owners_corrections(self):
        path = self.root / "listings" / "002.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["overrides"] = {"specs": {"Condition": 2}}
        path.write_text(json.dumps(data), encoding="utf-8")
        (msg,) = self.run_report("/show", "2")
        self.assertIn("✏️", msg.text)

    def test_show_problems(self):
        with self.assertRaises(ProcessError):
            self.run_report("/show")
        with self.assertRaises(ProcessError):
            self.run_report("/show", "9")

    def test_top_leaves_out_closed_listings(self):
        self.close(1)
        (msg,) = self.run_report("/top")
        self.assertNotIn("/show_1\n", msg.text + "\n")
        self.assertIn("/show_2", msg.text)
        (msg,) = self.run_report("/top", "1")
        self.assertEqual(len(msg.text.splitlines()), 1)

    def test_compare_table_uses_the_domain_specs(self):
        (msg,) = self.run_report("/compare", "1", "#2", "3")
        for name in self.domain.specs:
            self.assertIn(name[:14], msg.text)
        self.assertIn("Open: /show_1 · /show_2 · /show_3", msg.text)

    def test_compare_problems(self):
        for args in (["1"], ["1", "2", "3", "4", "5"], ["1", "9"]):
            with self.assertRaises(ProcessError):
                self.run_report("/compare", *args)

    def test_unreadable_json_gives_an_entry_without_spec_scores(self):
        (self.root / "listings" / "001.json").write_text("{", encoding="utf-8")
        entries = library.load_entries(self.root, self.domain)
        self.assertEqual(entries[1].specs, ())
        self.assertTrue(entries[2].specs)

    def test_unknown_report(self):
        with self.assertRaises(ProcessError):
            self.run_report("/nope")


if __name__ == "__main__":
    unittest.main()
