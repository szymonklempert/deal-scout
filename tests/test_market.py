import tempfile
import unittest
from pathlib import Path

from deal_scout import domain, market, storage

CAMPERVAN = domain.load("campervan")
CFG = CAMPERVAN.market

FAM = "Sprinter W906 (2006-2018)"


def row(
    n,
    price,
    year=2011,
    km=180000,
    seller="",
    build="DIY",
    reg="M1",
    family=FAM,
    net="",
    price_score=7,
    fair=(65000, 80000),
):
    return {
        "number": str(n),
        "price_pln": str(price) if price is not None else "",
        "year": str(year),
        "mileage_km": str(km),
        "seller": seller,
        "model_family": family,
        "build": build,
        "registration": reg,
        "body": "van conversion",
        "price_net": net,
        "fair_price_low": str(fair[0]) if fair else "",
        "fair_price_high": str(fair[1]) if fair else "",
        "Price vs market": f"x ({price_score}/10)" if price_score is not None else "not shown",
    }


class BandTests(unittest.TestCase):
    def test_bands_match_knowledge_md(self):
        self.assertEqual(market.year_band("2011", CFG["year_bands"]), "2009-2012")
        self.assertEqual(market.year_band("2018", CFG["year_bands"]), "2018+")
        self.assertEqual(market.year_band("", CFG["year_bands"]), "year unknown")
        self.assertEqual(market.km_band("119999", CFG["km_bands"]), "<120k km")
        self.assertEqual(market.km_band("200000", CFG["km_bands"]), "200-280k km")
        self.assertEqual(market.km_band("450000", CFG["km_bands"]), "400k+ km")

    def test_defaults_without_domain_settings(self):
        rows = [dict(row(i, 80000 + i * 1000, km=100000 + i * 5000), model_family="") for i in range(1, 5)]
        self.assertIn("## any year", market.build(rows))

    def test_net_price_gets_vat(self):
        self.assertEqual(market.gross_price(row(1, 100000, net="yes")), 123000)
        self.assertEqual(market.gross_price(row(1, 100000)), 100000)


class GroupTests(unittest.TestCase):
    def test_repost_counts_once_at_latest_price_and_drop_is_reported(self):
        rows = [
            row(1, 90000, seller="Jan K", km=180000),
            row(2, 84000, seller="jan  k", km=181500),
            row(3, 80000, km=150000),
            row(4, 82000, km=160000),
            row(5, 86000, km=170000),
        ]
        self.assertEqual(len(market.items(rows)), 4)
        text = market.build(rows, CFG, today="2026-11-15")
        self.assertIn("4 item(s) from 5 listing(s)", text)
        self.assertIn("## Sprinter W906 (2006-2018) · 2009-2012", text)
        self.assertIn("median 83k PLN", text)  # 80, 82, 84, 86
        self.assertIn("1 price drop(s), avg -7%", text)

    def test_different_sellers_are_different_items(self):
        self.assertFalse(market.same_item(row(1, 1, seller="A"), row(2, 1, seller="B")))
        self.assertFalse(market.same_item(row(1, 1, km=100000), row(2, 1, km=110000)))
        self.assertFalse(market.same_item(row(1, 1, family="unknown"), row(2, 1, family="unknown")))

    def test_small_groups_hidden_and_exclusions_counted(self):
        rows = [
            row(1, 80000),
            row(2, 82000, km=100000),
            row(3, None, km=120000),
            row(4, 30000, km=90000, price_score=0),
        ]
        text = market.build(rows, CFG, today="2026-11-15")
        self.assertIn("left out: 1 without a price, 1 suspiciously low", text)
        self.assertIn("Not enough data yet", text)

    def test_warning_when_asking_prices_beat_the_fair_range(self):
        rows = [row(i, p, km=100000 + i * 10000) for i, p in enumerate([88000, 90000, 92000, 94000], 1)]
        self.assertIn("⚠️ asking median 14% above your fair range", market.build(rows, CFG))
        rows = [row(i, p, km=100000 + i * 10000, fair=None) for i, p in enumerate([88000, 90000, 92000, 94000], 1)]
        self.assertNotIn("⚠️", market.build(rows, CFG))

    def test_finer_groups_shown_when_they_differ(self):
        rows = [row(i, 80000 + i * 1000, km=100000 + i * 5000, build="pro") for i in range(1, 5)]
        rows += [row(i, 70000 + i * 1000, km=300000 + i * 5000, build="DIY") for i in range(5, 9)]
        text = market.build(rows, CFG)
        self.assertIn("- all: median", text)
        self.assertIn("- DIY build: median", text)
        self.assertIn("- pro build: median", text)


class WriteTests(unittest.TestCase):
    def test_write_from_csv(self):
        with tempfile.TemporaryDirectory() as d:
            listings = Path(d)
            rows = {i: dict(row(i, 80000 + i * 1000, km=100000 + i * 5000), title="t") for i in range(1, 5)}
            storage.write_rows(listings / "listings.csv", rows, storage.csv_fields(CAMPERVAN))
            market.write(listings, CFG)
            self.assertIn("median 82.5k PLN", (listings / "market.md").read_text())


class GroupMediansTests(unittest.TestCase):
    def test_labels_match_the_market_headings_and_small_groups_are_left_out(self):
        rows = [row(i, 80000 + i * 1000, km=100000 + i * 20000) for i in range(1, 6)]
        rows += [row(9, 50000, family="Other (2000)"), row(10, 51000, year=2020, family="Other (2000)")]
        medians = market.group_medians(rows, CFG)
        self.assertEqual(list(medians), [f"{FAM} · 2009-2012"])
        self.assertEqual(medians[f"{FAM} · 2009-2012"], (83000, 5))
        self.assertIn(f"## {FAM} · 2009-2012", market.build(rows, CFG))

    def test_no_groups_without_enough_data(self):
        self.assertEqual(market.group_medians([row(1, 80000)], CFG), {})


if __name__ == "__main__":
    unittest.main()
