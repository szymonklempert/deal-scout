"""The /top ranking and the /compare table."""

import unittest

from deal_scout import reports
from deal_scout.reports import Entry


def entry(n, score=None, **kw):
    base = {
        "title": f"Van {n}",
        "verdict": "ask first",
        "status": "new",
        "score": score,
        "specs_avg": None,
        "price": None,
        "year": None,
        "km": None,
    }
    base.update(kw)
    return Entry(n=n, **base)


class RankingTests(unittest.TestCase):
    def test_best_score_first_then_specs_then_newest_unscored_last(self):
        entries = [
            entry(1, 7.0, specs_avg=5.0),
            entry(2, 7.0, specs_avg=6.0),
            entry(3),
            entry(4, 7.0, specs_avg=6.0),
            entry(5, 9.0),
        ]
        self.assertEqual([e.n for e in reports.rank_open(entries)], [5, 4, 2, 1, 3])

    def test_closed_listings_are_left_out(self):
        entries = [entry(1, 9.0, status="Sold"), entry(2, 5.0), entry(3, 8.0, status="rejected")]
        self.assertEqual([e.n for e in reports.rank_open(entries)], [2])

    def test_line_has_title_score_and_a_tappable_command(self):
        line = reports.ranking_line(1, entry(12, 7.4, title="Mercedes <Sprinter> 313"))
        self.assertEqual(line, "1. Mercedes &lt;Sprinter&gt; 313 · 7.4/10 · /show_12")

    def test_long_title_is_shortened_and_no_score_is_a_dash(self):
        line = reports.ranking_line(2, entry(3, title="x" * 60))
        self.assertEqual(line, f"2. {'x' * 39}… · – · /show_3")

    def test_untitled_listing_is_named_by_its_number(self):
        self.assertEqual(reports.ranking_line(1, entry(4, 5.0, title="")), "1. #4 · 5.0/10 · /show_4")

    def test_top_message_respects_the_limit_and_has_no_buttons(self):
        msg = reports.top_message([entry(n, n) for n in range(1, 9)], limit=3)
        self.assertEqual(len(msg.text.splitlines()), 3)
        self.assertTrue(msg.html)
        self.assertEqual(msg.buttons, ())

    def test_no_open_listings(self):
        self.assertEqual(reports.top_message([entry(1, 5.0, status="sold")]).text, "No open listings yet.")

    def test_limit_parsing(self):
        self.assertEqual(reports.parse_limit([]), 5)
        self.assertEqual(reports.parse_limit(["10"]), 10)
        self.assertEqual(reports.parse_limit(["99"]), 10)
        self.assertEqual(reports.parse_limit(["0"]), 1)
        self.assertEqual(reports.parse_limit(["many"]), 5)


class CommandTests(unittest.TestCase):
    def test_show_link_becomes_show_with_a_number(self):
        self.assertEqual(reports.split_show_link("/show_12", []), ("/show", ["12"]))
        self.assertEqual(reports.split_show_link("/show", ["7"]), ("/show", ["7"]))
        self.assertEqual(reports.split_show_link("/show_x", []), ("/show_x", []))

    def test_numbers(self):
        self.assertEqual(reports.parse_numbers(["#7", "12,", "15", "7", "abc"]), [7, 12, 15])

    def test_count_problems(self):
        self.assertIn("Usage", reports.compare_problem([7]))
        self.assertIn("at most 4", reports.compare_problem([1, 2, 3, 4, 5]))
        self.assertEqual(reports.compare_problem([1, 2]), "")


class CompareTests(unittest.TestCase):
    def test_number_formats(self):
        self.assertEqual(reports.thousands(84000), "84k")
        self.assertEqual(reports.thousands(211600), "212k")
        self.assertEqual(reports.thousands(1350), "1.4k")
        self.assertEqual(reports.thousands(5000), "5k")
        self.assertEqual(reports.thousands(850), "850")
        self.assertEqual(reports.thousands(None), "–")
        self.assertEqual(reports.number_text(6.0), "6")
        self.assertEqual(reports.number_text(None), "–")

    def test_table_stays_narrow_for_four_listings(self):
        entries = [
            entry(
                n,
                7.4,
                specs_avg=6.5,
                price=84000,
                year=2015,
                km=212000,
                specs=(("A very long spec name indeed", 8), ("Solar", None)),
            )
            for n in (101, 102, 103, 104)
        ]
        table = reports.compare_table(entries, ["A very long spec name indeed", "Solar"])
        lines = table.splitlines()
        self.assertEqual(lines[0], " " * 14 + "  #101  #102  #103  #104")
        self.assertEqual(lines[3], "Price".ljust(14) + "   84k" * 4)
        self.assertEqual(lines[6], "A very long sp     8     8     8     8")
        self.assertEqual(lines[7], "Solar              –     –     –     –")
        self.assertTrue(all(len(line) <= 40 for line in lines))

    def test_message_names_the_best_and_links_every_listing(self):
        entries = [entry(7, 5.0), entry(12, 7.4), entry(15)]
        msg = reports.compare_message(entries, [])
        self.assertTrue(msg.text.startswith("<pre>"))
        self.assertIn("Best overall: #12 (7.4/10)", msg.text)
        self.assertTrue(msg.text.endswith("Open: /show_7 · /show_12 · /show_15"))

    def test_no_best_line_when_nothing_is_scored(self):
        self.assertNotIn("Best overall", reports.compare_message([entry(1), entry(2)], []).text)

    def test_spec_names_are_escaped(self):
        text = reports.compare_message([entry(1, specs=(("Solar & co", 3),))], ["Solar & co"]).text
        self.assertIn("Solar &amp; co", text)


if __name__ == "__main__":
    unittest.main()
