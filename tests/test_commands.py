"""The bot's slash commands."""

import tempfile
import unittest
from pathlib import Path

from test_bot import BotCase, Clock, FakeAPI, msg

from deal_scout import bot as botmod
from deal_scout import commands
from deal_scout.render import Message


class CommandTests(BotCase):
    def test_new_command_forces_new_listing(self):
        self.bot.handle_update(msg(photo="a"))
        self.bot.handle_update(msg(text="/new"))
        self.bot.handle_update(msg(photo="b"))
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001", "002"])

    def test_list_command(self):
        self.bot.handle_update(msg(photo="a", caption="https://x.pl/1"))
        self.bot.handle_update(msg(text="/list"))
        self.assertIn("#1  1 photo(s)  https://x.pl/1", self.api.sent[-1])

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
            self.assertIn(word, commands.HELP)


if __name__ == "__main__":
    unittest.main()
