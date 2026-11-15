"""The core must work for any domain: here a made-up one for buying a used TV."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
from test_process import FakeRunner  # noqa: E402

import domain  # noqa: E402
import market  # noqa: E402
import process  # noqa: E402

TV = {"item": "used TV", "unit": "TV", "seller_language": "English", "title_example": "LG OLED C2 55\"",
      "reference_files": {"panels.md": "panel types and burn-in risk"},
      "specs": ["Price vs market", "Panel", "Size", "Burn-in", "Remote & box"],
      "categories": {"panel": {"values": ["OLED", "QLED", "LCD", "unknown"]}},
      "market": {"same_item_key": "model", "group_by": ["panel"], "year_bands": [[0, "any year"]]}}
RESULT = {"title": "LG OLED C2 55", "seller_name": None, "source": "OLX", "location": "Kraków", "model": "LG C2",
          "panel": "OLED", "price_net": False, "fair_price_low": None, "fair_price_high": None, "price_pln": 2900,
          "year": 2022, "mileage_km": None, "verdict": "ask first", "summary": "Good price, burn-in unknown.",
          "specs": [{"name": "Panel", "value": "OLED", "score": 9}, {"name": "Burn-in", "value": "not shown",
                                                                     "score": None}],
          "facts": [], "criteria_checks": [], "missing": ["burn-in test photo"], "red_flags": [],
          "seller_message": "Hello, is the TV still available?", "seller_sms": "", "seller_phone": None,
          "contact_preference": "not stated"}


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        d = self.root / "domains" / "tv"
        d.mkdir(parents=True)
        (d / "domain.json").write_text(json.dumps(TV))
        (d / "instructions.md").write_text("- Ask for a photo of a grey test screen to check burn-in.")
        (self.root / "inbox" / "001").mkdir(parents=True)
        self.tv = domain.load("tv", roots=[self.root])
        before = process.DOMAIN
        process.use_domain(self.tv)
        self.addCleanup(process.use_domain, before)

    def test_loads_from_the_data_folder(self):
        self.assertEqual((self.tv.name, self.tv.unit, self.tv.specs[1]), ("tv", "TV", "Panel"))
        self.assertFalse(self.tv.has_criteria())

    def test_prompt_and_schema_follow_the_domain(self):
        text = process.prompt("001")
        self.assertIn("a listing for a used TV", text)
        self.assertIn("domains/tv/: checklist.md, card-template.md, panels.md (panel types and burn-in risk)", text)
        self.assertIn("grey test screen", text)
        self.assertIn("which TV it is about", text)
        self.assertNotIn("campervan", text.lower())
        self.assertNotIn("knowledge.md", text)
        schema = process.schema()
        self.assertEqual(schema["properties"]["specs"]["items"]["properties"]["name"]["enum"], TV["specs"])
        self.assertEqual(schema["properties"]["panel"]["enum"], ["OLED", "QLED", "LCD", "unknown"])
        self.assertNotIn("model_family", schema["properties"])

    def test_full_run_for_a_tv(self):
        out = process.process_listing(self.root, 1, settings=config.Settings(), model="opus",
                                      runner=FakeRunner(RESULT))
        self.assertIn("⚪ Burn-in: <i>not shown</i>", out[0]["text"])
        self.assertIn("🟢 Panel: OLED · 9/10", out[0]["text"])
        row = process.read_rows(self.root / "listings/listings.csv")[1]
        self.assertEqual((row["panel"], row["Panel"], row["Size"]), ("OLED", "OLED (9/10)", "not shown"))
        self.assertNotIn("model_family", row)
        self.assertTrue((self.root / "listings/market.md").exists())

    def test_market_groups_by_domain_keys(self):
        rows = [{"number": str(i), "price_pln": str(2500 + 100 * i), "model": "LG C%d" % i, "panel": "OLED",
                 "year": "2022", "mileage_km": ""} for i in range(1, 5)]
        self.assertIn("## OLED", market.build(rows, TV["market"]))

    def test_unknown_domain(self):
        with self.assertRaises(domain.DomainError):
            domain.load("bicycle", roots=[self.root])


if __name__ == "__main__":
    unittest.main()
