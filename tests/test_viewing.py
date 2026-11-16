import json
import tempfile
import unittest
from pathlib import Path

from deal_scout import viewing
from deal_scout.errors import ProcessError

DATA = {"title": "Acme <3>", "viewing_checks": ["Battery", "Screen", "Charger"], "missing": ["VIN"], "red_flags": []}


class ViewingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        (self.root / "listings").mkdir()
        (self.root / "inbox" / "001").mkdir(parents=True)
        self.write(DATA)

    def write(self, data):
        (self.root / "listings" / "001.json").write_text(json.dumps(data), encoding="utf-8")

    def stored(self):
        return json.loads((self.root / "listings" / "001.json").read_text(encoding="utf-8"))

    def handle(self, action, *args):
        return viewing.handle(self.root, 1, action, list(args), clock=lambda: 1000.0)

    def test_start_freezes_the_checklist_and_sends_it_as_a_new_message(self):
        reply = self.handle("start")
        self.assertFalse(reply.edit)
        self.assertEqual([b[1] for b in reply.message.buttons][:3], ["vc:1:0", "vc:1:1", "vc:1:2"])
        v = self.stored()["viewing"]
        self.assertEqual((v["items"], v["marks"]), (["Battery", "Screen", "Charger"], {}))
        self.assertRegex(v["started"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_start_without_the_models_list_uses_the_fallback(self):
        self.write({"title": "T", "missing": ["VIN"], "red_flags": ["Urgent"]})
        self.handle("start")
        self.assertEqual(self.stored()["viewing"]["items"], ["Verify: VIN", "Check: Urgent"])

    def test_start_with_nothing_to_check_says_so(self):
        self.write({"title": "T"})
        with self.assertRaisesRegex(ProcessError, "nothing to check"):
            self.handle("start")

    def test_toggle_cycles_and_survives_a_restart(self):
        self.handle("start")
        reply = self.handle("toggle", "1")
        self.assertTrue(reply.edit)
        self.assertEqual(reply.message.buttons[1][0], "✅ Screen")
        self.assertEqual(self.stored()["viewing"]["marks"], {"1": "ok"})
        self.assertEqual(
            self.handle("toggle", "1").message.buttons[1][0], "❌ Screen"
        )  # state read from disk each time
        self.assertEqual(self.handle("toggle", "1").message.buttons[1][0], "☐ Screen")
        self.assertEqual(self.stored()["viewing"]["marks"], {})

    def test_reopening_keeps_the_marks(self):
        self.handle("start")
        self.handle("toggle", "0")
        reply = self.handle("start")
        self.assertEqual(reply.message.buttons[0][0], "✅ Battery")

    def test_a_broken_or_old_button_is_refused(self):
        with self.assertRaisesRegex(ProcessError, "out of date"):
            self.handle("toggle", "0")  # no viewing started
        self.handle("start")
        for bad in (["9"], ["x"], []):
            with self.assertRaisesRegex(ProcessError, "out of date"):
                self.handle("toggle", *bad)

    def test_finish_writes_the_results_and_the_tally(self):
        self.handle("start")
        self.handle("toggle", "0")
        self.handle("toggle", "2")
        self.handle("toggle", "2")
        reply = self.handle("finish")
        self.assertTrue(reply.edit)
        self.assertEqual(reply.message.text, "🔍 Viewing #1 finished: 1 fine, 1 problem, 1 not checked.")
        text = (self.root / "inbox/001/viewing_checks.txt").read_text(encoding="utf-8")
        self.assertEqual(text, "✅ fine: Battery\n☐ not checked: Screen\n❌ problem: Charger\n")

    def test_cancel_keeps_everything(self):
        self.handle("start")
        self.handle("toggle", "0")
        reply = self.handle("cancel")
        self.assertEqual(reply.extra[0].text, "🔍 #1: viewing paused; tap 🔍 Viewing to continue.")
        self.assertEqual(self.stored()["viewing"]["marks"], {"0": "ok"})

    def test_unknown_action_and_unanalysed_listing(self):
        with self.assertRaises(ProcessError):
            self.handle("explode")
        with self.assertRaisesRegex(ProcessError, "not been analysed"):
            viewing.handle(self.root, 2, "start", [])
