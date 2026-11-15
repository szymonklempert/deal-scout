"""The prompt and the JSON schema."""
import tempfile
import unittest
from pathlib import Path

from fakes import CAMPERVAN

from deal_scout import prompts


class SchemaTests(unittest.TestCase):
    def test_strict_mode_rules_hold_everywhere(self):
        """OpenAI strict mode and Anthropic structured outputs: every object closed with all keys required, enums
        typed, no numeric limits."""
        def walk(node, path="$"):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False, path)
                    self.assertEqual(sorted(node["required"]), sorted(node["properties"]), path)
                if "enum" in node:
                    self.assertEqual(node.get("type"), "string", path)
                for bad in ("minimum", "maximum", "multipleOf"):
                    self.assertNotIn(bad, node, path)
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
        walk(prompts.schema(CAMPERVAN, card=True))


class ApiRequestTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.folder = Path(self.dir.name) / "inbox" / "001"
        self.folder.mkdir(parents=True)
        (self.folder / "photo_01.jpg").write_bytes(b"p1")
        (self.folder / "photo_02.jpg").write_bytes(b"p2")

    def request(self):
        return prompts.api_request(self.dir.name, "001", CAMPERVAN)

    def test_without_replies_nothing_changes(self):
        _, user, images = self.request()
        self.assertTrue(user.endswith("2 photo(s) of the listing are attached, in order."))
        self.assertNotIn("seller's replies", user)
        self.assertEqual([p.name for p in images], ["photo_01.jpg", "photo_02.jpg"])

    def test_replies_text_and_images_follow_the_listing(self):
        (self.folder / "links.txt").write_text("https://x.pl/1\n", encoding="utf-8")
        (self.folder / "replies.txt").write_text("[2026-01-01 10:00] VIN is ABC\n", encoding="utf-8")
        (self.folder / "reply_01.jpg").write_bytes(b"r1")
        _, user, images = self.request()
        self.assertLess(user.index("Links:"), user.index("The seller's replies:\n[2026-01-01 10:00] VIN is ABC"))
        self.assertTrue(user.endswith("2 photo(s) of the listing, then 1 of the seller's replies, are attached, "
                                      "in order."))
        self.assertEqual([p.name for p in images], ["photo_01.jpg", "photo_02.jpg", "reply_01.jpg"])


if __name__ == "__main__":
    unittest.main()
