"""The line that says what the seller's reply changed."""
import unittest

from deal_scout import replies


class AnsweredTests(unittest.TestCase):
    def test_answered_are_the_old_items_no_longer_missing(self):
        self.assertEqual(replies.answered_items(["VIN", "DMC", "Toilet type"], ["toilet  TYPE", "Rust"]),
                         ["VIN", "DMC"])
        self.assertEqual(replies.answered_items([], ["VIN"]), [])


class ReplyNoteTests(unittest.TestCase):
    def note(self, seen=1, before=0, old="6.2", new="7.4", old_missing=("VIN", "DMC"), new_missing=("DMC",)):
        previous = {"missing": list(old_missing), "replies_seen": before}
        return replies.reply_note(previous, {"missing": list(new_missing), "replies_seen": seen}, old, new)

    def test_score_change_and_answered(self):
        self.assertEqual(self.note(), "📨 After the seller's reply: score 6.2 → 7.4 · answered: VIN")

    def test_unchanged_without_answers(self):
        self.assertEqual(self.note(old="6.2", new="6.2", new_missing=("VIN", "DMC")),
                         "📨 After the seller's reply: score 6.2 (unchanged)")

    def test_no_score(self):
        self.assertEqual(self.note(old="", new="", new_missing=("VIN", "DMC")), "📨 After the seller's reply: score –")
        self.assertIn("score – → 5.0", self.note(old="", new="5.0"))

    def test_no_line_without_new_replies_or_an_earlier_analysis(self):
        self.assertEqual(self.note(seen=2, before=2), "")
        self.assertEqual(self.note(seen=0, before=0), "")
        self.assertEqual(replies.reply_note(None, {"replies_seen": 1}, "", "5.0"), "")

    def test_items_are_escaped(self):
        previous = {"missing": ["VIN <x>"], "replies_seen": 0}
        self.assertIn("answered: VIN &lt;x&gt;", replies.reply_note(previous, {"replies_seen": 1}, "1", "2"))


if __name__ == "__main__":
    unittest.main()
