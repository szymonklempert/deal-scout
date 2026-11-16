import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from deal_scout import bot as botmod
from deal_scout.feedback_menu import FixReply
from deal_scout.render import Message


class FakeAPI:
    def __init__(self, refuse_buttons=False):
        self.sent = []
        self.buttons = []
        self.refuse_buttons = refuse_buttons
        self.refuse_edit = False
        self.edited = []

    def send_message(self, chat_id, text, buttons=None, html=False, columns=1):
        self.html = getattr(self, "html", []) + [html]
        if (buttons or html) and self.refuse_buttons:
            raise RuntimeError("Telegram error in sendMessage: BUTTON_URL_INVALID")
        self.sent.append(text)
        self.buttons.append(buttons)

    def edit_message(self, chat_id, message_id, text, buttons=None, html=False, columns=1):
        if self.refuse_edit:
            raise RuntimeError("Telegram error in editMessageText: message can't be edited")
        self.edited.append((message_id, text, buttons, columns))

    def answer_callback(self, callback_id):
        self.answered = getattr(self, "answered", []) + [callback_id]

    def get_file_bytes(self, file_id):
        if file_id.startswith("lost"):  # TelegramAPI already retried; it still timed out
            raise TimeoutError("The read operation timed out")
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
        self.assertIn("https://www.facebook.com/marketplace/item/123", (self.root / "inbox/001/links.txt").read_text())
        self.assertIn("#1: 1 photo(s), 1 link(s)", self.api.sent[-1])

    def test_photo_that_cannot_be_downloaded_is_reported_and_the_rest_kept(self):
        self.bot.handle_update(msg(photo="a", group="g"))
        self.bot.handle_update(msg(photo="lost-b", group="g"))
        self.bot.handle_update(msg(photo="c", group="g", caption="https://example.com/x"))
        self.assertEqual(
            self.api.sent,
            ["#1: 1 photo(s) couldn't be downloaded (Telegram didn't answer in time). Please send it again."],
        )
        self.clock.t += 3
        self.bot.tick()
        self.assertEqual(len(list((self.root / "inbox/001").glob("photo_*"))), 2)
        self.assertIn("#1: 2 photo(s), 1 link(s)", self.api.sent[-1])

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
        self.assertEqual(
            self.api.sent[:2], ["#1: 1 photo(s), 0 link(s) saved.", "#1: analysing, usually about a minute..."]
        )

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
            return [f"#{n} analysed", "Dzień dobry"] if n == 2 else f"#{n} analysed"

        bot = botmod.Bot(
            self.api, self.root, 1, window_seconds=180, clock=self.clock, processor=processor, auto_analyse=auto
        )
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
        bot.send(
            Message(
                "Dzień dobry",
                buttons=(("Send on WhatsApp", "https://wa.me/48600123456?text=x"),),
                fallback_url="https://wa.me/48600123456",
            )
        )
        self.assertEqual(self.api.sent, ["Dzień dobry"])
        self.assertEqual(self.api.buttons[0][0][1], "https://wa.me/48600123456?text=x")

    def test_refused_button_falls_back_to_plain_link(self):
        self.api.refuse_buttons = True
        bot, _ = self.analysing_bot()
        bot.send(
            Message(
                "Dzień dobry",
                buttons=(("Send on WhatsApp", "https://wa.me/1?text=x"),),
                fallback_url="https://wa.me/48600123456",
            )
        )
        self.assertEqual(self.api.sent[0], "Dzień dobry")
        self.assertIn("https://wa.me/48600123456", self.api.sent[1])

    def test_refused_html_falls_back_to_plain_text(self):
        self.api.refuse_buttons = True
        bot, _ = self.analysing_bot()
        bot.send(Message("<b>#1 Ducato</b> &amp; more", html=True))
        self.assertEqual(self.api.sent, ["#1 Ducato & more"])

    def test_status_reports_stale_code(self):
        self.bot.handle_update(msg(text="/status"))
        self.assertIn("Analysis: off", self.api.sent[-1])
        self.assertNotIn("restart", self.api.sent[-1])
        self.bot.version = "old"
        self.bot.handle_update(msg(text="/status"))
        self.assertIn("restart it", self.api.sent[-1])

    def test_code_version_fingerprints_the_package_files(self):
        files = sorted(Path(botmod.__file__).resolve().parent.glob("*.py"))
        self.assertIn("bot.py", [p.name for p in files])
        expected = hashlib.sha1(b"".join(p.read_bytes() for p in files)).hexdigest()[:8]
        self.assertEqual(botmod.code_version(), expected)

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
            return [Message(f"{kind} for #{n}")]

        bot = botmod.Bot(self.api, self.root, 1, clock=self.clock, seller_text=seller_text)
        return bot, calls

    def test_tap_on_message_button_sends_the_draft(self):
        bot, calls = self.tap_bot()
        bot.handle_update(
            {"update_id": 5, "callback_query": {"id": "c1", "from": {"id": 1}, "data": "msg:7:48600123456"}}
        )
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
        bot.send(
            Message(
                "<b>#1</b>",
                html=True,
                buttons=(("💬 WhatsApp message", "msg:1:"), ("📊 Open sheet", "https://docs.google.com/x")),
            )
        )
        self.assertEqual(self.api.sent, ["#1", "📊 Open sheet: https://docs.google.com/x"])

    def test_edit_changes_a_message_in_place(self):
        self.bot.edit(1, 55, Message("new", buttons=(("A", "fb:1"),), columns=3))
        self.assertEqual(self.api.edited, [(55, "new", [("A", "fb:1")], 3)])
        self.assertEqual(self.api.sent, [])

    def test_refused_edit_sends_a_new_message(self):
        self.api.refuse_edit = True
        self.bot.edit(1, 55, Message("new"))
        self.assertEqual((self.api.edited, self.api.sent), ([], ["new"]))

    def fix_bot(self, ask=True):
        calls = []

        def fixer(n, action, args):
            calls.append((n, action, args))
            if action == "reason":
                return FixReply(extra=(Message("Noted."),))
            return FixReply(
                Message("menu", buttons=(("A", f"fb:{n}"),), columns=3),
                edit=action != "open",
                ask_reason="Solar" if ask and action == "value" else None,
            )

        return botmod.Bot(self.api, self.root, 1, clock=self.clock, fixer=fixer), calls

    def fix_tap(self, bot, data, user=1, message_id=77):
        bot.handle_update(
            {
                "update_id": 5,
                "callback_query": {
                    "id": "c",
                    "from": {"id": user},
                    "data": data,
                    "message": {"message_id": message_id, "chat": {"id": 1}},
                },
            }
        )

    def test_stranger_fix_tap_is_ignored(self):
        bot, calls = self.fix_bot()
        self.fix_tap(bot, "fv:1:0:3", user=99)
        self.assertEqual((calls, self.api.sent, self.api.edited), ([], [], []))

    def test_fix_tap_routes_and_edits_the_tapped_message(self):
        bot, calls = self.fix_bot()
        self.fix_tap(bot, "fs:1:4")
        self.fix_tap(bot, "fx:1")
        self.assertEqual(calls, [(1, "spec", ["4"]), (1, "open", [])])
        self.assertEqual(self.api.edited, [(77, "menu", [("A", "fb:1")], 3)])
        self.assertEqual(self.api.sent, ["menu"])  # "open" sends a new message

    def test_fix_error_is_reported(self):
        bot, _ = self.fix_bot()

        def broken(n, action, args):
            raise ValueError("#1 has not been analysed yet")

        bot.fixer = broken
        self.fix_tap(bot, "fx:1")
        self.assertEqual(self.api.sent, ["#1: #1 has not been analysed yet"])

    def test_pending_reason_takes_the_next_plain_text(self):
        bot, calls = self.fix_bot()
        self.fix_tap(bot, "fv:1:0:3")
        bot.handle_update(msg(text="panel is only 100 W"))
        self.assertEqual(calls[-1], (1, "reason", ["Solar", "panel is only 100 W"]))
        self.assertEqual(self.api.sent, ["Noted."])
        self.assertFalse((self.root / "inbox").exists() and list((self.root / "inbox").iterdir()))
        bot.handle_update(msg(text="second note"))  # the reason was taken: this is an ordinary note
        self.assertEqual(len(calls), 2)
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001"])

    def test_url_photo_or_command_clears_the_pending_reason(self):
        for update in (msg(text="see https://x.pl/a"), msg(photo="a"), msg(text="/list")):
            bot, calls = self.fix_bot()
            self.fix_tap(bot, "fv:1:0:3")
            bot.handle_update(update)
            bot.handle_update(msg(text="late reason"))
            self.assertEqual([c[1] for c in calls], ["value"])
        self.assertTrue((self.root / "inbox/001/links.txt").exists())

    def test_other_taps_and_expiry_clear_the_pending_reason(self):
        bot, calls = self.fix_bot()
        self.fix_tap(bot, "fv:1:0:3")
        self.fix_tap(bot, "fr:1")
        bot.handle_update(msg(text="late"))
        self.assertEqual([c[1] for c in calls], ["value", "skip"])
        self.fix_tap(bot, "fv:1:0:3")
        self.clock.t += 601
        bot.handle_update(msg(text="too late"))
        self.assertEqual([c[1] for c in calls], ["value", "skip", "value"])

    def test_feedback_phone_is_remembered_in_memory_for_done_only(self):
        bot, calls = self.fix_bot()
        self.fix_tap(bot, "fx:1:48600111222")
        self.fix_tap(bot, "fx:2:")
        self.assertEqual(bot.feedback_phone, {1: "48600111222"})
        self.fix_tap(bot, "fd:2")
        self.fix_tap(bot, "fd:1")
        self.fix_tap(bot, "fd:1")
        self.assertEqual(calls[2:], [(2, "done", []), (1, "done", ["48600111222"]), (1, "done", [])])
        self.assertEqual(bot.feedback_phone, {})
        self.assertFalse((self.root / ".state.json").exists() and "4860" in (self.root / ".state.json").read_text())

    def reply_bot(self, auto=False, processor=True):
        bot, done = self.analysing_bot(auto=auto)
        if not processor:
            bot = botmod.Bot(self.api, self.root, 1, window_seconds=180, clock=self.clock)
        bot.handle_update(msg(photo="a"))
        bot.handle_update(msg(text="/new"))
        self.api.sent.clear()
        return bot, done

    def tap_reply(self, bot, data, user=1):
        bot.handle_update({"update_id": 5, "callback_query": {"id": "c", "from": {"id": user}, "data": data}})

    def test_stranger_seller_replied_tap_is_ignored(self):
        bot, _ = self.reply_bot()
        self.tap_reply(bot, "sr:1", user=99)
        self.assertEqual((bot.attach.pending, self.api.sent), (None, []))

    def test_seller_replied_tap_starts_reply_mode_and_clears_the_pending_reason(self):
        bot, calls = self.fix_bot()
        bot.handle_update(msg(photo="a"))
        self.api.sent.clear()
        self.fix_tap(bot, "fv:1:0:3")
        self.tap_reply(bot, "sr:1")
        self.assertIsNone(bot.pending_reason)
        self.assertEqual(
            self.api.sent[-1],
            "📨 #1: send the seller's reply: paste the text or send screenshots. Tap Re-analyse when done.",
        )
        self.assertEqual(self.api.buttons[-1], [("✅ Re-analyse", "sa:1"), ("Cancel", "sc:1")])
        bot.handle_update(msg(text="late reason"))
        self.assertEqual([c[1] for c in calls], ["value"])  # taken as the seller's reply, not as a reason

    def test_messages_in_reply_mode_go_to_the_listing_and_are_confirmed_once(self):
        bot, _ = self.reply_bot()
        self.tap_reply(bot, "sr:1")
        self.api.sent.clear()
        bot.handle_update(msg(photo="shot"))
        self.clock.t += 20
        bot.handle_update(msg(text="VIN is ABC https://x.pl/vin"))
        bot.tick()
        self.assertEqual(self.api.sent, [])
        self.clock.t += 3
        bot.tick()
        bot.tick()
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001"])  # no new listing
        folder = self.root / "inbox/001"
        self.assertEqual(len(list(folder.glob("reply_*"))), 1)
        self.assertIn("VIN is ABC", (folder / "replies.txt").read_text())
        self.assertFalse((folder / "notes.txt").exists() or (folder / "links.txt").exists())
        self.assertEqual(self.api.sent, ["📨 #1: 1 photo(s), 1 text(s) saved as the seller's reply."])
        self.assertIsNone(bot.state["current"])

    def test_each_message_extends_reply_mode(self):
        bot, _ = self.reply_bot()
        self.tap_reply(bot, "sr:1")
        self.clock.t += 800
        bot.handle_update(msg(text="one"))
        self.clock.t += 800
        bot.tick()
        self.assertIsNotNone(bot.attach.pending)
        bot.handle_update(msg(text="two"))
        self.assertEqual((self.root / "inbox/001/replies.txt").read_text().count("\n"), 2)

    def test_done_button_and_command_reanalyse_the_listing(self):
        bot, done = self.reply_bot()
        self.tap_reply(bot, "sr:1")
        bot.handle_update(msg(text="answer"))
        self.tap_reply(bot, "sa:1")
        self.assertIsNone(bot.attach.pending)
        self.assertEqual(
            self.api.sent[-2:],
            ["📨 #1: 0 photo(s), 1 text(s) saved as the seller's reply.", "#1: analysing, usually about a minute..."],
        )
        self.tap_reply(bot, "sr:1")
        bot.handle_update(msg(text="/done"))
        self.assertIsNone(bot.attach.pending)
        self.drain(bot)
        self.assertEqual(done, [1, 1])

    def test_done_in_reply_mode_with_analysis_off(self):
        bot, _ = self.reply_bot(processor=False)
        self.tap_reply(bot, "sr:1")
        bot.handle_update(msg(text="/done"))
        self.assertIn("Analysis is off", self.api.sent[-1])
        self.assertTrue(bot.jobs.empty())

    def test_cancel_ends_reply_mode_and_keeps_what_was_sent(self):
        bot, done = self.reply_bot()
        self.tap_reply(bot, "sr:1")
        bot.handle_update(msg(text="answer"))
        self.tap_reply(bot, "sc:1")
        self.assertIsNone(bot.attach.pending)
        self.assertEqual(self.api.sent[-1], "📨 #1: reply mode ended; what you sent is kept. Tap 📨 again to add more.")
        self.assertTrue((self.root / "inbox/001/replies.txt").exists())
        bot.handle_update(msg(text="a plain note"))  # reply mode is over: a new listing
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001", "002"])
        self.assertEqual(done, [])

    def test_another_command_ends_reply_mode_then_runs(self):
        bot, _ = self.reply_bot()
        self.tap_reply(bot, "sr:1")
        bot.handle_update(msg(text="/list"))
        self.assertIsNone(bot.attach.pending)
        self.assertEqual(self.api.sent[-2], "📨 #1: reply mode ended; what you sent is kept. Tap 📨 again to add more.")
        self.assertIn("#1  1 photo(s)", self.api.sent[-1])

    def test_expiry_without_auto_analysis_offers_the_button(self):
        bot, _ = self.reply_bot()
        self.tap_reply(bot, "sr:1")
        bot.handle_update(msg(text="answer"))
        self.clock.t += 901
        bot.tick()
        self.assertIsNone(bot.attach.pending)
        self.assertEqual(
            self.api.sent[-1], "📨 #1: reply mode ended after 15 minutes of quiet. Re-analyse when you're ready:"
        )
        self.assertEqual(self.api.buttons[-1], [("✅ Re-analyse", "sa:1")])
        self.assertTrue(bot.jobs.empty())

    def test_expiry_with_auto_analysis_queues_it_only_when_something_was_saved(self):
        bot, done = self.reply_bot(auto=True)
        self.tap_reply(bot, "sr:1")
        bot.handle_update(msg(text="answer"))
        self.clock.t += 901
        bot.tick()
        self.drain(bot)
        self.assertEqual(done, [1])
        bot, done = self.reply_bot(auto=True)
        self.tap_reply(bot, "sr:1")
        self.clock.t += 901
        bot.tick()
        self.assertTrue(bot.jobs.empty())
        self.assertEqual(self.api.buttons[-1], [("✅ Re-analyse", "sa:1")])

    def test_a_message_after_expiry_is_an_ordinary_one(self):
        bot, _ = self.reply_bot()
        self.tap_reply(bot, "sr:1")
        self.clock.t += 901
        bot.handle_update(msg(text="new listing note"))
        self.assertIn("reply mode ended after 15 minutes", self.api.sent[1])
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001", "002"])

    def test_usage_command(self):
        self.bot.handle_update(msg(text="/usage"))
        self.assertIn("Last 7 days: nothing", self.api.sent[-1])

    def test_sheet_command(self):
        self.bot.handle_update(msg(text="/sheet"))
        self.assertIn("GOOGLE_SHEET_LINK", self.api.sent[-1])
        bot = botmod.Bot(
            self.api, self.root, 1, clock=self.clock, sheet_link="https://docs.google.com/spreadsheets/d/a"
        )
        bot.handle_update(msg(text="/sheet"))
        self.assertEqual(self.api.buttons[-1], [("📊 Open sheet", "https://docs.google.com/spreadsheets/d/a")])

    def test_refused_summary_button_falls_back_to_plain_link(self):
        self.api.refuse_buttons = True
        bot, _ = self.analysing_bot()
        bot.send(Message("<b>#1</b>", html=True, buttons=(("📊 Open sheet", "https://docs.google.com/x"),)))
        self.assertEqual(self.api.sent, ["#1", "📊 Open sheet: https://docs.google.com/x"])

    def test_phone_command(self):
        calls = []

        def contact(n, phone):
            calls.append((n, phone))
            return [Message("Dzień dobry", buttons=(("Send on WhatsApp", "https://wa.me/48600123456?text=x"),))]

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


class ReportsRoutingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.api = FakeAPI()
        self.calls = []

        def reports(cmd, args):
            self.calls.append((cmd, list(args)))
            if args == ["boom"]:
                raise ValueError("Usage: nope")
            return [Message("report", html=True)]

        self.bot = botmod.Bot(self.api, Path(self.dir.name), 1, clock=Clock(), reports=reports)

    def tap(self, data, user=1):
        self.bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "from": {"id": user}, "data": data}})

    def test_commands_go_to_the_one_callable(self):
        self.bot.handle_update(msg(text="/top 10"))
        self.bot.handle_update(msg(text="/compare 7 12"))
        self.bot.handle_update(msg(text="/show 7"))
        self.assertEqual(self.calls, [("/top", ["10"]), ("/compare", ["7", "12"]), ("/show", ["7"])])
        self.assertEqual(self.api.sent, ["report"] * 3)

    def test_tappable_show_link_with_and_without_the_bot_name(self):
        self.bot.handle_update(msg(text="/show_12"))
        self.bot.handle_update(msg(text="/show_13@DealBot"))
        self.assertEqual(self.calls, [("/show", ["12"]), ("/show", ["13"])])

    def test_show_button(self):
        self.tap("sh:12")
        self.assertEqual(self.calls, [("/show", ["12"])])
        self.tap("sh:x")
        self.tap("sh:12", user=99)
        self.assertEqual(len(self.calls), 1)

    def test_problem_is_one_line(self):
        self.bot.handle_update(msg(text="/compare boom"))
        self.assertEqual(self.api.sent, ["Usage: nope"])

    def test_not_available_without_the_callable(self):
        bot = botmod.Bot(self.api, Path(self.dir.name), 1, clock=Clock())
        bot.handle_update(msg(text="/top"))
        self.assertEqual(self.api.sent, ["Not available."])

    def test_stranger_gets_nothing(self):
        self.bot.handle_update(msg(user=99, text="/top"))
        self.assertEqual((self.calls, self.api.sent), ([], []))

    def test_help_lists_the_commands(self):
        for word in ("/top", "/compare", "/show"):
            self.assertIn(word, botmod.HELP)


class DigestTests(unittest.TestCase):
    """The morning digest: scheduled in tick(), on request with /digest."""

    TIME = datetime(2026, 11, 16, 9, 30).timestamp()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.api = FakeAPI()
        self.clock = Clock()
        self.clock.t = self.TIME
        self.calls = 0
        self.items = [Message("digest")]

    def digest(self, save):
        self.calls += 1
        self.saves = getattr(self, "saves", []) + [save]
        return self.items

    def make(self, digest_time=(9, 0)):
        return botmod.Bot(self.api, self.root, 1, clock=self.clock, digest=self.digest, digest_time=digest_time)

    def test_sent_once_a_day_after_its_time(self):
        bot = self.make()
        self.clock.t = datetime(2026, 11, 16, 8, 59).timestamp()
        bot.tick()
        self.assertEqual(self.calls, 0)
        self.clock.t = self.TIME
        bot.tick()
        bot.tick()
        self.assertEqual((self.calls, self.api.sent), (1, ["digest"]))
        self.assertEqual(bot.state["last_digest"], "2026-11-16")
        self.clock.t += 86400
        bot.tick()
        self.assertEqual(self.calls, 2)

    def test_restart_after_the_time_catches_up_but_not_twice(self):
        self.make().tick()
        again = self.make()  # restarted later the same day: .state.json remembers
        again.tick()
        self.assertEqual(self.calls, 1)
        self.api.sent.clear()
        (self.root / ".state.json").unlink()
        self.make().tick()  # a fresh state past 09:00 sends it
        self.assertEqual(self.api.sent, ["digest"])

    def test_state_file_from_before_the_digest_loads(self):
        (self.root / ".state.json").write_text('{"offset": 5, "current": null}')
        bot = self.make()
        self.assertEqual(bot.state["offset"], 5)
        self.assertNotIn("last_digest", bot.state)
        bot.tick()
        self.assertEqual(self.calls, 1)

    def test_off_or_not_wired_sends_nothing(self):
        self.make(digest_time=None).tick()
        botmod.Bot(self.api, self.root, 1, clock=self.clock, digest_time=(9, 0)).tick()
        self.assertEqual((self.calls, self.api.sent), (0, []))

    def test_nothing_to_say_is_still_todays_digest(self):
        self.items = []
        bot = self.make()
        bot.tick()
        bot.tick()
        self.assertEqual((self.calls, self.api.sent), (1, []))

    def test_a_failing_digest_is_not_retried_every_tick(self):
        def boom(save):
            self.calls += 1
            raise RuntimeError("no")

        bot = botmod.Bot(self.api, self.root, 1, clock=self.clock, digest=boom, digest_time=(9, 0))
        bot.tick()
        bot.tick()
        self.assertEqual(self.calls, 1)

    def test_command_sends_it_without_changing_last_digest(self):
        bot = self.make(digest_time=None)
        bot.handle_update(msg(text="/digest"))
        self.assertEqual(self.api.sent, ["digest"])
        self.assertNotIn("last_digest", bot.state)
        self.assertEqual(self.saves, [False])  # looking doesn't use up the next morning's market moves

    def test_command_with_nothing_to_report(self):
        self.items = []
        self.make().handle_update(msg(text="/digest"))
        self.assertEqual(self.api.sent, ["Nothing to report today."])

    def test_command_problems(self):
        botmod.Bot(self.api, self.root, 1, clock=self.clock).handle_update(msg(text="/digest"))
        self.assertEqual(self.api.sent, ["Not available."])

        def boom(save):
            raise RuntimeError("disk")

        bot = botmod.Bot(self.api, self.root, 1, clock=self.clock, digest=boom)
        bot.handle_update(msg(text="/digest"))
        self.assertEqual(self.api.sent[-1], "The digest failed: disk")

    def test_stranger_gets_nothing(self):
        self.make().handle_update(msg(user=99, text="/digest"))
        self.assertEqual((self.calls, self.api.sent), (0, []))


if __name__ == "__main__":
    unittest.main()
