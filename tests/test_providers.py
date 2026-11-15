"""The API providers, with fake Anthropic / OpenAI clients: no network, no keys."""
import dataclasses
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as Ns

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
from test_process import RESULT  # noqa: E402

import process  # noqa: E402
import providers  # noqa: E402
import usage  # noqa: E402

SETTINGS = config.Settings()
CARD = "# #001 Fiat Ducato\n- Seller: Jan Kowalski, tel. +48 600-123-456\n"


def answer(**extra):
    return json.dumps(dict(RESULT, card_markdown=CARD, **extra))


class FakeAnthropic:
    def __init__(self, text=None, stop_reason="end_turn"):
        self.calls = []
        msg = Ns(stop_reason=stop_reason, model="claude-opus-5-5", stop_details=Ns(category="cyber"),
                 content=[Ns(type="thinking", thinking=""), Ns(type="text", text=text or answer())],
                 usage=Ns(input_tokens=1200, cache_read_input_tokens=8000, cache_creation_input_tokens=0,
                          output_tokens=5000))

        def stream(kind):
            @contextmanager
            def run(**kw):
                self.calls.append((kind, kw))
                yield Ns(get_final_message=lambda: msg)
            return run

        self.messages = Ns(stream=stream("plain"))
        self.beta = Ns(messages=Ns(stream=stream("beta")))


class FakeOpenAI:
    def __init__(self, content=None, refusal=None, finish_reason="stop", cost=None):
        self.calls = []
        usage_ = Ns(prompt_tokens=9000, completion_tokens=4000, prompt_tokens_details=Ns(cached_tokens=6000))
        if cost is not None:
            usage_.cost = cost
        resp = Ns(model="some-model", usage=usage_, choices=[Ns(
            finish_reason=finish_reason, message=Ns(content=content or answer(), refusal=refusal))])

        def create(**kw):
            self.calls.append(kw)
            return resp
        self.chat = Ns(completions=Ns(create=create))


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
        return process.process_listing(self.root, 1, settings=settings, model=model, client=client)

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
        self.assertIn("Seller: Jan Kowalski", out[0]["text"])
        rec = usage.load(self.root / "logs/usage.jsonl")[-1]
        self.assertEqual((rec["ok"], rec["provider"], rec["input_tokens"], rec["cached_tokens"]),
                         (True, "anthropic", 9200, 8000))

    def test_anthropic_model_without_fallback_and_effort_off(self):
        client = FakeAnthropic()
        self.run_listing("anthropic", client, model="claude-haiku-4-5", effort="none")
        kind, kw = client.calls[0]
        self.assertEqual(kind, "plain")
        self.assertNotIn("fallbacks", kw)
        self.assertNotIn("effort", kw["output_config"])

    def test_anthropic_refusal_is_reported_and_recorded(self):
        with self.assertRaisesRegex(process.ProcessError, "anthropic: the model declined \\(cyber\\)"):
            self.run_listing("anthropic", FakeAnthropic(stop_reason="refusal"))
        self.assertFalse(usage.load(self.root / "logs/usage.jsonl")[-1]["ok"])
        with self.assertRaisesRegex(process.ProcessError, "cut off"):
            self.run_listing("anthropic", FakeAnthropic(stop_reason="max_tokens"))
        with self.assertRaisesRegex(process.ProcessError, "valid JSON"):
            self.run_listing("anthropic", FakeAnthropic(text="Sorry, here is the card..."))

    def test_openai_needs_a_model(self):
        with self.assertRaisesRegex(process.ProcessError, "set MODEL"):
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
        with self.assertRaisesRegex(process.ProcessError, "declined: no"):
            self.run_listing("openai", FakeOpenAI(refusal="no"), model="gpt-x")
        with self.assertRaisesRegex(process.ProcessError, "cut off"):
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
                    walk(v, "%s.%s" % (path, k))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, "%s[%d]" % (path, i))
        walk(process.schema(card=True))

    def test_scores_are_clamped(self):
        data = {"specs": [{"name": "Year", "value": "2014", "score": 14}, {"name": "Mileage", "value": "x",
                                                                          "score": -2}]}
        got = {n: s for n, _, s in process.ordered_specs(data)}
        self.assertEqual((got["Year"], got["Mileage"]), (10, 0))


class ScrubTests(unittest.TestCase):
    def test_formats(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.md"
            for written in ("+48 600-123-456", "600 123 456", "0048600123456", "(600) 123 456"):
                path.write_text("call %s today, 196 000 km" % written)
                process.scrub_phone(path, "600123456")
                self.assertEqual(path.read_text(), "call [phone removed] today, 196 000 km", written)


if __name__ == "__main__":
    unittest.main()
