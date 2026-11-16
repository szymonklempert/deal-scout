"""Phone numbers and the seller drafts."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from deal_scout import contact
from deal_scout.errors import ProcessError


class PhoneTests(unittest.TestCase):
    def test_formats(self):
        for raw in ("+48 600-123-456", "0048 600 123 456", "600123456", "600 123 456", "+48600123456"):
            self.assertEqual(contact.normalise_phone(raw), "48600123456", raw)
        self.assertEqual(contact.normalise_phone("+49 151 23456789"), "4915123456789")
        for raw in (None, "", "12", "not shown"):
            self.assertIsNone(contact.normalise_phone(raw), raw)


class PrettyPhoneTests(unittest.TestCase):
    def test_format(self):
        self.assertEqual(contact.pretty_phone("48600123456"), "+48 600 123 456")
        self.assertEqual(contact.pretty_phone("4915123456789"), "+4915123456789")


class ScrubTests(unittest.TestCase):
    def test_formats(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.md"
            for written in ("+48 600-123-456", "600 123 456", "0048600123456", "(600) 123 456"):
                path.write_text(f"call {written} today, 196 000 km")
                contact.scrub_phone(path, "600123456")
                self.assertEqual(path.read_text(), "call [phone removed] today, 196 000 km", written)


class ContactedAtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "listings").mkdir()
        self.path = self.root / "listings" / "007.json"
        self.path.write_text(json.dumps({"seller_message": "Hi", "seller_sms": "Hi!", "title": "Łódź"}))

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_taking_a_draft_records_the_first_time_only(self):
        contact.seller_text(self.root, 7, "msg", "", now=datetime(2026, 11, 12, 9, 5))
        self.assertEqual(self.stored()["contacted_at"], "2026-11-12 09:05")
        contact.seller_text(self.root, 7, "sms", "", now=datetime(2026, 11, 15, 18, 0))
        self.assertEqual(self.stored()["contacted_at"], "2026-11-12 09:05")
        self.assertEqual(self.stored()["title"], "Łódź")

    def test_no_phone_is_written(self):
        contact.seller_text(self.root, 7, "msg", "48600123456", now=datetime(2026, 11, 12, 9, 5))
        self.assertNotIn("600", self.path.read_text())

    def test_a_missing_draft_records_nothing(self):
        self.path.write_text(json.dumps({"title": "x"}))
        with self.assertRaises(ProcessError):
            contact.seller_text(self.root, 7, "msg", "")
        self.assertNotIn("contacted_at", self.stored())

    def test_unwritable_json_does_not_block_the_draft(self):
        mark = contact.mark_contacted
        self.path.write_text("{")
        mark(self.root, 7, datetime(2026, 11, 12, 9, 5))  # logs a warning, does not raise
        self.assertEqual(self.path.read_text(), "{")


if __name__ == "__main__":
    unittest.main()
