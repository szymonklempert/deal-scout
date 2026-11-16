"""The morning digest: what it says and when it is due."""

import unittest
from datetime import datetime

from fakes import entry

from deal_scout import digest

NOW = datetime(2026, 11, 16, 9, 0)  # a Monday


class ScheduleTests(unittest.TestCase):
    def test_time_parsing(self):
        self.assertEqual(digest.parse_time("09:00"), (9, 0))
        self.assertEqual(digest.parse_time(" 7:05 "), (7, 5))
        for bad in ("", "off", "25:00", "9", "09:60", "noon"):
            self.assertIsNone(digest.parse_time(bad), bad)

    def test_off_is_empty_or_off(self):
        self.assertTrue(digest.is_off(""))
        self.assertTrue(digest.is_off(" OFF "))
        self.assertFalse(digest.is_off("25:00"))

    def test_due_once_a_day_from_the_time_on(self):
        self.assertFalse(digest.due(datetime(2026, 11, 16, 8, 59), (9, 0), None))
        self.assertTrue(digest.due(datetime(2026, 11, 16, 9, 0), (9, 0), None))
        self.assertTrue(digest.due(datetime(2026, 11, 16, 22, 0), (9, 0), "2026-11-15"))
        self.assertFalse(digest.due(datetime(2026, 11, 16, 22, 0), (9, 0), "2026-11-16"))


class SectionTests(unittest.TestCase):
    def test_waiting_needs_three_days_no_reply_and_an_open_listing(self):
        entries = [
            entry(1, title="Sprinter 313", contacted_at="2026-11-12 08:00"),  # 4 days
            entry(2, contacted_at="2026-11-14 08:00"),  # too recent
            entry(3, contacted_at="2026-11-01 08:00", replies_seen=1),  # answered
            entry(4, contacted_at="2026-11-01 08:00", status="sold"),
            entry(5, contacted_at="not a date"),
            entry(6),
        ]
        self.assertEqual(digest.waiting_lines(entries, NOW), ["#1 Sprinter 313 · contacted 4 days ago · /show_1"])

    def test_waiting_is_longest_first_and_capped(self):
        entries = [entry(n, contacted_at=f"2026-11-{n:02d} 08:00") for n in range(1, 9)]
        lines = digest.waiting_lines(entries, NOW)
        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[0].startswith("#1 "))

    def test_top_three_in_tops_format(self):
        entries = [entry(n, float(n)) for n in range(1, 6)]
        self.assertEqual(
            digest.top_lines(entries),
            ["1. Van 5 · 5.0/10 · /show_5", "2. Van 4 · 4.0/10 · /show_4", "3. Van 3 · 3.0/10 · /show_3"],
        )

    def test_new_counts_yesterday_and_today(self):
        entries = [
            entry(1, first_seen="2026-11-16"),
            entry(2, first_seen="2026-11-15"),
            entry(3, first_seen="2026-11-14"),
        ]
        self.assertEqual(digest.new_count(entries, NOW), 2)

    def test_market_moves_need_three_percent_and_enough_items(self):
        now = {"A · 2013": (94000, 6), "B": (100000, 5), "C": (90000, 4), "D": (80000, 3), "E": (97000, 5)}
        before = {"A · 2013": (97000, 5), "B": (99000, 5), "C": (100000, 4), "D": (100000, 5), "F": (1, 9)}
        self.assertEqual(digest.market_lines(now, before), ["C: 100k → 90k (−10%)", "A · 2013: 97k → 94k (−3%)"])

    def test_market_rise(self):
        self.assertEqual(digest.market_lines({"A": (103000, 5)}, {"A": (100000, 5)}), ["A: 100k → 103k (+3%)"])


class BuildTests(unittest.TestCase):
    def test_nothing_to_say_is_no_message(self):
        self.assertEqual(digest.build([], NOW, {}, {}), [])
        self.assertEqual(digest.build([entry(1, status="sold", first_seen="2026-01-01")], NOW, {}, {}), [])

    def test_sections_are_left_out_when_empty(self):
        (msg,) = digest.build([entry(1, 7.0, first_seen="2026-11-16")], NOW, {}, {})
        self.assertTrue(msg.html)
        self.assertEqual(
            msg.text,
            "☀️ Morning digest · Monday 2026-11-16\n\n⭐ Top open listings\n1. Van 1 · 7.0/10 · /show_1\n\n"
            "🆕 New since yesterday: 1",
        )
        self.assertNotIn("Waiting", msg.text)
        self.assertNotIn("Market", msg.text)
        self.assertEqual(msg.buttons, ())


if __name__ == "__main__":
    unittest.main()
