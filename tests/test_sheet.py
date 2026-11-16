import contextlib
import dataclasses
import io
import json
import tempfile
import unittest
from pathlib import Path

from deal_scout import config, domain, sheet, storage

CAMPERVAN = domain.load("campervan")
FIELDS = storage.csv_fields(CAMPERVAN)
SETTINGS = config.Settings(sheet_url="https://script.google.com/macros/s/x/exec", sheet_secret="s3cret")


class FakeOpener:
    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append(req)
        return io.BytesIO(self.answer.encode("utf-8"))


class SheetTests(unittest.TestCase):
    def test_sends_secret_fields_and_numbers(self):
        opener = FakeOpener('{"ok": true, "row": 2}')
        sheet.push(
            {"number": "7", "title": "Ducato", "price_pln": "118500", "score": "6.5", "year": ""},
            ["number", "title", "price_pln", "score", "year"],
            SETTINGS,
            opener=opener,
        )
        req = opener.requests[0]
        self.assertEqual(req.full_url, "https://script.google.com/macros/s/x/exec")
        body = json.loads(req.data)
        self.assertEqual(body["secret"], "s3cret")
        self.assertEqual(body["row"], {"number": 7, "title": "Ducato", "price_pln": 118500, "score": 6.5, "year": ""})

    def test_script_error_raises(self):
        with self.assertRaisesRegex(RuntimeError, "wrong secret"):
            sheet.push({"number": 1}, ["number"], SETTINGS, opener=FakeOpener('{"ok": false, "error": "wrong secret"}'))

    def test_html_answer_raises(self):
        with self.assertRaisesRegex(RuntimeError, "did not answer with JSON"):
            sheet.push({"number": 1}, ["number"], SETTINGS, opener=FakeOpener("<html>Sign in</html>"))

    def test_link_only_accepts_a_spreadsheet_address(self):
        good = dataclasses.replace(SETTINGS, sheet_link="https://docs.google.com/spreadsheets/d/abc/edit")
        self.assertEqual(sheet.link(good), "https://docs.google.com/spreadsheets/d/abc/edit")
        self.assertIsNone(
            sheet.link(dataclasses.replace(SETTINGS, sheet_link="https://script.google.com/macros/s/x/exec"))
        )
        self.assertIsNone(sheet.link(SETTINGS))

    def test_push_all_copies_every_row_with_all_columns(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "listings.csv"
            storage.write_rows(path, {2: {"number": 2, "title": "B"}, 1: {"number": 1, "title": "A"}}, FIELDS)
            opener = FakeOpener('{"ok": true, "row": 2}')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(sheet.push_all(path, FIELDS, SETTINGS, opener=opener), 2)
        bodies = [json.loads(r.data) for r in opener.requests]
        self.assertEqual([b["row"]["number"] for b in bodies], [1, 2])
        self.assertEqual(bodies[0]["fields"], FIELDS)

    def test_enabled(self):
        self.assertTrue(sheet.enabled(SETTINGS))
        self.assertFalse(sheet.enabled(dataclasses.replace(SETTINGS, sheet_url="")))


if __name__ == "__main__":
    unittest.main()
