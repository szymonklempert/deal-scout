"""The MCP server: tools, resources and prompts, over an in-memory client on a temporary data home."""

import json
import tempfile
import unittest
from pathlib import Path

from fakes import FakeRunner
from mcp import Client
from mcp.shared.exceptions import MCPError
from test_golden import FIXTURES, result

from deal_scout import config, domain, process, storage
from deal_scout.mcp_server import create_server

PHONE_DIGITS = "600111222"
READ_ONLY_TOOLS = {"list_listings", "top", "compare", "show_listing", "market_prices", "review_format"}


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
        self.assertEqual(set(tools), READ_ONLY_TOOLS)
        for tool in tools.values():
            hints = tool.annotations
            self.assertTrue(hints.read_only_hint, tool.name)
            self.assertFalse(hints.destructive_hint, tool.name)
            self.assertTrue(hints.idempotent_hint, tool.name)
            self.assertIsNotNone(tool.output_schema, tool.name)

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


if __name__ == "__main__":
    unittest.main()
