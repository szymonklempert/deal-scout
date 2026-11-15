import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
import sheet  # noqa: E402


class FakeOpener:
    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append(req)
        return io.BytesIO(self.answer.encode("utf-8"))


class SheetTests(unittest.TestCase):
    def setUp(self):
        os.environ["GOOGLE_SHEET_URL"] = "https://script.google.com/macros/s/x/exec"
        os.environ["GOOGLE_SHEET_SECRET"] = "s3cret"
        self.addCleanup(os.environ.pop, "GOOGLE_SHEET_URL", None)
        self.addCleanup(os.environ.pop, "GOOGLE_SHEET_SECRET", None)

    def test_sends_secret_fields_and_numbers(self):
        opener = FakeOpener('{"ok": true, "row": 2}')
        sheet.push({"number": "7", "title": "Ducato", "price_pln": "118500", "score": "6.5", "year": ""},
                   ["number", "title", "price_pln", "score", "year"], opener=opener)
        req = opener.requests[0]
        self.assertEqual(req.full_url, "https://script.google.com/macros/s/x/exec")
        body = json.loads(req.data)
        self.assertEqual(body["secret"], "s3cret")
        self.assertEqual(body["row"], {"number": 7, "title": "Ducato", "price_pln": 118500, "score": 6.5, "year": ""})

    def test_script_error_raises(self):
        with self.assertRaisesRegex(RuntimeError, "wrong secret"):
            sheet.push({"number": 1}, ["number"], opener=FakeOpener('{"ok": false, "error": "wrong secret"}'))

    def test_html_answer_raises(self):
        with self.assertRaisesRegex(RuntimeError, "did not answer with JSON"):
            sheet.push({"number": 1}, ["number"], opener=FakeOpener("<html>Sign in</html>"))

    def test_link_only_accepts_a_spreadsheet_address(self):
        os.environ["GOOGLE_SHEET_LINK"] = "https://docs.google.com/spreadsheets/d/abc/edit"
        self.addCleanup(os.environ.pop, "GOOGLE_SHEET_LINK", None)
        self.assertEqual(sheet.link(), "https://docs.google.com/spreadsheets/d/abc/edit")
        os.environ["GOOGLE_SHEET_LINK"] = "https://script.google.com/macros/s/x/exec"
        self.assertIsNone(sheet.link())

    def test_push_all_copies_every_row_with_all_columns(self):
        import process
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "listings.csv"
            process.write_rows(path, {2: {"number": 2, "title": "B"}, 1: {"number": 1, "title": "A"}})
            opener = FakeOpener('{"ok": true, "row": 2}')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(sheet.push_all(path, opener=opener), 2)
        bodies = [json.loads(r.data) for r in opener.requests]
        self.assertEqual([b["row"]["number"] for b in bodies], [1, 2])
        self.assertEqual(bodies[0]["fields"], process.csv_fields())

    def test_enabled(self):
        self.assertTrue(sheet.enabled())
        os.environ["GOOGLE_SHEET_URL"] = ""
        self.assertFalse(sheet.enabled())


if __name__ == "__main__":
    unittest.main()
