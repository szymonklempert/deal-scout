"""The API providers, with fake Anthropic / OpenAI clients: no network, no keys."""

import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path

from fakes import CAMPERVAN, SETTINGS, FakeAnthropic, FakeOpenAI

from deal_scout import errors, process, providers, usage


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        folder = self.root / "inbox" / "001"
        folder.mkdir(parents=True)
        (folder / "photo_01.jpg").write_bytes(b"\xff\xd8fake")
        (folder / "photo_02.png").write_bytes(b"\x89PNGfake")
        (folder / "notes.txt").write_text("[2026-11-15 10:00] found in a FB group")

    def run_listing(self, provider, client, model=None, **overrides):
        settings = dataclasses.replace(SETTINGS, provider=provider, **overrides)
        return process.process_listing(self.root, 1, settings=settings, domain=CAMPERVAN, model=model, client=client)

    def test_default_is_claude_code_on_the_subscription(self):
        self.assertEqual(providers.name(SETTINGS), "claude-code")
        self.assertEqual(providers.model_for("claude-code", SETTINGS), "opus")
        with self.assertRaisesRegex(providers.ProviderError, "not one of"):
            providers.name(dataclasses.replace(SETTINGS, provider="gemini"))

    def test_anthropic_end_to_end(self):
        client = FakeAnthropic()
        out = self.run_listing("anthropic", client)
        kind, kw = client.calls[0]
        self.assertEqual((kind, kw["model"]), ("beta", "claude-opus-5-5"))  # default model, with refusal fallback
        self.assertEqual((kw["betas"], kw["fallbacks"]), (["server-side-fallback-2026-07-01"], "default"))
        self.assertEqual(kw["output_config"]["effort"], "medium")
        self.assertIn("card_markdown", kw["output_config"]["format"]["schema"]["properties"])
        system = kw["system"][0]
        self.assertEqual(system["cache_control"], {"type": "ephemeral"})  # domain files cached across listings
        self.assertIn("===== knowledge.md =====", system["text"])
        self.assertNotIn("Listing #001", system["text"])  # per-listing content stays out of the cached prefix
        content = kw["messages"][0]["content"]
        self.assertEqual([b["type"] for b in content], ["image", "image", "text"])
        self.assertEqual(content[1]["source"]["media_type"], "image/png")
        self.assertIn("found in a FB group", content[2]["text"])
        card = (self.root / "listings/001.md").read_text()
        self.assertIn("Fiat Ducato", card)
        self.assertNotIn("600", card.replace("196 000", ""))  # the phone number is scrubbed from the card
        self.assertIn("[phone removed]", card)
        self.assertNotIn("card_markdown", json.loads((self.root / "listings/001.json").read_text()))
        self.assertIn("Seller: Jan Kowalski", out[0].text)
        rec = usage.load(self.root / "logs/usage.jsonl")[-1]
        self.assertEqual(
            (rec["ok"], rec["provider"], rec["input_tokens"], rec["cached_tokens"]), (True, "anthropic", 9200, 8000)
        )

    def test_anthropic_model_without_fallback_and_effort_off(self):
        client = FakeAnthropic()
        self.run_listing("anthropic", client, model="claude-haiku-4-5", effort="none")
        kind, kw = client.calls[0]
        self.assertEqual(kind, "plain")
        self.assertNotIn("fallbacks", kw)
        self.assertNotIn("effort", kw["output_config"])

    def test_anthropic_refusal_is_reported_and_recorded(self):
        with self.assertRaisesRegex(errors.ProcessError, "anthropic: the model declined \\(cyber\\)"):
            self.run_listing("anthropic", FakeAnthropic(stop_reason="refusal"))
        self.assertFalse(usage.load(self.root / "logs/usage.jsonl")[-1]["ok"])
        with self.assertRaisesRegex(errors.ProcessError, "cut off"):
            self.run_listing("anthropic", FakeAnthropic(stop_reason="max_tokens"))
        with self.assertRaisesRegex(errors.ProcessError, "valid JSON"):
            self.run_listing("anthropic", FakeAnthropic(text="Sorry, here is the card..."))

    def test_openai_needs_a_model(self):
        with self.assertRaisesRegex(errors.ProcessError, "set MODEL"):
            self.run_listing("openai", FakeOpenAI())

    def test_openai_end_to_end(self):
        client = FakeOpenAI()
        self.run_listing("openai", client, model="gpt-x", price_per_mtok="2,8")
        kw = client.calls[0]
        self.assertEqual(kw["model"], "gpt-x")
        fmt = kw["response_format"]
        self.assertEqual((fmt["type"], fmt["json_schema"]["strict"]), ("json_schema", True))
        self.assertEqual(kw["messages"][0]["role"], "system")
        images = [b for b in kw["messages"][1]["content"] if b["type"] == "image_url"]
        self.assertTrue(images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertTrue((self.root / "listings/001.md").exists())
        rec = usage.load(self.root / "logs/usage.jsonl")[-1]
        self.assertEqual((rec["provider"], rec["cached_tokens"], rec["cost_usd"]), ("openai", 6000, 0.05))

    def test_openai_refusal_and_cut_off(self):
        with self.assertRaisesRegex(errors.ProcessError, "declined: no"):
            self.run_listing("openai", FakeOpenAI(refusal="no"), model="gpt-x")
        with self.assertRaisesRegex(errors.ProcessError, "cut off"):
            self.run_listing("openai", FakeOpenAI(finish_reason="length"), model="gpt-x")

    def test_openrouter_needs_a_key_and_reports_cost(self):
        with self.assertRaisesRegex(providers.ProviderError, "OPENROUTER_API_KEY"):
            providers.call("openrouter", "m", "s", "u", [], {}, SETTINGS)
        self.run_listing("openrouter", FakeOpenAI(cost=0.031), model="anthropic/claude-x")
        self.assertEqual(usage.load(self.root / "logs/usage.jsonl")[-1]["cost_usd"], 0.031)

    def test_missing_sdk_is_explained(self):
        real = sys.modules.get("anthropic")
        sys.modules["anthropic"] = None  # makes `import anthropic` fail
        try:
            with self.assertRaisesRegex(providers.ProviderError, "pip install anthropic"):
                providers.call_anthropic("m", "s", "u", [], {})
        finally:
            if real is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = real


if __name__ == "__main__":
    unittest.main()
