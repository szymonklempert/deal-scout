"""The Telegram HTML helpers."""
import unittest

from deal_scout import render


class PlainTextTests(unittest.TestCase):
    def test_tags_removed_and_entities_decoded(self):
        self.assertEqual(render.plain_text("<b>a</b> &amp; <i>b</i>"), "a & b")


if __name__ == "__main__":
    unittest.main()
