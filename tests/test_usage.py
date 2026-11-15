import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
import usage  # noqa: E402

OUT = {"num_turns": 6, "duration_ms": 52000, "total_cost_usd": 0.3,
       "modelUsage": {"claude-opus-5-5": {"inputTokens": 1000, "cacheCreationInputTokens": 20000,
                                           "cacheReadInputTokens": 60000, "outputTokens": 4000, "costUSD": 0.25},
                      "claude-haiku-5-5": {"inputTokens": 500, "outputTokens": 100, "costUSD": 0.01}}}


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.addCleanup(self.dir.cleanup)

    def test_record_sums_models(self):
        rec = usage.record(self.root, 7, OUT, ok=True)
        self.assertEqual(rec["input_tokens"], 81500)
        self.assertEqual(rec["cached_tokens"], 60000)
        self.assertEqual(rec["output_tokens"], 4100)
        self.assertEqual(rec["cost_usd"], 0.26)
        self.assertEqual(rec["duration_s"], 52.0)
        self.assertEqual(len(usage.load(self.root / "logs/usage.jsonl")), 1)

    def test_failed_run_recorded(self):
        rec = usage.record(self.root, 7, {}, ok=False)
        self.assertEqual((rec["ok"], rec["input_tokens"], rec["cost_usd"]), (False, 0, 0))

    def test_summary_windows(self):
        usage.record(self.root, 1, OUT, ok=True)
        usage.record(self.root, 2, OUT, ok=True)
        usage.record(self.root, 3, {}, ok=False)
        path = self.root / "logs/usage.jsonl"
        text = usage.summary(path)
        self.assertIn("Last 7 days: 3 analyses (1 failed) · 163k tokens in (74% cached) · 8k out · ~$0.52", text)
        self.assertIn("per listing ~$0.26, 52 s", text)
        later = usage.summary(path, now=time.time() + 10 * 86400)
        self.assertIn("Last 7 days: nothing", later)
        self.assertIn("Last 30 days: 3 analyses", later)
        self.assertIn("/usage in Claude Code", text)


if __name__ == "__main__":
    unittest.main()
