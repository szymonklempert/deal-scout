"""Phone numbers and the seller drafts."""

import tempfile
import unittest
from pathlib import Path

from deal_scout import contact


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


if __name__ == "__main__":
    unittest.main()
