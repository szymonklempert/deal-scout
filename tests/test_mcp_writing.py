"""The MCP server's write side: submit_review and record_seller_reply on an empty temporary data home."""

import json
import tempfile
import unittest
from pathlib import Path

from mcp import Client
from test_golden import FIXTURES, result
from test_mcp_reading import PHONE_DIGITS

from deal_scout import config, domain, scoring, storage, viewing
from deal_scout.mcp_server import create_server


def review(**over):
    """A valid review of the gadget domain, with a card (the card names the phone: it must be scrubbed)."""
    return dict(result(**over), card_markdown="# #001 Widget\n- Seller: Alex Example, tel. +48 600 111 222\n")


def squeezed(text):
    return "".join(ch for ch in text if ch.isdigit())


class SubmitTests(unittest.IsolatedAsyncioTestCase):
    """submit_review and record_seller_reply on an empty temp home."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.settings = config.Settings()
        self.domain = domain.load("gadget", roots=[FIXTURES])
        self.pushed = []
        self.server = create_server(self.home, self.settings, self.domain, push=self.push)

    def push(self, row, fields):
        self.pushed.append(row["number"])

    def serve(self, settings=None, push=None):
        self.server = create_server(self.home, settings or self.settings, self.domain, push=push or self.push)

    async def call(self, name, **arguments):
        async with Client(self.server) as client:
            return await client.call_tool(name, arguments)

    def files(self):
        return [f for f in self.home.rglob("*") if f.is_file() and f.name != ".lock"]

    def assert_no_phone_anywhere(self):
        for f in self.files():
            self.assertNotIn(PHONE_DIGITS, squeezed(f.read_bytes().decode("utf-8", "ignore")), f.name)

    async def test_a_new_listing_is_stored_scored_and_carries_no_phone(self):
        res = await self.call(
            "submit_review",
            review=review(),
            notes="Seen in a group, call 600 111 222",
            links=["https://example.com/item/1"],
        )
        self.assertFalse(res.is_error, res.content)
        out = res.structured_content
        self.assertEqual(out["number"], 1)
        self.assertEqual(out["score"], scoring.score(result()["criteria_checks"]))
        self.assertEqual(out["verdict"], "ask first")
        self.assertEqual(out["missing"], ["battery health", "serial number"])
        self.assertEqual(out["deal_breakers_hit"], [])
        self.assertTrue(out["whatsapp_url"].startswith("https://wa.me/48600111222?text=Hello"))
        self.assertFalse(out["sheet_updated"])
        self.assertEqual(out["card_resource"], "deal-scout://listing/1")
        self.assertIn("#1 Acme Widget 3 128 GB", out["summary_text"])
        self.assertNotIn("<b>", out["summary_text"])
        folder = self.home / "inbox" / "001"
        notes = (folder / "notes.txt").read_text(encoding="utf-8")
        self.assertIn("Seen in a group, call [number removed]", notes)
        self.assertEqual((folder / "links.txt").read_text(encoding="utf-8"), "https://example.com/item/1\n")
        listings = self.home / "listings"
        self.assertEqual(list(storage.read_rows(listings / "listings.csv")), [1])
        self.assertEqual(storage.read_rows(listings / "listings.csv")[1]["link"], "https://example.com/item/1")
        self.assertIn("[phone removed]", (listings / "001.md").read_text(encoding="utf-8"))
        self.assertNotIn("seller_phone", json.loads((listings / "001.json").read_text(encoding="utf-8")))
        self.assertIn("| 1 | Acme Widget 3", (listings / "INDEX.md").read_text(encoding="utf-8"))
        self.assertEqual(self.pushed, [])
        self.assert_no_phone_anywhere()
        again = await self.call("submit_review", review=review(title="Second"))
        self.assertEqual(again.structured_content["number"], 2)
        self.assertEqual(sorted(p.name for p in (self.home / "inbox").iterdir()), ["001", "002"])

    async def test_without_a_card_there_is_no_card_resource(self):
        data = result()
        res = await self.call("submit_review", review=data)
        self.assertIsNone(res.structured_content["card_resource"])
        self.assertIsNone(
            (await self.call("submit_review", review=dict(data, seller_phone=None))).structured_content["whatsapp_url"]
        )

    async def test_the_sheet_is_updated_when_it_is_set_up(self):
        self.serve(config.Settings(sheet_url="https://example.com/sheet"))
        res = await self.call("submit_review", review=review())
        self.assertTrue(res.structured_content["sheet_updated"])
        self.assertEqual(self.pushed, [1])

        def failing(row, fields):
            raise RuntimeError("down")

        self.serve(config.Settings(sheet_url="https://example.com/sheet"), push=failing)
        res = await self.call("submit_review", review=review(), number=1)
        self.assertFalse(res.is_error)
        self.assertFalse(res.structured_content["sheet_updated"])
        self.assertIn("Google Sheet not updated", res.structured_content["summary_text"])

    async def test_an_invalid_review_lists_what_to_fix_and_writes_nothing(self):
        res = await self.call("submit_review", review=review(verdict="maybe", title=5))
        self.assertTrue(res.is_error)
        text = res.content[0].text
        self.assertIn("$.verdict", text)
        self.assertIn("$.title: expected string", text)
        self.assertIn("call submit_review again", text)
        self.assertEqual(self.files(), [])
        res = await self.call("submit_review", review={})
        self.assertTrue(res.is_error)
        self.assertLessEqual(sum(1 for line in res.content[0].text.splitlines() if line.startswith("- ")), 20)
        self.assertFalse((self.home / "inbox").exists())

    async def test_caps(self):
        cases = [
            ({"notes": "x" * 10_001}, "notes"),
            ({"links": [f"https://example.com/{i}" for i in range(21)]}, "links has 21"),
            ({"links": ["http://example.com/a"]}, "https://"),
            ({"links": ["https://example.com/" + "a" * 500]}, "500 characters"),
            ({"links": ["https://example.com/a b"]}, "without spaces"),
        ]
        for extra, expected in cases:
            res = await self.call("submit_review", review=review(), **extra)
            self.assertTrue(res.is_error, extra)
            self.assertIn(expected, res.content[0].text)
        card = dict(review(), card_markdown="x" * 50_001)
        res = await self.call("submit_review", review=card)
        self.assertIn("card_markdown", res.content[0].text)
        self.assertEqual(self.files(), [])
        ok = await self.call("submit_review", review=review(), notes="x" * 10_000)
        self.assertFalse(ok.is_error)

    async def test_a_review_again_keeps_the_owners_corrections(self):
        await self.call("submit_review", review=review())
        path = self.home / "listings" / "001.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["overrides"] = {"specs": {"Condition": 2}}
        data["contacted_at"] = "2026-01-01 10:00"
        path.write_text(json.dumps(data), encoding="utf-8")
        res = await self.call("submit_review", review=review(summary="Updated."), number=1, notes="Seller replied")
        self.assertEqual(res.structured_content["number"], 1)
        shown = (await self.call("show_listing", number=1)).structured_content
        self.assertEqual(shown["specs"][1]["score"], 2)
        self.assertEqual(shown["summary"], "Updated.")
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["overrides"], {"specs": {"Condition": 2}})
        self.assertEqual(stored["contacted_at"], "2026-01-01 10:00")
        self.assertEqual(list(storage.read_rows(self.home / "listings" / "listings.csv")), [1])
        missing = await self.call("submit_review", review=review(), number=5)
        self.assertIn("#5 does not exist", missing.content[0].text)

    async def test_a_seller_reply_is_saved_without_numbers(self):
        await self.call("submit_review", review=review())
        res = await self.call("record_seller_reply", number=1, reply="Battery health is 91%.\nCall 600 111 222")
        self.assertFalse(res.is_error, res.content)
        out = res.structured_content
        self.assertEqual(out["review"]["number"], 1)
        self.assertEqual(
            out["instruction"],
            "Re-review with these replies (they override the listing) and call submit_review(number=1, ...).",
        )
        replies = (self.home / "inbox" / "001" / "replies.txt").read_text(encoding="utf-8")
        self.assertIn("Battery health is 91%.", replies)
        self.assertIn("[number removed]", replies)
        self.assert_no_phone_anywhere()
        for args, expected in (
            ({"number": 9, "reply": "hi"}, "has not been analysed"),
            ({"number": 1, "reply": "  "}, "1 to 10000"),
            ({"number": 1, "reply": "x" * 10_001}, "1 to 10000"),
            ({"number": 0, "reply": "hi"}, "from 1 to 9999"),
        ):
            res = await self.call("record_seller_reply", **args)
            self.assertTrue(res.is_error, args)
            self.assertIn(expected, res.content[0].text)

    async def test_viewing_checklist_before_and_after_a_viewing_starts(self):
        await self.call("submit_review", review=review(viewing_checks=["Test the battery", "Check the screen"]))
        out = (await self.call("viewing_checklist", number=1)).structured_content
        self.assertEqual(
            out,
            {
                "number": 1,
                "title": "Acme Widget 3 128 GB",
                "frozen": False,
                "started": "",
                "items": [{"item": "Test the battery", "mark": ""}, {"item": "Check the screen", "mark": ""}],
            },
        )
        viewing.handle(self.home, 1, "start", [], clock=lambda: 0.0)
        viewing.handle(self.home, 1, "toggle", ["1"])
        out = (await self.call("viewing_checklist", number=1)).structured_content
        self.assertTrue(out["frozen"])
        self.assertEqual([i["mark"] for i in out["items"]], ["", "ok"])
        res = await self.call("viewing_checklist", number=3)
        self.assertTrue(res.is_error)

    async def test_the_fallback_checklist_comes_from_the_open_questions(self):
        await self.call("submit_review", review=review())
        out = (await self.call("viewing_checklist", number=1)).structured_content
        self.assertEqual(out["items"][0], {"item": "Verify: battery health", "mark": ""})


if __name__ == "__main__":
    unittest.main()
