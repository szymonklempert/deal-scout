"""Settings: what each environment variable becomes, and what must never be logged or passed on."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
import config  # noqa: E402


class LoadEnvTests(unittest.TestCase):
    def test_env_file_does_not_override(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("# c\nFOO_TEST_KEY=1\nBAR_TEST_KEY='x y'\n")
            os.environ["FOO_TEST_KEY"] = "keep"
            self.addCleanup(os.environ.pop, "FOO_TEST_KEY", None)
            self.addCleanup(os.environ.pop, "BAR_TEST_KEY", None)
            config.load_env(env)
            self.assertEqual(os.environ["FOO_TEST_KEY"], "keep")
            self.assertEqual(os.environ["BAR_TEST_KEY"], "x y")


class SettingsTests(unittest.TestCase):
    def test_defaults_from_an_empty_environment(self):
        s = config.Settings.from_env({})
        self.assertEqual(s, config.Settings())
        self.assertEqual((s.provider, s.effort, s.analysis, s.claude_bin), ("claude-code", "medium", "off", "claude"))
        self.assertEqual((s.group_window_seconds, s.allowed_user_id, s.model, s.claude_model), (180, None, "", ""))

    def test_domain_defaults_to_campervan(self):
        self.assertEqual(config.Settings.from_env({}).domain, "campervan")
        self.assertEqual(config.Settings.from_env({"DOMAIN": ""}).domain, "campervan")
        self.assertEqual(config.Settings.from_env({"DOMAIN": "tv"}).domain, "tv")

    def test_provider_is_normalised_but_not_validated(self):
        self.assertEqual(config.Settings.from_env({"PROVIDER": " OpenAI "}).provider, "openai")
        self.assertEqual(config.Settings.from_env({"PROVIDER": ""}).provider, "claude-code")
        self.assertEqual(config.Settings.from_env({"PROVIDER": "Gemini"}).provider, "gemini")

    def test_model_effort_and_claude_settings(self):
        s = config.Settings.from_env({"MODEL": " gpt-x ", "CLAUDE_MODEL": " sonnet ", "EFFORT": " high ",
                                      "CLAUDE_BIN": "/opt/claude", "PRICE_PER_MTOK": "2,8"})
        self.assertEqual((s.model, s.claude_model, s.effort), ("gpt-x", " sonnet ", "high"))
        self.assertEqual((s.claude_bin, s.price_per_mtok), ("/opt/claude", "2,8"))
        self.assertEqual(config.Settings.from_env({"EFFORT": ""}).effort, "medium")
        self.assertEqual(config.Settings.from_env({"CLAUDE_BIN": ""}).claude_bin, "claude")

    def test_telegram_settings(self):
        s = config.Settings.from_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_ALLOWED_USER_ID": "42",
                                      "ANALYSIS": " Auto ", "GROUP_WINDOW_SECONDS": "60"})
        self.assertEqual((s.telegram_token, s.allowed_user_id, s.analysis, s.group_window_seconds),
                         ("t", 42, "auto", 60))
        self.assertIsNone(config.Settings.from_env({"TELEGRAM_ALLOWED_USER_ID": ""}).allowed_user_id)

    def test_sheet_settings(self):
        s = config.Settings.from_env({"GOOGLE_SHEET_URL": "u", "GOOGLE_SHEET_SECRET": "x",
                                      "GOOGLE_SHEET_LINK": " https://docs.google.com/spreadsheets/d/a ",
                                      "CAMPERVAN_ROOT": "/tmp/x", "OPENROUTER_API_KEY": "k"})
        self.assertEqual((s.sheet_url, s.sheet_secret, s.data_root, s.openrouter_api_key), ("u", "x", "/tmp/x", "k"))
        self.assertEqual(s.sheet_link, "https://docs.google.com/spreadsheets/d/a")

    def test_child_env_drops_secrets_and_keeps_the_rest(self):
        environ = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_ALLOWED_USER_ID": "1", "ANTHROPIC_API_KEY": "k",
                   "ANTHROPIC_AUTH_TOKEN": "a", "PATH": "/usr/bin"}
        self.assertEqual(config.child_env(environ), {"PATH": "/usr/bin"})
        self.assertEqual(dict(config.Settings.from_env(environ).claude_env), {"PATH": "/usr/bin"})

    def test_repr_hides_secrets(self):
        s = config.Settings(telegram_token="tok-123", sheet_secret="s3cret", sheet_url="https://x/exec",
                            openrouter_api_key="or-key", claude_env={"ANTHROPIC_API_KEY": "leak"})
        text = repr(s)
        for secret in ("tok-123", "s3cret", "https://x/exec", "or-key", "leak"):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
