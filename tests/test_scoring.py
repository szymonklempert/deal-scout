"""The score from criteria checks and the spec scores."""
import unittest

from fakes import CAMPERVAN, RESULT, check

from deal_scout import scoring


class ScoreTests(unittest.TestCase):
    def test_no_criteria_gives_no_score(self):
        self.assertIsNone(scoring.score([]))

    def test_met_unknown_missed(self):
        self.assertEqual(scoring.score(RESULT["criteria_checks"]), 6.2)  # (1 + 1 + 0.5 + 0) / 4

    def test_missed_deal_breaker_is_zero(self):
        self.assertEqual(scoring.score([check("Rust", "missed", True), check("Bed", "met")]), 0.0)

    def test_unknown_deal_breaker_is_not_zero(self):
        self.assertEqual(scoring.score([check("Rust", "unknown", True), check("Bed", "met")]), 7.5)

    def test_nice_to_haves_count_half(self):
        checks = [check("Solar", "met"), dict(check("Indoor shower", "missed"), importance="nice to have"),
                  dict(check("Swivel seats", "missed"), importance="big plus")]
        self.assertEqual(scoring.score(checks), 4.0)  # 1 of 2.5

    def test_deal_breaker_label_is_stripped(self):
        self.assertEqual(scoring.clean("Deal breaker: rust"), "rust")
        self.assertEqual(scoring.clean("Budget: 150 000"), "Budget: 150 000")


class OrderedSpecsTests(unittest.TestCase):
    def test_scores_are_clamped(self):
        data = {"specs": [{"name": "Year", "value": "2014", "score": 14}, {"name": "Mileage", "value": "x",
                                                                          "score": -2}]}
        got = {n: s for n, _, s in scoring.ordered_specs(data, CAMPERVAN.specs)}
        self.assertEqual((got["Year"], got["Mileage"]), (10, 0))


if __name__ == "__main__":
    unittest.main()
