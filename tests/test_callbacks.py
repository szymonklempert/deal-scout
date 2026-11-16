"""The bot's button taps: drafts, Feedback, seller replies and viewing checklists."""

import unittest

from test_bot import BotCase, msg

from deal_scout.feedback_menu import FixReply
from deal_scout.render import Message


class CallbackTests(BotCase):
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

    def test_stranger_viewing_taps_are_ignored(self):
        bot, calls, _ = self.viewing_bot()
        for data in ("vw:1", "vc:1:0", "vf:1", "vx:1"):
            self.tap_reply(bot, data, user=99)
        self.assertEqual((calls, bot.attach.pending, self.api.sent), ([], None, []))

    def test_viewing_taps_route_to_the_viewer(self):
        bot, calls, _ = self.viewing_bot()
        self.fix_tap(bot, "vw:1")
        self.fix_tap(bot, "vc:1:3")
        self.assertEqual(calls, [(1, "start", []), (1, "toggle", ["3"])])
        self.assertEqual(self.api.sent, ["list start"])  # the first screen is new, the toggle edits the tapped one
        self.assertEqual(self.api.edited, [(77, "list toggle", [("A", "vc:1:0")], 1)])
        self.assertEqual((bot.attach.pending.kind, bot.attach.pending.n), ("viewing", 1))

    def test_start_clears_a_pending_reason_and_replaces_another_mode(self):
        bot, calls = self.fix_bot()
        bot.viewer = lambda n, action, args: FixReply(Message("list"))
        bot.handle_update(msg(photo="a"))
        self.fix_tap(bot, "fv:1:0:3")
        self.tap_reply(bot, "sr:1")
        self.assertEqual(bot.attach.pending.kind, "reply")
        self.fix_tap(bot, "fv:1:0:3")
        self.fix_tap(bot, "vw:1")
        self.assertIsNone(bot.pending_reason)
        self.assertEqual(bot.attach.pending.kind, "viewing")

    def test_finish_button_ends_the_mode_edits_the_list_and_queues_the_analysis(self):
        bot, calls, done = self.viewing_bot()
        self.fix_tap(bot, "vw:1")
        self.fix_tap(bot, "vf:1")
        self.assertIsNone(bot.attach.pending)
        self.assertEqual(calls[-1], (1, "finish", []))
        self.assertEqual(self.api.edited[-1][1], "list finish")
        self.assertEqual(self.api.sent[-1], "#1: analysing, usually about a minute...")
        self.drain(bot)
        self.assertEqual(done, [1])

    def test_viewer_errors_are_reported_and_leave_the_mode_alone(self):
        bot, _, _ = self.viewing_bot()
        self.fix_tap(bot, "vw:1")
        bot.viewer = lambda n, action, args: (_ for _ in ()).throw(ValueError("out of date"))
        self.fix_tap(bot, "vc:1:0")
        self.assertEqual(self.api.sent[-1], "#1: out of date")
        self.assertIsNotNone(bot.attach.pending)
        bot.viewer = None
        self.fix_tap(bot, "vx:1")  # not wired: ignored
        self.assertIsNotNone(bot.attach.pending)

    def test_finish_with_analysis_off_still_ends_the_viewing(self):
        bot, calls, _ = self.viewing_bot(processor=False)
        self.fix_tap(bot, "vw:1")
        self.fix_tap(bot, "vf:1")
        self.assertIsNone(bot.attach.pending)
        self.assertIn("Analysis is off", self.api.sent[-1])
        self.assertTrue(bot.jobs.empty())

    def test_cancel_ends_the_mode_without_analysing(self):
        bot, calls, done = self.viewing_bot()
        self.fix_tap(bot, "vw:1")
        self.fix_tap(bot, "vx:1")
        self.assertIsNone(bot.attach.pending)
        self.assertEqual(calls[-1], (1, "cancel", []))
        self.assertTrue(bot.jobs.empty())


if __name__ == "__main__":
    unittest.main()
