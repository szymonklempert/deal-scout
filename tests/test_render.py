"""The Telegram HTML helpers."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
import render  # noqa: E402


class PlainTextTests(unittest.TestCase):
    def test_tags_removed_and_entities_decoded(self):
        self.assertEqual(render.plain_text("<b>a</b> &amp; <i>b</i>"), "a & b")


if __name__ == "__main__":
    unittest.main()
