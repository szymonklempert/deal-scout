# Domains

A domain is the kind of thing being bought. The code is generic; everything specific lives in `domains/<name>/`.
Pick the active one with `DOMAIN=<name>` in `.env` (the default is `campervan`).

| File | What it is |
|---|---|
| `domain.json` | item name, the seller's language, the spec list, categories, market grouping |
| `instructions.md` | rules added to the analysis prompt: how to score, what to ask the seller, local quirks |
| `criteria.example.md` | template for the buyer's wishes; copy it to `criteria.md` (git-ignored) and fill it in |
| `checklist.md` | what to check on any listing of this kind: papers, condition, red flags |
| `card-template.md` | the layout of a listing card |
| reference files | knowledge the reviewer reads on every run, listed in `domain.json` (e.g. scoring bands, known faults) |

## domain.json

```json
{
  "item": "used TV",
  "unit": "TV",
  "seller_language": "Polish",
  "title_example": "LG OLED C2 55\"",
  "reference_files": {"panels.md": "panel types, burn-in risk, fair prices"},
  "specs": ["Price vs market", "Panel", "Size", "Burn-in", "Remote & box", "Warranty"],
  "categories": {"panel": {"values": ["OLED", "QLED", "LCD", "unknown"]}},
  "market": {"same_item_key": "model", "group_by": ["panel"], "detail_by": [], "finest_by": []}
}
```

- **specs:** one line in every summary and one column in the sheet, each scored 0–10. Keep "Price vs market" first
  to use the market comparison.
- **categories:** fixed choices the model must pick from. They become sheet columns and can be used to group market
  prices.
- **market:**
  - `group_by`, `detail_by` and `finest_by` list category keys, plus `"years"` and `"km"` (banded by
    `year_bands` and `km_bands`);
  - `same_item_key` identifies reposts of the same item.

## Making a good domain

- **Reference files:** for each spec, write what scores 10 / 7 / 5 / 3 / 0, with concrete thresholds. Consistent
  bands are what make scores comparable across listings. Add a "watch" line for known traps.
- **Uncertain facts:** mark anything legal or uncertain with "(verify)", so the reviewer doesn't state it as fact.
- **Size:** keep the reference files short (a few thousand tokens). They're read on every run.
- **Where the content comes from:** a good way to write them is to ask a Claude session that has been helping you
  research the market to write them up in this format.

`tests/test_domain.py` runs the whole pipeline with a made-up TV domain.
