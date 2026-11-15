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
            self.assertEqual(inbox.add_links(1, ["https://a.example/1", "https://a.example/2"]), ["https://a.example/2"])
            self.assertEqual(inbox.summary(1), (2, ["https://a.example/1", "https://a.example/2"]))


if __name__ == "__main__":
    unittest.main()
