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
