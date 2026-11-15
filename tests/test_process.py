"""Analysing one listing: files written, summary, contact drafts."""
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from fakes import CAMPERVAN, RESULT, SETTINGS, FakeRunner, check

from deal_scout import config, contact, errors, process, storage
from deal_scout.render import Message


class NoSellerMessageTests(unittest.TestCase):
    def test_no_seller_message_means_one_telegram_message(self):
        root = Path(self._tmp.name)
        (root / "inbox" / "001").mkdir(parents=True)
        out = process.process_listing(root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                      runner=FakeRunner(dict(RESULT, seller_message=" ", seller_sms="")))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].buttons, (("📨 Seller replied", "sr:1"), ("📝 Feedback", "fx:1:48600123456")))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)


class ProcessListingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / "inbox" / "001").mkdir(parents=True)
        (self.root / "inbox" / "001" / "links.txt").write_text("https://www.facebook.com/groups/1/posts/2\n")

    def tearDown(self):
        self.dir.cleanup()

    def test_writes_json_csv_index_and_summary(self):
        runner = FakeRunner()
        settings = config.Settings.from_env({"TELEGRAM_BOT_TOKEN": "secret", "ANTHROPIC_API_KEY": "k",
                                             "PATH": "/usr/bin"})
        out = process.process_listing(self.root, 1, settings=settings, domain=CAMPERVAN, model="opus", runner=runner)
        self.assertEqual(len(out), 1)  # the drafts come on a tap, not straight away
        summary = out[0]
        self.assertTrue(summary.html)
        text = summary.text
        self.assertEqual(summary.buttons, (("💬 WhatsApp message", "msg:1:48600123456"),
                                           ("✉️ SMS", "sms:1:48600123456"), ("📨 Seller replied", "sr:1"),
                                           ("📝 Feedback", "fx:1:48600123456")))
        message = contact.seller_text(self.root, 1, "msg", "48600123456")[0]
        self.assertTrue(message.text.startswith("Dzień dobry"))
        label, url = message.buttons[0]
        self.assertTrue(url.startswith("https://wa.me/48600123456?text=Dzie%C5%84%20dobry"))
        sms = contact.seller_text(self.root, 1, "sms", "48600123456")
        self.assertEqual(sms, [Message(
            text="<code>Dzień dobry, czy mogę prosić o VIN Hymera?</code>\n\n"
                 "📱 +48 600 123 456\nTap the text to copy it, then tap the number and choose SMS.",
            html=True)])
        self.assertIn("No phone number", contact.seller_text(self.root, 1, "sms", "")[0].text)
        self.assertEqual(contact.seller_text(self.root, 1, "msg", "")[0].buttons, ())
        self.assertIn("listing asks for whatsapp", text)
        self.assertIn("Seller: Jan Kowalski", text)
        for name in ("001.json", "listings.csv", "INDEX.md"):
            self.assertNotIn("600-123", (self.root / "listings" / name).read_text(), name)
            self.assertNotIn("600123", (self.root / "listings" / name).read_text(), name)
        self.assertEqual(runner.calls[0][1]["cwd"], str(self.root))
        env = runner.calls[0][1]["env"]
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertFalse([k for k in env if k.startswith("TELEGRAM_") or k == "ANTHROPIC_API_KEY"])
        self.assertNotIn("ANTHROPIC_API_KEY", runner.calls[0][1]["env"])
        self.assertEqual(json.loads((self.root / "listings/001.json").read_text())["price_pln"], 118500)
        rows = storage.read_rows(self.root / "listings/listings.csv")
        self.assertEqual(rows[1]["score"], "6.2")
        self.assertEqual(rows[1]["missing"], "VIN; DMC")
        self.assertEqual(rows[1]["source"], "Kampery i campervany na sprzedaż")
        self.assertEqual(rows[1]["link"], "https://www.facebook.com/groups/1/posts/2")

        self.assertIn("| 1 | Fiat Ducato 2.3 Hymer 2014 | 118500 |", (self.root / "listings/INDEX.md").read_text())
        self.assertIn("<b>118 500 PLN</b> · Łódź · Kampery i campervany na sprzedaż", text)
        self.assertIn("Score <b>6.2/10</b> · specs <b>7.0/10</b> · <b>ask first</b>", text)
        self.assertIn("🟢 Solar &amp; batteries: Victron 400 W · 8/10", text)
        self.assertIn("⚪ Mileage: <i>not shown</i>", text)  # a score for something not shown is dropped
        self.assertIn("⚪ Rust &amp; damp: <i>not shown</i>", text)  # left out by Claude: still listed
        self.assertEqual(text.count("/10\n") + text.count("not shown</i>\n"), len(CAMPERVAN.specs))
        self.assertEqual(rows[1]["Solar & batteries"], "Victron 400 W (8/10)")
        self.assertEqual(rows[1]["specs_avg"], "7.0")
        self.assertEqual(rows[1]["model_family"], "Ducato/Jumper/Boxer X250 (2006-2014)")
        self.assertEqual(rows[1]["fair_price_high"], "115000")
        self.assertTrue((self.root / "listings/market.md").exists())

    def analyse_with_reply(self, runner_data=None, replies=1):
        folder = self.root / "inbox" / "001"
        for i in range(replies):
            (folder / f"reply_{i + 1:02d}.jpg").write_bytes(b"r")
        return process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                       runner=FakeRunner(runner_data or RESULT))

    def test_seller_reply_adds_one_line_and_stores_replies_seen(self):
        first = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                        runner=FakeRunner())[0]
        self.assertNotIn("📨 After", first.text)
        self.assertNotIn("replies_seen", json.loads((self.root / "listings/001.json").read_text()))
        better = dict(RESULT, missing=["DMC"], criteria_checks=[check("Budget", "met", True),
                                                                check("Fixed bed", "met"), check("Toilet", "met")])
        text = self.analyse_with_reply(better)[0].text
        self.assertEqual(json.loads((self.root / "listings/001.json").read_text())["replies_seen"], 1)
        self.assertRegex(text, r"</i>\n📨 After the seller's reply: score 6\.2 → [\d.]+ · answered: VIN\n\n")
        again = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                        runner=FakeRunner(better))[0]
        self.assertNotIn("📨 After", again.text)  # no new reply since the last analysis
        self.assertIn(("📨 Seller replied", "sr:1"), again.buttons)

    def test_reply_note_says_unchanged(self):
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner())
        text = self.analyse_with_reply(replies=2)[0].text
        self.assertIn("📨 After the seller's reply: score 6.2 (unchanged)\n", text)
        self.assertEqual(json.loads((self.root / "listings/001.json").read_text())["replies_seen"], 2)

    def test_first_analysis_with_replies_has_no_comparison(self):
        self.assertNotIn("📨 After", self.analyse_with_reply()[0].text)

    def test_reprocessing_replaces_row_and_keeps_status(self):
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner())
        rows = storage.read_rows(self.root / "listings/listings.csv")
        rows[1]["status"] = "contacted"
        rows[1]["first_seen"] = "2026-10-01"
        storage.write_rows(self.root / "listings/listings.csv", rows, storage.csv_fields(CAMPERVAN))
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner())
        rows = storage.read_rows(self.root / "listings/listings.csv")
        self.assertEqual(list(rows), [1])
        self.assertEqual(rows[1]["status"], "contacted")
        self.assertEqual(rows[1]["first_seen"], "2026-10-01")

    def test_index_sorted_by_score_and_zero_shown(self):
        (self.root / "inbox" / "002").mkdir()
        bad = dict(RESULT, title="Rusty", criteria_checks=[check("Rust", "missed", True)])
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner(bad))
        process.process_listing(self.root, 2, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner())
        index = (self.root / "listings/INDEX.md").read_text().splitlines()
        self.assertTrue(index[4].startswith("| 2 |"))
        self.assertIn("| 0.0 |", index[5])

    def test_seller_saved_and_repeat_seller_flagged(self):
        (self.root / "inbox" / "002").mkdir()
        (self.root / "inbox" / "003").mkdir()
        first = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                        runner=FakeRunner())[0].text
        self.assertIn("Seller: Jan Kowalski", first)
        self.assertNotIn("also", first)
        process.process_listing(self.root, 2, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                runner=FakeRunner(dict(RESULT, seller_name="Anna Nowak")))
        third = process.process_listing(self.root, 3, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                        runner=FakeRunner(dict(RESULT, seller_name="jan  KOWALSKI")))[0].text
        self.assertIn("Seller: jan  KOWALSKI (also #1)", third)
        self.assertEqual(storage.read_rows(self.root / "listings/listings.csv")[1]["seller"], "Jan Kowalski")

    def test_no_phone_on_screenshot(self):
        out = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                      runner=FakeRunner(dict(RESULT, seller_phone=None)))
        self.assertIn("/phone 1 600123456 adds one", out[0].text)
        self.assertEqual(out[0].buttons[0], ("💬 WhatsApp message", "msg:1:"))
        later = contact.contact_for(self.root, 1, "600 123 456")
        self.assertIn("wa.me/48600123456?text=", later[0].buttons[0][1])
        self.assertEqual(len(later), 2)

    def test_google_sheet_push(self):
        pushed = []
        settings = dataclasses.replace(SETTINGS, sheet_url="https://script.google.com/macros/s/x/exec")
        process.process_listing(self.root, 1, settings=settings, domain=CAMPERVAN, model="opus", runner=FakeRunner(),
                                pusher=lambda row, fields: pushed.append((row, fields)))
        self.assertEqual(pushed[0][0]["seller"], "Jan Kowalski")
        self.assertIn("source", pushed[0][1])

        def broken(row, fields):
            raise RuntimeError("wrong secret")
        text = process.process_listing(self.root, 1, settings=settings, domain=CAMPERVAN, model="opus",
                                       runner=FakeRunner(),
                                       pusher=broken)[0].text
        self.assertIn("Google Sheet not updated (wrong secret)", text)
        self.assertTrue((self.root / "listings/listings.csv").exists())

    def test_open_sheet_button_on_summary(self):
        settings = dataclasses.replace(SETTINGS, sheet_link="https://docs.google.com/spreadsheets/d/abc/edit")
        summary = process.process_listing(self.root, 1, settings=settings, domain=CAMPERVAN, model="opus",
                                          runner=FakeRunner())[0]
        self.assertEqual(summary.buttons[-2], ("📝 Feedback", "fx:1:48600123456"))
        self.assertEqual(summary.buttons[-1], ("📊 Open sheet", "https://docs.google.com/spreadsheets/d/abc/edit"))
        summary = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                          runner=FakeRunner())[0]
        self.assertNotIn("📊 Open sheet", [b[0] for b in summary.buttons])

    def test_sms_button_errors_when_there_is_no_draft(self):
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                runner=FakeRunner(dict(RESULT, seller_sms="")))
        with self.assertRaisesRegex(errors.ProcessError, "no SMS draft"):
            contact.seller_text(self.root, 1, "sms")

    def test_no_sheet_configured_means_no_push(self):
        pushed = []
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner(),
                                pusher=lambda row, fields: pushed.append(row))
        self.assertEqual(pushed, [])

    def test_contact_for_errors(self):
        with self.assertRaisesRegex(errors.ProcessError, "not been analysed"):
            contact.contact_for(self.root, 1, "600123456")
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner())
        with self.assertRaisesRegex(errors.ProcessError, "doesn't look like a phone number"):
            contact.contact_for(self.root, 1, "12")

    def test_seller_not_shown(self):
        text = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                       runner=FakeRunner(dict(RESULT, seller_name=None)))[0].text
        self.assertNotIn("Seller:", text)

    def test_empty_criteria_says_so(self):
        text = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                       runner=FakeRunner(dict(RESULT, criteria_checks=[])))[0].text
        self.assertIn("Score – (fill in criteria.md)", text)

    def test_missing_values_are_reported_not_invented(self):
        data = dict(RESULT, price_pln=None, mileage_km=None)
        text = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                       runner=FakeRunner(data))[0].text
        self.assertIn("price not shown · Łódź", text)

    def test_claude_error_raises(self):
        runner = FakeRunner(stdout=json.dumps({"is_error": True, "result": "Not logged in"}))
        with self.assertRaisesRegex(errors.ProcessError, "Not logged in"):
            process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=runner)
        with self.assertRaisesRegex(errors.ProcessError, "exit 1"):
            process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                    runner=FakeRunner(stdout="", returncode=1))

    def test_unknown_listing(self):
        with self.assertRaises(errors.ProcessError):
            process.process_listing(self.root, 9, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                    runner=FakeRunner())


if __name__ == "__main__":
    unittest.main()
