"""Applying the owner's Feedback taps."""
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from fakes import CAMPERVAN, RESULT, SETTINGS, FakeRunner, check

from deal_scout import corrections, errors, process, storage


class FixTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        (self.root / "inbox" / "001").mkdir(parents=True)
        self.analyse()
        self.csv = self.root / "listings/listings.csv"
        self.solar = CAMPERVAN.specs.index("Solar & batteries")

    def analyse(self, data=RESULT):
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                runner=FakeRunner(data))

    def fix(self, action, *args, **kw):
        return corrections.fix(self.root, 1, action, list(args), settings=SETTINGS, domain=CAMPERVAN, **kw)

    def feedback(self):
        path = self.root / "listings/feedback.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_value_changes_score_row_and_logs(self):
        before = storage.read_rows(self.csv)[1]
        reply = self.fix("value", str(self.solar), "3")
        row = storage.read_rows(self.csv)[1]
        self.assertEqual(row["Solar & batteries"], "Victron 400 W (3/10)")
        self.assertEqual(row["corrections"], "Solar & batteries 8→3")
        self.assertEqual(row["specs_avg"], "4.5")
        self.assertEqual(row["processed"], before["processed"])
        self.assertTrue(reply.edit)
        self.assertEqual(reply.ask_reason, "Solar & batteries")
        self.assertIn("Solar &amp; batteries: 8 → 3", reply.message.text)
        saved = json.loads((self.root / "listings/001.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["overrides"], {"specs": {"Solar & batteries": 3}})
        self.assertEqual(saved["specs"][1]["score"], 8)  # the model's value is untouched
        line = self.feedback()[0]
        self.assertEqual((line["kind"], line["name"], line["model"], line["owner"], line["domain"]),
                         ("spec", "Solar & batteries", 8, 3, "campervan"))
        self.assertNotIn("Kowalski", json.dumps(line))
        self.fix("reason", "Solar & batteries", "panel is 100 W, call 600 123 456")
        text = (self.root / "listings/feedback.jsonl").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\d{7}|600 123")
        self.assertEqual(self.feedback()[1]["kind"], "reason")

    def test_mark_changes_the_score_and_back_to_model_removes_it(self):
        self.fix("mark", "2", "m")  # Toilet: unknown -> met
        self.assertEqual(storage.read_rows(self.csv)[1]["corrections"], "Toilet unknown→met")
        self.assertEqual(storage.read_rows(self.csv)[1]["met"], "3")
        self.fix("mark", "2", "u")
        self.assertEqual(storage.read_rows(self.csv)[1]["corrections"], "")
        saved = json.loads((self.root / "listings/001.json").read_text(encoding="utf-8"))
        self.assertNotIn("overrides", saved)

    def test_corrections_survive_reanalysis(self):
        self.fix("value", str(self.solar), "x")
        self.fix("mark", "2", "m")
        self.fix("mark", "3", "m")
        changed = dict(RESULT, criteria_checks=[check("Budget", "met", True), check("Toilet", "unknown")])
        self.analyse(changed)
        saved = json.loads((self.root / "listings/001.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["overrides"], {"specs": {"Solar & batteries": None}, "checks": {"Toilet": "met"}})
        row = storage.read_rows(self.csv)[1]
        self.assertEqual(row["Solar & batteries"], "Victron 400 W")
        self.assertEqual(row["corrections"], "Solar & batteries 8→–; Toilet unknown→met")

    def test_done_returns_refreshed_summary_with_mark(self):
        self.fix("value", str(self.solar), "3")
        reply = self.fix("done")
        self.assertEqual(reply.message.text, "📝 #1: 1 correction(s) saved.")
        self.assertEqual(reply.message.buttons, ())
        (summary,) = reply.extra
        self.assertIn("Solar &amp; batteries: Victron 400 W · 3/10 ✏️ (was 8)", summary.text)
        self.assertIn(("📝 Feedback", "fx:1:"), summary.buttons)
        self.assertIn("no phone on the screenshots", summary.text)

    def test_done_with_the_phone_from_the_button_keeps_it_in_the_summary(self):
        self.fix("value", str(self.solar), "3")
        (summary,) = self.fix("done", "+48 600-123-456").extra
        self.assertIn(("💬 WhatsApp message", "msg:1:48600123456"), summary.buttons)
        self.assertIn(("📝 Feedback", "fx:1:48600123456"), summary.buttons)
        self.assertNotIn("no phone on the screenshots", summary.text)
        for path in (self.root / "listings").iterdir():
            self.assertNotIn("600123456", path.read_text(encoding="utf-8", errors="ignore"), path.name)

    def test_confirm_shows_specs_average_for_a_spec_and_score_for_a_check(self):
        spec = self.fix("value", str(self.solar), "3").message.text
        self.assertIn("· specs 7.0 → 4.5\n", spec)
        check_text = self.fix("mark", "2", "m").message.text
        self.assertRegex(check_text, r"· score 6\.2 → \d+\.\d\n")

    def test_sheet_failure_is_reported_not_raised(self):
        settings = dataclasses.replace(SETTINGS, sheet_url="https://script.google.com/macros/s/x/exec")

        def broken(row, fields):
            raise RuntimeError("wrong secret")
        reply = corrections.fix(self.root, 1, "value", [str(self.solar), "3"], settings=settings, domain=CAMPERVAN,
                            pusher=broken)
        self.assertIn("Google Sheet not updated (wrong secret)", reply.message.text)

    def test_screens(self):
        self.assertFalse(self.fix("open").edit)
        for action, args in (("back", []), ("spec", ["0"]), ("checks", []), ("check", ["1"]), ("skip", [])):
            self.assertTrue(self.fix(action, *args).edit)

    def test_errors(self):
        with self.assertRaisesRegex(errors.ProcessError, "not been analysed"):
            corrections.fix(self.root, 5, "open", [], settings=SETTINGS, domain=CAMPERVAN)
        for action, args in (("spec", ["99"]), ("spec", ["x"]), ("value", ["0", "11"]), ("check", ["9"]),
                             ("mark", ["0", "z"])):
            with self.assertRaisesRegex(errors.ProcessError, "out of date"):
                self.fix(action, *args)


if __name__ == "__main__":
    unittest.main()
