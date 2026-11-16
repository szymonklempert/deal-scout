"""The MCP server's wiring: tool annotations, prompts, and the real thing over stdio."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from test_golden import FIXTURES, GOLDEN
from test_mcp_reading import ServerCase

from deal_scout import prompts

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


class AnnotationTests(ServerCase):
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
