"""The MCP server: tools, resources and prompts, over an in-memory client on a temporary data home."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import anyio
from fakes import FakeRunner
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from test_golden import FIXTURES, GOLDEN, result

from deal_scout import config, domain, process, prompts, scoring, storage, viewing
from deal_scout.mcp_server import create_server

PHONE_DIGITS = "600111222"
READ_ONLY_TOOLS = {
    "list_listings",
    "top",
    "compare",
    "show_listing",
    "viewing_checklist",
    "market_prices",
    "review_format",
}
WRITE_TOOLS = {"submit_review", "record_seller_reply"}


class ServerCase(unittest.IsolatedAsyncioTestCase):
    """A server over a temp home with three analysed listings (the gadget fixture domain, a fake model)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.settings = config.Settings()
        self.domain = domain.load("gadget", roots=[FIXTURES])
        for n, price in ((1, 1450), (2, 1100), (3, 1350)):
            self.seed(n, price)
        self.server = create_server(self.home, self.settings, self.domain)

    def seed(self, n, price):
        folder = self.home / "inbox" / f"{n:03d}"
        folder.mkdir(parents=True)
        (folder / "photo_01.jpg").write_bytes(b"\xff\xd8x")
        over = {"price_pln": price, "model": f"Acme Widget {n}"}
        if n == 2:
            over["criteria_checks"] = [dict(result()["criteria_checks"][0], result="missed")]
        process.process_listing(
            self.home, n, settings=self.settings, domain=self.domain, runner=FakeRunner(result(**over))
        )

    async def call(self, name, **arguments):
        async with Client(self.server) as client:
            return await client.call_tool(name, arguments)

    async def read(self, uri):
        async with Client(self.server) as client:
            return (await client.read_resource(uri)).contents[0].text


class ToolTests(ServerCase):
    async def test_tools_are_listed_read_only(self):
        async with Client(self.server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
        self.assertEqual(set(tools), READ_ONLY_TOOLS | WRITE_TOOLS)
        for name, tool in tools.items():
            hints = tool.annotations
            self.assertIsNotNone(tool.output_schema, name)
            self.assertFalse(hints.destructive_hint, name)
            if name in READ_ONLY_TOOLS:
                self.assertTrue(hints.read_only_hint, name)
                self.assertTrue(hints.idempotent_hint, name)
            else:
                self.assertFalse(hints.read_only_hint, name)
                self.assertFalse(hints.idempotent_hint, name)

    async def test_list_listings_newest_first_and_open_only(self):
        out = (await self.call("list_listings")).structured_content
        self.assertEqual([x["number"] for x in out["listings"]], [3, 2, 1])
        self.assertEqual(out["total"], 3)
        first = out["listings"][0]
        self.assertEqual(
            sorted(first),
            ["first_seen", "number", "price_pln", "score", "specs_avg", "status", "title", "verdict"],
        )
        path = self.home / "listings" / "listings.csv"
        rows = storage.read_rows(path)
        rows[2]["status"] = "sold"
        storage.write_rows(path, rows, storage.csv_fields(self.domain))
        out = (await self.call("list_listings", limit=5)).structured_content
        self.assertEqual([x["number"] for x in out["listings"]], [3, 1])
        out = (await self.call("list_listings", limit=1, open_only=False)).structured_content
        self.assertEqual((out["total"], [x["number"] for x in out["listings"]]), (3, [3]))

    async def test_top_is_the_bots_ranking(self):
        out = (await self.call("top", n=2)).structured_content
        numbers = [x["number"] for x in out["listings"]]
        self.assertEqual(len(numbers), 2)
        self.assertNotIn(2, numbers)  # #2 hits a deal breaker, so its score is 0
        scores = [x["score"] for x in out["listings"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    async def test_compare(self):
        out = (await self.call("compare", numbers=[1, 2, 3])).structured_content
        self.assertEqual([x["number"] for x in out["listings"]], [1, 2, 3])
        self.assertIn(out["best"], (1, 3))
        specs = out["listings"][0]["specs"]
        self.assertEqual([s["name"] for s in specs], self.domain.specs)
        self.assertEqual(specs[1], {"name": "Condition", "value": "light scratches <back>", "score": 7})

    async def test_show_listing_applies_overrides_and_holds_no_phone(self):
        path = self.home / "listings" / "001.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["overrides"] = {"specs": {"Condition": 2}, "checks": {"Original box": "met"}}
        path.write_text(json.dumps(data), encoding="utf-8")
        res = await self.call("show_listing", number=1)
        out = res.structured_content
        self.assertEqual(out["specs"][1]["score"], 2)
        self.assertEqual({c["criterion"]: c["result"] for c in out["checks"]}["Original box"], "met")
        self.assertEqual(out["verdict"], "ask first")
        self.assertEqual(out["missing"], ["battery health", "serial number"])
        self.assertIn("Condition", out["corrections"])
        self.assertIsNotNone(out["score"])
        self.assertNotIn(PHONE_DIGITS, json.dumps(out).replace(" ", ""))
        for found in self.home.rglob("*"):
            if found.is_file() and found.name != ".lock":
                self.assertNotIn(PHONE_DIGITS, found.read_bytes().decode("utf-8", "ignore").replace(" ", ""), found)

    async def test_market_prices(self):
        for n in range(4, 11):
            self.seed(n, 1200 + 10 * n)
        out = (await self.call("market_prices")).structured_content
        self.assertIn("Market", out["text"])
        self.assertEqual(out["groups"][0]["items"], 10)
        self.assertEqual(out["groups"][0]["group"], "phone")

    async def test_review_format(self):
        out = (await self.call("review_format")).structured_content
        self.assertEqual(out["specs"], self.domain.specs)
        self.assertEqual(out["categories"]["kind"], ["phone", "tablet", "unknown"])
        self.assertIn("card_markdown", out["json_schema"]["properties"])
        self.assertEqual(out["optional_keys"], ["card_markdown", "viewing_checks"])

    async def test_bad_arguments_come_back_as_errors_the_model_can_act_on(self):
        cases = [
            ("list_listings", {"limit": 0}, "limit must be a whole number from 1 to 100"),
            ("list_listings", {"limit": 101}, "limit"),
            ("top", {"n": 11}, "n must be a whole number from 1 to 10"),
            ("compare", {"numbers": [1]}, "2 to 4"),
            ("compare", {"numbers": [1, 2, 3, 4, 5]}, "2 to 4"),
            ("compare", {"numbers": [1, 1]}, "2 to 4 different"),
            ("compare", {"numbers": [1, 10000]}, "1 to 9999"),
            ("compare", {"numbers": [1, 77]}, "#77"),
            ("show_listing", {"number": 0}, "number must be a whole number from 1 to 9999"),
            ("show_listing", {"number": 42}, "has not been analysed"),
            ("show_listing", {"number": "x"}, "number"),
        ]
        for name, args, expected in cases:
            res = await self.call(name, **args)
            self.assertTrue(res.is_error, (name, args))
            self.assertIn(expected, res.content[0].text, (name, args))


class ResourceTests(ServerCase):
    async def test_criteria_and_domain_files(self):
        criteria = (FIXTURES / "domains" / "gadget" / "criteria.md").read_text(encoding="utf-8")
        self.assertEqual(await self.read("deal-scout://criteria"), criteria)
        for name in ("instructions.md", "checklist.md", "card-template.md", "guide.md"):
            text = (FIXTURES / "domains" / "gadget" / name).read_text(encoding="utf-8")
            self.assertEqual(await self.read(f"deal-scout://domain/{name}"), text, name)

    async def test_missing_criteria_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            for f in (FIXTURES / "domains" / "gadget").iterdir():
                if f.name != "criteria.md":
                    (Path(d) / f.name).write_bytes(f.read_bytes())
            bare = domain.Domain.from_dir(d)
            server = create_server(self.home, self.settings, bare)
            async with Client(server) as client:
                text = (await client.read_resource("deal-scout://criteria")).contents[0].text
        self.assertIn("deal-scout init", text)

    async def test_listing_card_and_market(self):
        (self.home / "listings" / "001.md").write_text("# #001 Widget\n", encoding="utf-8")
        self.assertEqual(await self.read("deal-scout://listing/1"), "# #001 Widget\n")
        self.assertEqual(await self.read("deal-scout://listing/001"), "# #001 Widget\n")
        self.assertIn("Market", await self.read("deal-scout://market"))

    async def test_resource_templates_are_listed(self):
        async with Client(self.server) as client:
            templates = {t.uri_template for t in (await client.list_resource_templates()).resource_templates}
            resources = {str(r.uri) for r in (await client.list_resources()).resources}
        self.assertEqual(templates, {"deal-scout://domain/{name}", "deal-scout://listing/{number}"})
        self.assertEqual(resources, {"deal-scout://criteria", "deal-scout://market"})

    async def test_the_allowlist_rejects_everything_else(self):
        bad = [
            "deal-scout://domain/..%2F.env",
            "deal-scout://domain/../.env",
            "deal-scout://domain/..%2F..%2Fetc%2Fpasswd",
            "deal-scout://domain/%2Fetc%2Fpasswd",
            "deal-scout://domain/criteria.md",  # served only by its own resource
            "deal-scout://domain/domain.json",
            "deal-scout://domain/nope.md",
            "deal-scout://listing/abc",
            "deal-scout://listing/0",
            "deal-scout://listing/-1",
            "deal-scout://listing/99999",
            "deal-scout://listing/42",  # no such card
            "deal-scout://listing/..%2F..%2F.env",
        ]
        async with Client(self.server) as client:
            for uri in bad:
                with self.assertRaises(MCPError, msg=uri):
                    await client.read_resource(uri)


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


class PromptTests(ServerCase):
    async def prompt(self, name, **arguments):
        async with Client(self.server) as client:
            return (await client.get_prompt(name, arguments)).messages[0].content.text

    async def test_the_prompts_are_listed(self):
        async with Client(self.server) as client:
            names = {p.name for p in (await client.list_prompts()).prompts}
        self.assertEqual(names, {"review_listing", "draft_seller_message", "prepare_viewing"})

    async def test_the_review_prompt_is_frozen_and_shares_the_rules(self):
        text = await self.prompt("review_listing")
        path = GOLDEN / "mcp_review_prompt.txt"
        if os.environ.get("GOLDEN_UPDATE"):
            path.write_text(text + "\n", encoding="utf-8")
        self.assertEqual(text + "\n", path.read_text(encoding="utf-8"))
        self.assertIn(prompts.rules(self.domain), text)
        self.assertIn("submit_review", text)
        noted = await self.prompt("review_listing", notes="Seen at the market")
        self.assertIn("Seen at the market", noted)

    async def test_the_follow_up_prompts_point_at_the_tools(self):
        text = await self.prompt("draft_seller_message", number="2")
        self.assertIn("show_listing(number=2)", text)
        self.assertIn("English", text)
        text = await self.prompt("prepare_viewing", number="3")
        self.assertIn("viewing_checklist(number=3)", text)
        async with Client(self.server) as client:
            with self.assertRaises(MCPError):
                await client.get_prompt("prepare_viewing", {"number": "0"})


class StdioTests(unittest.IsolatedAsyncioTestCase):
    """The real thing: `python -m deal_scout mcp` as a subprocess, over stdio."""

    async def test_review_format_over_stdio(self):
        if sys.platform == "win32":
            self.skipTest("the stdio subprocess test is only run on POSIX platforms")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shutil.copytree(FIXTURES / "domains" / "gadget", home / "domains" / "gadget")
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "deal_scout", "mcp"],
                env={"DEAL_SCOUT_HOME": str(home), "DOMAIN": "gadget"},
                cwd=home,
            )
            try:
                with anyio.fail_after(60):
                    async with Client(params) as client:
                        names = {t.name for t in (await client.list_tools()).tools}
                        res = await client.call_tool("review_format", {})
            except OSError as exc:
                self.skipTest(f"cannot start the server subprocess here: {exc}")
        self.assertIn("submit_review", names)
        self.assertEqual(res.structured_content["specs"], ["Price vs market", "Condition", "Battery", "Accessories"])


if __name__ == "__main__":
    unittest.main()
