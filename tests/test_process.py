import dataclasses
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
import config  # noqa: E402

import domain  # noqa: E402
import process  # noqa: E402

SETTINGS = config.Settings()
CAMPERVAN = domain.load("campervan")


def check(criterion, result, deal_breaker=False):
    return {"criterion": criterion, "result": result, "deal_breaker": deal_breaker}


RESULT = {
    "title": "Fiat Ducato 2.3 Hymer 2014",
    "seller_name": "Jan Kowalski",
    "location": "Łódź",
    "model_family": "Ducato/Jumper/Boxer X250 (2006-2014)",
    "body": "coachbuilt",
    "build": "OEM",
    "registration": "M1",
    "price_net": False,
    "fair_price_low": 105000,
    "fair_price_high": 115000,
    "specs": [{"name": "Year", "value": "2014", "score": 6}, {"name": "Solar & batteries", "value": "Victron 400 W",
                                                               "score": 8},
              {"name": "Mileage", "value": "not shown", "score": 3}],
    "source": "Kampery i campervany na sprzedaż",
    "price_pln": 118500,
    "model": "Fiat Ducato 2.3",
    "year": 2014,
    "mileage_km": 196000,
    "facts": [{"field": "Weight (DMC)", "value": "not shown", "certainty": "not shown"}],
    "criteria_checks": [check("Budget", "met", True), check("Fixed bed", "met"), check("Toilet", "unknown"),
                        check("Solar", "missed")],
    "missing": ["VIN", "DMC"],
    "red_flags": ["Urgent sale"],
    "verdict": "ask first",
    "summary": "Fits most criteria.",
    "seller_message": "Dzień dobry, interesuje mnie kamper Hymer.\n1. Czy mogę prosić o VIN?\nDziękuję.",
    "seller_sms": "Dzień dobry, czy mogę prosić o VIN Hymera?",
    "seller_phone": "+48 600-123-456",
    "contact_preference": "whatsapp",
}


class FakeRunner:
    """Stands in for subprocess.run(claude ...): records the call and returns a canned Claude Code JSON result."""

    def __init__(self, structured=RESULT, stdout=None, returncode=0):
        self.calls = []
        self.stdout = stdout if stdout is not None else json.dumps(
            {"type": "result", "subtype": "success", "is_error": False, "structured_output": structured})
        self.returncode = returncode

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "boom")


class PhoneTests(unittest.TestCase):
    def test_formats(self):
        for raw in ("+48 600-123-456", "0048 600 123 456", "600123456", "600 123 456", "+48600123456"):
            self.assertEqual(process.normalise_phone(raw), "48600123456", raw)
        self.assertEqual(process.normalise_phone("+49 151 23456789"), "4915123456789")
        for raw in (None, "", "12", "not shown"):
            self.assertIsNone(process.normalise_phone(raw), raw)


class PrettyPhoneTests(unittest.TestCase):
    def test_format(self):
        self.assertEqual(process.pretty_phone("48600123456"), "+48 600 123 456")
        self.assertEqual(process.pretty_phone("4915123456789"), "+4915123456789")


class ScoreTests(unittest.TestCase):
    def test_no_criteria_gives_no_score(self):
        self.assertIsNone(process.score([]))

    def test_met_unknown_missed(self):
        self.assertEqual(process.score(RESULT["criteria_checks"]), 6.2)  # (1 + 1 + 0.5 + 0) / 4

    def test_missed_deal_breaker_is_zero(self):
        self.assertEqual(process.score([check("Rust", "missed", True), check("Bed", "met")]), 0.0)

    def test_unknown_deal_breaker_is_not_zero(self):
        self.assertEqual(process.score([check("Rust", "unknown", True), check("Bed", "met")]), 7.5)

    def test_nice_to_haves_count_half(self):
        checks = [check("Solar", "met"), dict(check("Indoor shower", "missed"), importance="nice to have"),
                  dict(check("Swivel seats", "missed"), importance="big plus")]
        self.assertEqual(process.score(checks), 4.0)  # 1 of 2.5

    def test_deal_breaker_label_is_stripped(self):
        self.assertEqual(process.clean("Deal breaker: rust"), "rust")
        self.assertEqual(process.clean("Budget: 150 000"), "Budget: 150 000")


class CommandTests(unittest.TestCase):
    def test_claude_cannot_run_commands_or_browse_or_write_outside_listings(self):
        cmd = process.claude_command("007", "opus", CAMPERVAN)
        tools = cmd[cmd.index("--tools") + 1]
        self.assertNotIn("Bash", tools)
        self.assertNotIn("Web", tools)
        self.assertIn("--restricted", cmd)
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--permission-mode")]
        self.assertEqual([a for a in allowed if a.startswith(("Write", "Edit"))],
                         ["Write(./listings/**)", "Edit(./listings/**)"])
        self.assertIn("inbox/007/", cmd[2])
        self.assertIn("Never write phone numbers into any file", cmd[2])
        self.assertIn("seller_name", process.schema(CAMPERVAN)["required"])
        self.assertIn("seller_message", process.schema(CAMPERVAN)["required"])
        self.assertIn("grammatical gender", cmd[2])
        self.assertIn("knowledge.md (the scoring reference", cmd[2])
        self.assertIn("First read these files in domains/campervan/:", cmd[2])
        self.assertIn("overrides knowledge.md's", cmd[2])
        self.assertIn("listings/market.md", cmd[2])
        self.assertIn("base-vans.md", cmd[2])
        self.assertIn("Do not ask about a viewing, a test drive", cmd[2])

    def test_no_seller_message_means_one_telegram_message(self):
        root = Path(self._tmp.name)
        (root / "inbox" / "001").mkdir(parents=True)
        out = process.process_listing(root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                      runner=FakeRunner(dict(RESULT, seller_message=" ", seller_sms="")))
        self.assertEqual(len(out), 1)
        self.assertNotIn("buttons", out[0])

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
        self.assertTrue(summary["html"])
        text = summary["text"]
        self.assertEqual(summary["buttons"], [("💬 WhatsApp message", "msg:1:48600123456"),
                                              ("✉️ SMS", "sms:1:48600123456")])
        message = process.seller_text(self.root, 1, "msg", "48600123456")[0]
        self.assertTrue(message["text"].startswith("Dzień dobry"))
        label, url = message["buttons"][0]
        self.assertTrue(url.startswith("https://wa.me/48600123456?text=Dzie%C5%84%20dobry"))
        sms = process.seller_text(self.root, 1, "sms", "48600123456")
        self.assertEqual(sms, [{"html": True, "text": "<code>Dzień dobry, czy mogę prosić o VIN Hymera?</code>\n\n"
                                "📱 +48 600 123 456\nTap the text to copy it, then tap the number and choose SMS."}])
        self.assertIn("No phone number", process.seller_text(self.root, 1, "sms", "")[0]["text"])
        self.assertNotIn("buttons", process.seller_text(self.root, 1, "msg", "")[0])
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
        rows = process.read_rows(self.root / "listings/listings.csv")
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

    def test_reprocessing_replaces_row_and_keeps_status(self):
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner())
        rows = process.read_rows(self.root / "listings/listings.csv")
        rows[1]["status"] = "contacted"
        rows[1]["first_seen"] = "2026-10-01"
        process.write_rows(self.root / "listings/listings.csv", rows, process.csv_fields(CAMPERVAN))
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner())
        rows = process.read_rows(self.root / "listings/listings.csv")
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
                                        runner=FakeRunner())[0]["text"]
        self.assertIn("Seller: Jan Kowalski", first)
        self.assertNotIn("also", first)
        process.process_listing(self.root, 2, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                runner=FakeRunner(dict(RESULT, seller_name="Anna Nowak")))
        third = process.process_listing(self.root, 3, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                        runner=FakeRunner(dict(RESULT, seller_name="jan  KOWALSKI")))[0]["text"]
        self.assertIn("Seller: jan  KOWALSKI (also #1)", third)
        self.assertEqual(process.read_rows(self.root / "listings/listings.csv")[1]["seller"], "Jan Kowalski")

    def test_no_phone_on_screenshot(self):
        out = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                      runner=FakeRunner(dict(RESULT, seller_phone=None)))
        self.assertIn("/phone 1 600123456 adds one", out[0]["text"])
        self.assertEqual(out[0]["buttons"][0], ("💬 WhatsApp message", "msg:1:"))
        later = process.contact_for(self.root, 1, "600 123 456")
        self.assertIn("wa.me/48600123456?text=", later[0]["buttons"][0][1])
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
                                       pusher=broken)[0]["text"]
        self.assertIn("Google Sheet not updated (wrong secret)", text)
        self.assertTrue((self.root / "listings/listings.csv").exists())

    def test_open_sheet_button_on_summary(self):
        settings = dataclasses.replace(SETTINGS, sheet_link="https://docs.google.com/spreadsheets/d/abc/edit")
        summary = process.process_listing(self.root, 1, settings=settings, domain=CAMPERVAN, model="opus",
                                          runner=FakeRunner())[0]
        self.assertEqual(summary["buttons"][-1], ("📊 Open sheet", "https://docs.google.com/spreadsheets/d/abc/edit"))
        summary = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                          runner=FakeRunner())[0]
        self.assertNotIn("📊 Open sheet", [b[0] for b in summary["buttons"]])

    def test_sms_button_errors_when_there_is_no_draft(self):
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                runner=FakeRunner(dict(RESULT, seller_sms="")))
        with self.assertRaisesRegex(process.ProcessError, "no SMS draft"):
            process.seller_text(self.root, 1, "sms")

    def test_no_sheet_configured_means_no_push(self):
        pushed = []
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner(),
                                pusher=lambda row, fields: pushed.append(row))
        self.assertEqual(pushed, [])

    def test_contact_for_errors(self):
        with self.assertRaisesRegex(process.ProcessError, "not been analysed"):
            process.contact_for(self.root, 1, "600123456")
        process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=FakeRunner())
        with self.assertRaisesRegex(process.ProcessError, "doesn't look like a phone number"):
            process.contact_for(self.root, 1, "12")

    def test_seller_not_shown(self):
        text = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                       runner=FakeRunner(dict(RESULT, seller_name=None)))[0]["text"]
        self.assertNotIn("Seller:", text)

    def test_empty_criteria_says_so(self):
        text = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                       runner=FakeRunner(dict(RESULT, criteria_checks=[])))[0]["text"]
        self.assertIn("Score – (fill in criteria.md)", text)

    def test_missing_values_are_reported_not_invented(self):
        data = dict(RESULT, price_pln=None, mileage_km=None)
        text = process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                       runner=FakeRunner(data))[0]["text"]
        self.assertIn("price not shown · Łódź", text)

    def test_claude_error_raises(self):
        runner = FakeRunner(stdout=json.dumps({"is_error": True, "result": "Not logged in"}))
        with self.assertRaisesRegex(process.ProcessError, "Not logged in"):
            process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus", runner=runner)
        with self.assertRaisesRegex(process.ProcessError, "exit 1"):
            process.process_listing(self.root, 1, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                    runner=FakeRunner(stdout="", returncode=1))

    def test_unknown_listing(self):
        with self.assertRaises(process.ProcessError):
            process.process_listing(self.root, 9, settings=SETTINGS, domain=CAMPERVAN, model="opus",
                                    runner=FakeRunner())


if __name__ == "__main__":
    unittest.main()
