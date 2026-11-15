import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
import bot as botmod  # noqa: E402


class FakeAPI:
    def __init__(self, refuse_buttons=False):
        self.sent = []
        self.buttons = []
        self.refuse_buttons = refuse_buttons

    def send_message(self, chat_id, text, buttons=None, html=False):
        self.html = getattr(self, "html", []) + [html]
        if (buttons or html) and self.refuse_buttons:
            raise RuntimeError("Telegram error in sendMessage: BUTTON_URL_INVALID")
        self.sent.append(text)
        self.buttons.append(buttons)

    def answer_callback(self, callback_id):
        self.answered = getattr(self, "answered", []) + [callback_id]

    def get_file_bytes(self, file_id):
        return b"IMG:" + file_id.encode()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def msg(user=1, text=None, photo=None, group=None, caption=None):
    m = {"from": {"id": user}, "chat": {"id": user}, "date": 1000}
    if text is not None:
        m["text"] = text
    if caption is not None:
        m["caption"] = caption
    if photo:
        m["photo"] = [{"file_id": photo + "-small"}, {"file_id": photo}]
    if group:
        m["media_group_id"] = group
    return {"update_id": 1, "message": m}


class BotTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.api = FakeAPI()
        self.clock = Clock()
        self.bot = botmod.Bot(self.api, self.root, 1, window_seconds=180, clock=self.clock)

    def tearDown(self):
        self.dir.cleanup()

    def test_stranger_gets_nothing(self):
        self.bot.handle_update(msg(user=99, text="hi"))
        self.assertEqual(self.api.sent, [])
        self.assertEqual(list((self.root / "inbox").iterdir()), [])

    def test_setup_mode_reveals_id_and_saves_nothing(self):
        bot = botmod.Bot(self.api, self.root, None, clock=self.clock)
        bot.handle_update(msg(user=42, text="hi"))
        self.assertIn("42", self.api.sent[0])
        self.assertEqual(list((self.root / "inbox").iterdir()), [])

    def test_photo_and_link_group_into_one_listing(self):
        self.bot.handle_update(msg(photo="a"))
        self.clock.t += 20
        self.bot.handle_update(msg(text="https://www.facebook.com/marketplace/item/123 nice van"))
        self.clock.t += 3
        self.bot.tick()
        folders = sorted(p.name for p in (self.root / "inbox").iterdir())
        self.assertEqual(folders, ["001"])
        self.assertEqual(len(list((self.root / "inbox/001").glob("photo_*"))), 1)
        self.assertIn("https://www.facebook.com/marketplace/item/123",
                      (self.root / "inbox/001/links.txt").read_text())
        self.assertIn("#1: 1 photo(s), 1 link(s)", self.api.sent[-1])

    def test_window_expiry_starts_new_listing(self):
        self.bot.handle_update(msg(photo="a"))
        self.clock.t += 500
        self.bot.handle_update(msg(photo="b"))
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001", "002"])

    def test_new_command_forces_new_listing(self):
        self.bot.handle_update(msg(photo="a"))
        self.bot.handle_update(msg(text="/new"))
        self.bot.handle_update(msg(photo="b"))
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001", "002"])

    def test_album_is_one_listing_and_acknowledged_once(self):
        self.bot.handle_update(msg(photo="first"))  # one screenshot on its own, then an album: the real case
        for pid in ("a", "b", "c"):
            self.bot.handle_update(msg(photo=pid, group="g1"))
        self.bot.tick()
        self.assertEqual(self.api.sent, [])  # still arriving: nothing yet
        self.clock.t += 3
        self.bot.tick()
        self.bot.tick()
        self.assertEqual(len(list((self.root / "inbox/001").glob("photo_*"))), 4)
        self.assertEqual(self.api.sent, ["#1: 4 photo(s), 0 link(s) saved."])

    def test_command_confirms_pending_saves_first(self):
        bot, _ = self.analysing_bot()
        bot.handle_update(msg(photo="a"))
        bot.handle_update(msg(text="/done"))
        self.assertEqual(self.api.sent[:2], ["#1: 1 photo(s), 0 link(s) saved.", "#1: analysing, usually about a minute..."])

    def test_new_listing_confirms_the_previous_one(self):
        self.bot.handle_update(msg(photo="a"))
        self.clock.t += 500
        self.bot.handle_update(msg(photo="b"))
        self.assertEqual(self.api.sent, ["#1: 1 photo(s), 0 link(s) saved."])

    def test_duplicate_links_saved_once(self):
        self.bot.handle_update(msg(text="https://x.pl/a"))
        self.bot.handle_update(msg(text="again https://x.pl/a"))
        self.assertEqual((self.root / "inbox/001/links.txt").read_text().split(), ["https://x.pl/a"])

    def test_list_command(self):
        self.bot.handle_update(msg(photo="a", caption="https://x.pl/1"))
        self.bot.handle_update(msg(text="/list"))
        self.assertIn("#1  1 photo(s)  https://x.pl/1", self.api.sent[-1])

    def test_state_survives_restart(self):
        self.bot.handle_update(msg(photo="a"))
        self.bot.save_state()
        again = botmod.Bot(self.api, self.root, 1, window_seconds=180, clock=self.clock)
        again.handle_update(msg(photo="b"))
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001"])

    def analysing_bot(self, auto=False, fail=False):
        done = []

        def processor(n):
            if fail:
                raise RuntimeError("claude not found")
            done.append(n)
            return ["#%d analysed" % n, "Dzień dobry"] if n == 2 else "#%d analysed" % n
        bot = botmod.Bot(self.api, self.root, 1, window_seconds=180, clock=self.clock, processor=processor,
                         auto_analyse=auto)
        return bot, done

    def drain(self, bot):
        while not bot.jobs.empty():
            bot.run_job(bot.jobs.get())

    def test_done_analyses_current_listing_and_closes_it(self):
        bot, done = self.analysing_bot()
        bot.handle_update(msg(photo="a"))
        bot.handle_update(msg(text="/done"))
        self.drain(bot)
        self.assertEqual(done, [1])
        self.assertEqual(self.api.sent[-1], "#1 analysed")
        bot.handle_update(msg(photo="b"))
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001", "002"])

    def test_done_with_number_reanalyses(self):
        bot, done = self.analysing_bot()
        bot.handle_update(msg(photo="a"))
        bot.handle_update(msg(text="/done #1"))
        bot.handle_update(msg(text="/done 5"))
        self.drain(bot)
        self.assertEqual(done, [1])
        self.assertIn("There is no #5.", self.api.sent)

    def test_summary_and_seller_message_sent_separately(self):
        bot, _ = self.analysing_bot()
        bot.handle_update(msg(photo="a"))
        bot.handle_update(msg(text="/new"))
        bot.handle_update(msg(photo="b"))
        bot.handle_update(msg(text="/done"))
        self.drain(bot)
        self.assertEqual(self.api.sent[-2:], ["#2 analysed", "Dzień dobry"])

    def test_whatsapp_button_is_sent(self):
        bot, _ = self.analysing_bot()
        bot.send({"text": "Dzień dobry", "buttons": [("Send on WhatsApp", "https://wa.me/48600123456?text=x")],
                  "fallback_url": "https://wa.me/48600123456"})
        self.assertEqual(self.api.sent, ["Dzień dobry"])
        self.assertEqual(self.api.buttons[0][0][1], "https://wa.me/48600123456?text=x")

    def test_refused_button_falls_back_to_plain_link(self):
        self.api.refuse_buttons = True
        bot, _ = self.analysing_bot()
        bot.send({"text": "Dzień dobry", "buttons": [("Send on WhatsApp", "https://wa.me/1?text=x")],
                  "fallback_url": "https://wa.me/48600123456"})
        self.assertEqual(self.api.sent[0], "Dzień dobry")
        self.assertIn("https://wa.me/48600123456", self.api.sent[1])

    def test_refused_html_falls_back_to_plain_text(self):
        self.api.refuse_buttons = True
        bot, _ = self.analysing_bot()
        bot.send({"text": "<b>#1 Ducato</b> &amp; more", "html": True})
        self.assertEqual(self.api.sent, ["#1 Ducato & more"])

    def test_status_reports_stale_code(self):
        self.bot.handle_update(msg(text="/status"))
        self.assertIn("Analysis: off", self.api.sent[-1])
        self.assertNotIn("restart", self.api.sent[-1])
        self.bot.version = "old"
        self.bot.handle_update(msg(text="/status"))
        self.assertIn("restart it", self.api.sent[-1])

    def test_market_command(self):
        self.bot.handle_update(msg(text="/market"))
        self.assertIn("No market data yet", self.api.sent[-1])
        (self.root / "listings").mkdir()
        (self.root / "listings" / "market.md").write_text("# Market prices\nmedian 84k")
        self.bot.handle_update(msg(text="/market"))
        self.assertIn("median 84k", self.api.sent[-1])

    def tap_bot(self):
        calls = []

        def seller_text(n, kind, phone):
            calls.append((n, kind, phone))
            return [{"text": "%s for #%d" % (kind, n)}]
        bot = botmod.Bot(self.api, self.root, 1, clock=self.clock, seller_text=seller_text)
        return bot, calls

    def test_tap_on_message_button_sends_the_draft(self):
        bot, calls = self.tap_bot()
        bot.handle_update({"update_id": 5, "callback_query": {"id": "c1", "from": {"id": 1}, "data": "msg:7:48600123456"}})
        bot.handle_update({"update_id": 6, "callback_query": {"id": "c2", "from": {"id": 1}, "data": "sms:7"}})
        self.assertEqual(calls, [(7, "msg", "48600123456"), (7, "sms", None)])
        self.assertEqual(self.api.sent, ["msg for #7", "sms for #7"])
        self.assertEqual(self.api.answered, ["c1", "c2"])

    def test_stranger_tap_is_ignored(self):
        bot, calls = self.tap_bot()
        bot.handle_update({"update_id": 5, "callback_query": {"id": "c1", "from": {"id": 99}, "data": "msg:7:"}})
        self.assertEqual((calls, self.api.sent), ([], []))

    def test_bad_tap_data_is_ignored(self):
        bot, calls = self.tap_bot()
        bot.handle_update({"update_id": 5, "callback_query": {"id": "c1", "from": {"id": 1}, "data": "rm:-rf"}})
        self.assertEqual(calls, [])

    def test_callback_buttons_are_not_listed_in_plain_fallback(self):
        self.api.refuse_buttons = True
        bot, _ = self.analysing_bot()
        bot.send({"text": "<b>#1</b>", "html": True, "buttons": [("💬 WhatsApp message", "msg:1:"),
                                                                ("📊 Open sheet", "https://docs.google.com/x")]})
        self.assertEqual(self.api.sent, ["#1", "📊 Open sheet: https://docs.google.com/x"])

    def test_usage_command(self):
        self.bot.handle_update(msg(text="/usage"))
        self.assertIn("Last 7 days: nothing", self.api.sent[-1])

    def test_sheet_command(self):
        self.bot.handle_update(msg(text="/sheet"))
        self.assertIn("GOOGLE_SHEET_LINK", self.api.sent[-1])
        bot = botmod.Bot(self.api, self.root, 1, clock=self.clock, sheet_link="https://docs.google.com/spreadsheets/d/a")
        bot.handle_update(msg(text="/sheet"))
        self.assertEqual(self.api.buttons[-1], [("📊 Open sheet", "https://docs.google.com/spreadsheets/d/a")])

    def test_refused_summary_button_falls_back_to_plain_link(self):
        self.api.refuse_buttons = True
        bot, _ = self.analysing_bot()
        bot.send({"text": "<b>#1</b>", "html": True, "buttons": [("📊 Open sheet", "https://docs.google.com/x")]})
        self.assertEqual(self.api.sent, ["#1", "📊 Open sheet: https://docs.google.com/x"])

    def test_phone_command(self):
        calls = []

        def contact(n, phone):
            calls.append((n, phone))
            return [{"text": "Dzień dobry", "buttons": [("Send on WhatsApp", "https://wa.me/48600123456?text=x")]}]
        bot = botmod.Bot(self.api, self.root, 1, clock=self.clock, contact=contact)
        bot.handle_update(msg(text="/phone 7 600 123 456"))
        self.assertEqual(calls, [(7, "600 123 456")])
        self.assertEqual(self.api.sent, ["Dzień dobry"])
        self.assertFalse((self.root / "inbox").exists() and list((self.root / "inbox").iterdir()))
        bot.handle_update(msg(text="/phone 7"))
        self.assertIn("Usage", self.api.sent[-1])

    def test_phone_command_error_is_reported(self):
        def contact(n, phone):
            raise ValueError("#7 has not been analysed yet")
        bot = botmod.Bot(self.api, self.root, 1, clock=self.clock, contact=contact)
        bot.handle_update(msg(text="/phone 7 600123456"))
        self.assertIn("not been analysed", self.api.sent[-1])

    def test_done_when_analysis_is_off(self):
        self.bot.handle_update(msg(photo="a"))
        self.bot.handle_update(msg(text="/done"))
        self.assertIn("Analysis is off", self.api.sent[-1])

    def test_stranger_cannot_trigger_analysis(self):
        bot, done = self.analysing_bot()
        bot.handle_update(msg(photo="a"))
        bot.handle_update(msg(user=99, text="/done 1"))
        self.drain(bot)
        self.assertEqual(done, [])

    def test_auto_analysis_after_window(self):
        bot, done = self.analysing_bot(auto=True)
        bot.handle_update(msg(photo="a"))
        self.clock.t += 100
        bot.tick()
        self.assertTrue(bot.jobs.empty())
        self.clock.t += 200
        bot.tick()
        bot.tick()  # only once
        self.drain(bot)
        self.assertEqual(done, [1])

    def test_failed_analysis_is_reported(self):
        bot, _ = self.analysing_bot(fail=True)
        bot.handle_update(msg(photo="a"))
        bot.handle_update(msg(text="/done"))
        self.drain(bot)
        self.assertIn("analysis failed: claude not found", self.api.sent[-1])

    def test_env_file_does_not_override(self):
        env = self.root / ".env"
        env.write_text("# c\nFOO_TEST_KEY=1\nBAR_TEST_KEY='x y'\n")
        import os
        os.environ["FOO_TEST_KEY"] = "keep"
        botmod.load_env(env)
        self.assertEqual(os.environ["FOO_TEST_KEY"], "keep")
        self.assertEqual(os.environ["BAR_TEST_KEY"], "x y")


if __name__ == "__main__":
    unittest.main()
