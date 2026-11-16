"""The Telegram HTML helpers."""

import unittest

from deal_scout import render


class PlainTextTests(unittest.TestCase):
    def test_tags_removed_and_entities_decoded(self):
        self.assertEqual(render.plain_text("<b>a</b> &amp; <i>b</i>"), "a & b")

    def test_pre_tags_removed(self):
        self.assertEqual(render.plain_text("<pre>a\nb</pre>"), "a\nb")


class ContactLineTests(unittest.TestCase):
    DATA = {"contact_preference": "whatsapp"}

    def test_default_wording_is_unchanged(self):
        self.assertEqual(
            render.contact_line(7, self.DATA, None),
            "💬 listing asks for whatsapp · tap below for the message; no phone on the screenshots "
            "(/phone 7 600123456 adds one)",
        )

    def test_stored_summary_does_not_claim_the_screenshots_had_no_phone(self):
        line = render.contact_line(7, self.DATA, None, stored=True)
        self.assertNotIn("screenshots", line)
        self.assertIn("/phone 7 600123456 adds the seller's number to the WhatsApp button", line)

    def test_known_phone_wins(self):
        self.assertTrue(render.contact_line(7, self.DATA, "48600123456", stored=True).endswith("to the seller"))


if __name__ == "__main__":
    unittest.main()
