import tempfile
import unittest
from pathlib import Path

from deal_scout.inbox import Inbox


class InboxTest(unittest.TestCase):
    def test_photos_are_numbered_and_links_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Inbox(Path(tmp))
            self.assertEqual(inbox.add_photo(1, b"a").name, "photo_01.jpg")
            self.assertEqual(inbox.add_photo(1, b"b").name, "photo_02.jpg")
            self.assertEqual(inbox.add_links(1, ["https://a.example/1"]), ["https://a.example/1"])
            self.assertEqual(
                inbox.add_links(1, ["https://a.example/1", "https://a.example/2"]), ["https://a.example/2"]
            )
            self.assertEqual(inbox.summary(1), (2, ["https://a.example/1", "https://a.example/2"]))

    def test_replies_are_kept_apart_from_photos_notes_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Inbox(Path(tmp))
            inbox.add_photo(1, b"p")
            self.assertEqual(inbox.add_reply_photo(1, b"a").name, "reply_01.jpg")
            self.assertEqual(inbox.add_reply_photo(1, b"b").name, "reply_02.jpg")
            inbox.add_reply(1, "VIN is ABC https://a.example/vin", 1000.0)
            inbox.add_reply(1, "DMC 3500\nsecond line", 1000.0)
            folder = Path(tmp) / "inbox" / "001"
            lines = (folder / "replies.txt").read_text(encoding="utf-8").splitlines()
            self.assertRegex(lines[0], r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] VIN is ABC https://a.example/vin$")
            self.assertEqual(inbox.reply_summary(1), (2, 2))  # a text with two lines is one text
            self.assertEqual(inbox.summary(1), (1, []))  # photo_* untouched, nothing added to links.txt
            self.assertFalse((folder / "notes.txt").exists())
            self.assertEqual(inbox.reply_summary(2), (0, 0))


if __name__ == "__main__":
    unittest.main()
