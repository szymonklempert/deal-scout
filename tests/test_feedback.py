"""Score corrections: applying overrides and the screens."""
import copy
import unittest

from fakes import CAMPERVAN, RESULT

from deal_scout import feedback, fixmenu
from deal_scout.scoring import score
from deal_scout.storage import csv_row

LONG = "Deal breaker: " + "a very long criterion text " * 10


def big() -> dict:
    data = copy.deepcopy(RESULT)
    data["specs"] = [{"name": f"Spec {i}", "value": "v", "score": 5} for i in range(18)]
    data["criteria_checks"] = [{"criterion": f"{LONG}{j}", "result": "met", "deal_breaker": False}
                               for j in range(12)]
    return data


class ApplyOverridesTests(unittest.TestCase):
    def test_does_not_mutate_and_replaces(self):
        data = copy.deepcopy(RESULT)
        out = feedback.apply_overrides(data, {"specs": {"Solar & batteries": 3}, "checks": {"Toilet": "met"}})
        self.assertEqual(data, RESULT)
        self.assertEqual(out["specs"][1]["score"], 3)
        self.assertEqual(out["criteria_checks"][2]["result"], "met")
        self.assertEqual(feedback.corrections_text(out), "Solar & batteries 8→3; Toilet unknown→met")

    def test_clamps_none_and_no_change(self):
        out = feedback.apply_overrides(RESULT, {"specs": {"Year": 99, "Solar & batteries": None, "Mileage": 4}})
        by_name = {s["name"]: s["score"] for s in out["specs"]}
        self.assertEqual((by_name["Year"], by_name["Solar & batteries"]), (10, None))
        self.assertEqual(feedback.corrections_text(out), "Year 6→10; Solar & batteries 8→–; Mileage –→4")
        same = feedback.apply_overrides(RESULT, {"specs": {"Year": 6}})
        self.assertEqual(feedback.corrections_text(same), "")

    def test_empty_overrides_give_no_text(self):
        self.assertEqual(feedback.corrections_text(feedback.apply_overrides(RESULT, {})), "")

    def test_score_follows_overridden_checks(self):
        out = feedback.apply_overrides(RESULT, {"checks": {"Solar": "met", "Toilet": "met"}})
        self.assertGreater(score(out["criteria_checks"]), score(RESULT["criteria_checks"]))
        self.assertEqual(csv_row(1, out, CAMPERVAN)["score"], 10.0)

    def test_carry_drops_unmatched_checks(self):
        old = {"specs": {"A": 1}, "checks": {"Toilet": "met", "Gone": "met"}}
        kept, dropped = feedback.carry_overrides(old, RESULT)
        self.assertEqual((kept, dropped), ({"specs": {"A": 1}, "checks": {"Toilet": "met"}}, 1))

    def test_scrub_numbers(self):
        self.assertEqual(feedback.scrub_numbers("call +48 600-123-456 now, year 2014"),
                         "call [number removed] now, year 2014")


class ScreenTests(unittest.TestCase):
    def test_callback_data_is_short_and_uses_indexes(self):
        data = feedback.apply_overrides(big(), {"specs": {"Spec 3": 1}})
        specs = [f"Spec {i}" for i in range(18)]
        screens = [fixmenu.menu(123, data, specs), fixmenu.checks_screen(123, data),
                   fixmenu.check_screen(123, data, 11), fixmenu.confirm(123, "x", 5, 3, "7.4", "7.1")]
        screens += [fixmenu.spec_screen(123, data, specs, i) for i in (0, 17)]
        for screen in screens:
            for label, value in screen.buttons:
                self.assertLess(len(value.encode()), 64, value)
                self.assertNotIn("Spec", value)
                self.assertLessEqual(len(label), 60)

    def test_menu_marks_changes_and_layout(self):
        data = feedback.apply_overrides(RESULT, {"specs": {"Solar & batteries": 3}})
        menu = fixmenu.menu(1, data, CAMPERVAN.specs)
        labels = [b[0] for b in menu.buttons]
        self.assertIn("✏️ Solar & batteries · 3", labels)
        self.assertIn("Mileage · –", labels)
        self.assertEqual(labels[-2:], ["Criteria checks ▸", "✅ Done"])
        self.assertEqual(menu.columns, 2)
        self.assertIn("Feedback on #1", menu.text)
        self.assertNotIn("Criteria checks ▸", [b[0] for b in fixmenu.menu(1, dict(RESULT, criteria_checks=[]),
                                                                        CAMPERVAN.specs).buttons])

    def test_spec_and_check_screens_show_model_and_now(self):
        data = feedback.apply_overrides(RESULT, {"specs": {"Solar & batteries": 3}, "checks": {"Toilet": "met"}})
        i = CAMPERVAN.specs.index("Solar & batteries")
        text = fixmenu.spec_screen(1, data, CAMPERVAN.specs, i).text
        self.assertIn("Model: 8/10; now 3/10", text)
        self.assertEqual(fixmenu.check_screen(1, data, 2).text, "Toilet\nModel: unknown; now met. Set:")


if __name__ == "__main__":
    unittest.main()
