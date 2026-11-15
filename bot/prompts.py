"""The analysis prompt and the JSON schema the model must answer with."""
from pathlib import Path

from domain import Domain

FACT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["field", "value", "certainty"],
    "properties": {
        "field": {"type": "string"},
        "value": {"type": "string"},
        "certainty": {"type": "string", "enum": ["read", "unclear", "not shown"]},
    },
}


CHECK = {
    "type": "object",
    "additionalProperties": False,
    "required": ["criterion", "result", "deal_breaker", "importance"],
    "properties": {
        "criterion": {"type": "string"},
        "importance": {"type": "string", "enum": ["required", "big plus", "nice to have"]},
        "result": {"type": "string", "enum": ["met", "missed", "unknown"]},
        "deal_breaker": {"type": "boolean"},
    },
}


def schema(d: Domain, card: bool = False) -> dict:
    """The JSON shape the model must return: fixed fields plus the domain's specs and categories. With card=True (API
    providers, which can't write files) it also carries the card's text."""
    spec = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "value", "score"],
        "properties": {
            "name": {"type": "string", "enum": d.specs},
            "value": {"type": "string"},
            "score": {"type": ["integer", "null"]},  # 0-10; clamped in ordered_specs (not every provider takes limits)
        },
    }
    props = {
        "title": {"type": "string"},
        "seller_name": {"type": ["string", "null"]},
        "source": {"type": ["string", "null"]},
        "location": {"type": ["string", "null"]},
        "specs": {"type": "array", "items": spec},
        "price_net": {"type": "boolean"},
        "fair_price_low": {"type": ["integer", "null"]},
        "fair_price_high": {"type": ["integer", "null"]},
        "price_pln": {"type": ["number", "null"]},
        "model": {"type": ["string", "null"]},
        "year": {"type": ["integer", "null"]},
        "mileage_km": {"type": ["integer", "null"]},
        "facts": {"type": "array", "items": FACT},
        "criteria_checks": {"type": "array", "items": CHECK},
        "missing": {"type": "array", "items": {"type": "string"}},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string", "enum": ["worth a visit", "ask first", "skip"]},
        "summary": {"type": "string"},
        "seller_message": {"type": "string"},
        "seller_sms": {"type": "string"},
        "seller_phone": {"type": ["string", "null"]},
        "contact_preference": {"type": "string", "enum": ["whatsapp", "sms", "phone call", "messenger", "not stated"]},
    }
    for key, cat in d.categories.items():
        props[key] = {"type": "string", "enum": list(cat["values"])}
    if card:
        props["card_markdown"] = {"type": "string"}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


INTRO_CLAUDE_CODE = """You are reviewing a listing for a {item}. Process only the listing in inbox/{nnn}/, following
CLAUDE.md. First read these files in {ddir}/: {criteria}checklist.md, card-template.md{refs}.
Also read listings/market.md if it exists (asking prices from the owner's own listings).
Then look at every photo in inbox/{nnn}/ and read notes.txt and links.txt if they exist.

Write listings/{nnn}.md from {ddir}/card-template.md. In its fit section list what is met, missed and unknown and
any deal breakers hit, but give no numeric score: a script calculates the score from your criteria checks.
Do not create or edit any other file (a script updates listings/INDEX.md and the spreadsheet).

"""


INTRO_API = """You are reviewing a listing for a {item} for a private buyer. The buyer's files follow these
instructions: {criteria}checklist.md, card-template.md{refs}. The listing itself (photos, the owner's notes and links,
and market prices from the owner's own listings if any) is in the user message. Use only what you are given.

Write the listing card in card_markdown, following card-template.md. In its fit section list what is met, missed and
unknown and any deal breakers hit, but give no numeric score: a script calculates the score from your criteria checks.

"""


RULES = """Rules for this kind of listing (from {ddir}/instructions.md):
{instructions}

Then return the structured result:
- title: short{title_example}.
- seller_name: the seller's first and last name (or company name) exactly as the listing shows it; null if not shown.
- source: where it was posted: the group or site name exactly as the screenshot shows it (e.g. a Facebook group's
  name, "Facebook Marketplace", "OLX", "Otomoto"), from the screenshots, notes and links; null if you can't tell.
- location: town (and region if shown); null if not shown.
- specs: exactly one entry for each of these, in this order: {specs}.
  value: what the listing says, at most 40 characters, or "not shown".
  score: 0-10 from the reference files and the rules above, adjusted where criteria.md asks for more or less.
  Not shown at all: value "not shown" and score null. Mentioned but vague: at most 3. Never score what you can't see.
  "Price vs market" (if in the list): use the closest group in listings/market.md when it has 8 or more similar
  items (its median is the market price), otherwise the closest fair price range in the reference files. Value like
  "93 000 PLN · market ~84k (n=14) · fair 85-95k" (leave out what you don't have). 10 clearly below market, 7 at
  market, 5 up to ~10% above, 3 well above, 0 suspiciously low (30%+ below market: the scam pattern). Compare gross
  prices (net + 23% VAT). With nothing comparable, say so and score null.
{categories}- price_net: true when the price is net (before VAT), else false.
- fair_price_low, fair_price_high: the fair price range from the reference files you compared with, in PLN, or null.
- price_pln, year, mileage_km, model: null when not shown, unreadable or not applicable. Never guess. Convert other
  currencies only if the listing itself gives PLN; otherwise null and mention the price in facts.
- facts: the rows of the card's facts table, with certainty "read", "unclear" or "not shown".
- criteria_checks: one entry per concrete criterion filled in in criteria.md (each hard limit, must-have,
  nice-to-have and deal breaker). result is "unknown" when the listing does not show it. importance: "nice to have"
  for the nice-to-haves, "big plus" for the ones marked BIG PLUS, "required" for everything else. deal_breaker is
  true for the items under "Deal breakers" and for hard limits; nice-to-haves are never deal breakers. With no
  criteria filled in, return an empty list.
- missing: details a buyer needs that the listing does not show (use checklist.md), most important first, a few
  words each.
- red_flags: from checklist.md, the reference files and what the listing shows.
- summary: one short sentence: the main reason for the verdict.
- seller_message: a ready-to-send first message to the seller (WhatsApp or Messenger), in {language}, polite and
  plain. It is the first contact, to decide whether the {unit} is worth seeing, so ask only what the seller can
  answer remotely, following the rules above. Greeting, one line saying which {unit} it is about, then numbered
  questions, at most 10, none the listing already answers. End with a short thanks. Do not sign it with a name.
  Use the same text as the draft in the card.
- seller_sms: the same first contact as a short SMS in {language}, same tone: greeting and which {unit}, then the 4-5
  most important questions. At most 400 characters.
- seller_phone: the seller's phone number exactly as the listing shows it, or null. It is only used to build a
  WhatsApp link; never write it into the card or any other file.
- contact_preference: how the listing asks to be contacted ("whatsapp", "sms", "phone call", "messenger"), or
  "not stated".
Put the seller's name only in the card's seller line and in seller_name. Never write phone numbers into any file."""


def prompt(nnn: str, d: Domain, api: bool = False) -> str:
    """The instructions. Claude Code reads the files itself; for API providers the files are appended (api_files)."""
    cats = "".join("- %s: one of the allowed values%s; \"unknown\" when the listing doesn't let you tell.\n"
                   % (key, ": " + cat["help"] if cat.get("help") else "") for key, cat in d.categories.items())
    return ((INTRO_API if api else INTRO_CLAUDE_CODE) + RULES).format(
        item=d.item, unit=d.unit, nnn=nnn, ddir=d.rel, language=d.seller_language,
        criteria="criteria.md, " if d.has_criteria() else "",
        refs="".join(", %s (%s)" % (f, what) for f, what in d.reference_files.items()),
        instructions=d.instructions or "(none)", specs=", ".join(d.specs), categories=cats,
        title_example=', e.g. "%s"' % d.title_example if d.title_example else "")


def api_request(root, nnn, d: Domain):
    """(system, user_text, photo paths) for an API provider. The system prompt holds the instructions and the domain's
    files (the same for every listing, so providers can cache them); the listing goes in the user turn."""
    files = ((["criteria.md"] if d.has_criteria() else []) + ["checklist.md", "card-template.md"]
             + list(d.reference_files))
    parts = [prompt(nnn, d, api=True)]
    for name in files:
        path = d.dir / name
        if path.exists():
            parts.append("\n===== %s =====\n%s" % (name, path.read_text(encoding="utf-8").strip()))
    folder = Path(root) / "inbox" / nnn
    user = ["Listing #%s." % nnn]
    for name, label in (("notes.txt", "The owner's notes"), ("links.txt", "Links")):
        if (folder / name).exists():
            user.append("%s:\n%s" % (label, (folder / name).read_text(encoding="utf-8").strip()))
    market_md = Path(root) / "listings" / "market.md"
    if market_md.exists():
        user.append("Market prices from the owner's own listings:\n" + market_md.read_text(encoding="utf-8").strip())
    photos = sorted(x for x in folder.glob("photo_*") if x.is_file())
    user.append("%d photo(s) of the listing are attached, in order." % len(photos))
    return "\n".join(parts), "\n\n".join(user), photos
