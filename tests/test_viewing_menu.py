import unittest

from deal_scout import viewing_menu


class FallbackTests(unittest.TestCase):
    def test_model_list_wins_and_is_capped(self):
        data = {"viewing_checks": [f"check {i}" for i in range(20)], "missing": ["VIN"]}
        self.assertEqual(viewing_menu.viewing_items(data), [f"check {i}" for i in range(15)])

    def test_fallback_uses_missing_then_red_flags(self):
        data = {"missing": ["VIN", "DMC"], "red_flags": ["Urgent sale"]}
        self.assertEqual(viewing_menu.viewing_items(data), ["Verify: VIN", "Verify: DMC", "Check: Urgent sale"])

    def test_fallback_is_capped_and_empty_data_gives_nothing(self):
        data = {"missing": [f"m{i}" for i in range(10)], "red_flags": [f"r{i}" for i in range(10)]}
        self.assertEqual(len(viewing_menu.fallback_checks(data)), 15)
        self.assertEqual(viewing_menu.viewing_items({"viewing_checks": [" "]}), [])


def viewing(marks=None, items=("Battery", "Screen", "Charger", "Serial")):
    return {"items": list(items), "marks": marks or {}, "started": "2026-11-16 10:00"}


class ChecklistTests(unittest.TestCase):
    def test_toggle_cycles_through_fine_problem_and_clear(self):
        marks = {}
        seen = []
        for _ in range(4):
            marks = viewing_menu.toggled(marks, 2)
            seen.append(marks.get("2"))
        self.assertEqual(seen, ["ok", "bad", None, "ok"])
        self.assertEqual(viewing_menu.toggled({"0": "ok"}, 1), {"0": "ok", "1": "ok"})

    def test_screen_has_one_button_per_item_then_finish_and_cancel(self):
        msg = viewing_menu.checklist(7, "Phone <x>", viewing({"0": "ok", "1": "bad"}))
        self.assertTrue(msg.html)
        self.assertIn("Viewing #7 Phone &lt;x&gt;: tap an item", msg.text)
        self.assertEqual(
            msg.buttons,
            (
                ("✅ Battery", "vc:7:0"),
                ("❌ Screen", "vc:7:1"),
                ("☐ Charger", "vc:7:2"),
                ("☐ Serial", "vc:7:3"),
                ("✅ Finish viewing", "vf:7"),
                ("Cancel", "vx:7"),
            ),
        )

    def test_callback_data_stays_under_64_bytes_with_15_long_items(self):
        items = [f"a very long check about the {i} thing that goes on and on and on " * 3 for i in range(15)]
        msg = viewing_menu.checklist(123456, "t", viewing(items=items))
        self.assertEqual(len(msg.buttons), 17)
        for label, data in msg.buttons:
            self.assertLessEqual(len(data.encode()), 64)
            self.assertLessEqual(len(label), 47)
        self.assertTrue(msg.buttons[0][0].endswith("…"))

    def test_results_and_tally(self):
        v = viewing({"0": "ok", "2": "bad"})
        self.assertEqual(
            viewing_menu.results_text(v),
            "✅ fine: Battery\n☐ not checked: Screen\n❌ problem: Charger\n☐ not checked: Serial\n",
        )
        self.assertEqual(viewing_menu.counts(v), (1, 1, 2))
        self.assertEqual(
            viewing_menu.final_message(3, v).text, "🔍 Viewing #3 finished: 1 fine, 1 problem, 2 not checked."
        )
        self.assertEqual(viewing_menu.final_message(3, viewing()).buttons, ())
        self.assertIn("2 problems", viewing_menu.final_message(3, viewing({"0": "bad", "1": "bad"})).text)


class NoteTests(unittest.TestCase):
    def data(self, seen, marks):
        return {"viewing_seen": seen, "viewing": viewing(marks, [f"item {i}" for i in range(6)])}

    def test_no_line_without_earlier_analysis_or_new_findings(self):
        self.assertEqual(viewing_menu.viewing_note(None, self.data(2, {}), 7, 6), "")
        self.assertEqual(viewing_menu.viewing_note({"viewing_seen": 2}, self.data(2, {}), 7, 6), "")

    def test_line_lists_problems(self):
        note = viewing_menu.viewing_note({}, self.data(2, {"0": "bad", "1": "ok", "3": "bad"}), 7.4, 6.1)
        self.assertEqual(note, "🔍 After the viewing: score 7.4 → 6.1 · problems: item 0, item 3")

    def test_line_without_problems_and_with_many(self):
        self.assertEqual(
            viewing_menu.viewing_note({}, self.data(1, {"0": "ok"}), 7.4, 7.4),
            "🔍 After the viewing: score 7.4 (unchanged) · no problems found",
        )
        many = {str(i): "bad" for i in range(5)}
        self.assertTrue(
            viewing_menu.viewing_note({}, self.data(1, many), 7, 5).endswith("problems: item 0, item 1, item 2, …")
        )
