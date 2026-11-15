"""The Claude Code command line."""
import unittest

from fakes import CAMPERVAN

from deal_scout import analysis, prompts


class CommandTests(unittest.TestCase):
    def test_claude_cannot_run_commands_or_browse_or_write_outside_listings(self):
        cmd = analysis.claude_command("007", "opus", CAMPERVAN)
        tools = cmd[cmd.index("--tools") + 1]
        self.assertNotIn("Bash", tools)
        self.assertNotIn("Web", tools)
        self.assertIn("--restricted", cmd)
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--permission-mode")]
        self.assertEqual([a for a in allowed if a.startswith(("Write", "Edit"))],
                         ["Write(./listings/**)", "Edit(./listings/**)"])
        self.assertIn("inbox/007/", cmd[2])
        self.assertIn("Never write phone numbers into any file", cmd[2])
        self.assertIn("seller_name", prompts.schema(CAMPERVAN)["required"])
        self.assertIn("seller_message", prompts.schema(CAMPERVAN)["required"])
        self.assertIn("grammatical gender", cmd[2])
        self.assertIn("knowledge.md (the scoring reference", cmd[2])
        self.assertIn("First read these files in domains/campervan/:", cmd[2])
        self.assertIn("overrides knowledge.md's", cmd[2])
        self.assertIn("listings/market.md", cmd[2])
        self.assertIn("base-vans.md", cmd[2])
        self.assertIn("Do not ask about a viewing, a test drive", cmd[2])


if __name__ == "__main__":
    unittest.main()
