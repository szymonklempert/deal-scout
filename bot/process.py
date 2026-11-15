#!/usr/bin/env python3
"""Analyse one saved listing with Claude Code, score it, and update the spreadsheet.

    python3 bot/process.py 7        # analyse inbox/007/

Claude Code (`claude -p`, logged in with your Claude subscription) reads the screenshots and the active domain's
files (domains/<name>/), writes listings/007.md, and returns the facts as JSON. This script then calculates the score
from Claude's criteria checks (so the same checks always give the same score), saves listings/007.json, rewrites
listings/listings.csv and listings/INDEX.md, and returns a short summary for Telegram. Nothing here is specific to
what is being bought: that comes from the domain (bot/domain.py). Standard library only.
"""
import csv
import html
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import domain as domains
import market
import sheet
import usage

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("dealscout")
log.addHandler(logging.NullHandler())  # quiet unless setup_logging() adds real handlers

DOMAIN = domains.load()


def use_domain(d):
    """Switch the active domain (the bot uses one per run; tests switch it)."""
    global DOMAIN
    DOMAIN = d


FACT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["field", "value", "certainty"],
    "properties": {
        "field": {"type": "string"},
        "value": {"type": "string"},
        "certainty": {"enum": ["read", "unclear", "not shown"]},
    },
}
CHECK = {
    "type": "object",
    "additionalProperties": False,
    "required": ["criterion", "result", "deal_breaker", "importance"],
    "properties": {
        "criterion": {"type": "string"},
        "importance": {"enum": ["required", "big plus", "nice to have"]},
        "result": {"enum": ["met", "missed", "unknown"]},
        "deal_breaker": {"type": "boolean"},
        "note": {"type": "string"},
    },
}


def schema(d=None):
    """The JSON shape Claude must return: fixed fields plus the domain's specs and categories."""
    d = d or DOMAIN
    spec = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "value", "score"],
        "properties": {
            "name": {"enum": d.specs},
            "value": {"type": "string"},
            "score": {"type": ["integer", "null"], "minimum": 0, "maximum": 10},
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
        "verdict": {"enum": ["worth a visit", "ask first", "skip"]},
        "summary": {"type": "string"},
        "seller_message": {"type": "string"},
        "seller_sms": {"type": "string"},
        "seller_phone": {"type": ["string", "null"]},
        "contact_preference": {"enum": ["whatsapp", "sms", "phone call", "messenger", "not stated"]},
    }
    for key, cat in d.categories.items():
        props[key] = {"enum": list(cat["values"])}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


PROMPT = """You are reviewing a listing for a {item}. Process only the listing in inbox/{nnn}/, following CLAUDE.md.
First read these files in {ddir}/: {criteria}checklist.md, card-template.md{refs}.
Also read listings/market.md if it exists (asking prices from the owner's own listings).
Then look at every photo in inbox/{nnn}/ and read notes.txt and links.txt if they exist.

Write listings/{nnn}.md from {ddir}/card-template.md. In its fit section list what is met, missed and unknown and
any deal breakers hit, but give no numeric score: a script calculates the score from your criteria checks.
Do not create or edit any other file (a script updates listings/INDEX.md and the spreadsheet).

Rules for this kind of listing (from {ddir}/instructions.md):
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


def prompt(nnn, d=None):
    d = d or DOMAIN
    cats = "".join("- %s: one of the allowed values%s; \"unknown\" when the listing doesn't let you tell.\n"
                   % (key, ": " + cat["help"] if cat.get("help") else "") for key, cat in d.categories.items())
    return PROMPT.format(
        item=d.item, unit=d.unit, nnn=nnn, ddir=d.rel, language=d.seller_language,
        criteria="criteria.md, " if d.has_criteria() else "",
        refs="".join(", %s (%s)" % (f, what) for f, what in d.reference_files.items()),
        instructions=d.instructions or "(none)", specs=", ".join(d.specs), categories=cats,
        title_example=', e.g. "%s"' % d.title_example if d.title_example else "")


def csv_fields(d=None):
    d = d or DOMAIN
    return (["number", "title", "seller", "source", "location", "link", "price_pln", "price_net", "model"]
            + list(d.categories)
            + ["year", "mileage_km", "fair_price_low", "fair_price_high", "score", "specs_avg", "met", "missed",
               "unknown", "deal_breakers_hit", "missing", "red_flags", "verdict", "status", "first_seen",
               "processed"]
            + d.specs)


class ProcessError(Exception):
    pass


def claude_command(nnn, model, d=None):
    """The headless Claude Code call: read anything in the project, write only into listings/, no shell, no web."""
    return [
        os.environ.get("CLAUDE_BIN") or "claude", "-p", prompt(nnn, d),
        "--model", model,
        "--tools", "Read,Write,Edit,Glob",
        "--allowedTools", "Read", "Glob", "Write(./listings/**)", "Edit(./listings/**)",
        "--permission-mode", "dontAsk",
        "--restricted", "--strict-mcp-config", "--no-session-persistence",
        "--output-format", "json",
        "--json-schema", json.dumps(schema(d)),
    ]


def run_claude(root, nnn, model, runner=subprocess.run, timeout=900):
    try:
        # Claude never needs the bot token; and without an API key in its environment Claude Code always uses the
        # Claude subscription login, so a key added somewhere later can't quietly switch it to pay-per-use.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("TELEGRAM_") and k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        proc = runner(claude_command(nnn, model), cwd=str(root), env=env, capture_output=True, text=True,
                      timeout=timeout)
    except FileNotFoundError:
        raise ProcessError("Claude Code (`claude`) is not installed or not on PATH; set CLAUDE_BIN in .env")
    except subprocess.TimeoutExpired:
        raise ProcessError("Claude took longer than %d s" % timeout)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        log.error("#%s claude exit %s, no JSON. stderr: %s", nnn, proc.returncode, (proc.stderr or "")[-2000:])
        usage.record(root, int(nnn), {}, ok=False)
        raise ProcessError("Claude failed (exit %s): %s" % (proc.returncode, (proc.stderr or proc.stdout)[-300:]))
    # Only metadata goes to the log, never the listing content (names, phone numbers).
    log.info("#%s claude: %s, %.0f s, %s turns, est. $%.2f, %d permission denial(s) %s", nnn, out.get("subtype"),
             (out.get("duration_ms") or 0) / 1000, out.get("num_turns"), out.get("total_cost_usd") or 0,
             len(out.get("permission_denials") or []),
             [d.get("tool_name") for d in out.get("permission_denials") or []])
    ok = not out.get("is_error") and isinstance(out.get("structured_output"), dict)
    try:
        usage.record(root, int(nnn), out, ok)
    except OSError:
        log.exception("#%s usage not recorded", nnn)
    if not ok:
        log.error("#%s claude error: %s", nnn, str(out.get("result") or out.get("subtype"))[:2000])
        raise ProcessError("Claude failed: %s" % str(out.get("result") or out.get("subtype"))[:300])
    return out["structured_output"]


def ordered_specs(data):
    """[(name, value, score)] for every spec of the domain, in order; anything Claude left out counts as not shown."""
    got = {s.get("name"): s for s in data.get("specs") or []}
    out = []
    for name in DOMAIN.specs:
        s = got.get(name) or {}
        value = (s.get("value") or "").strip() or "not shown"
        score = s.get("score") if value.lower() != "not shown" else None
        out.append((name, value, score))
    return out


def specs_avg(data):
    scores = [s for _, _, s in ordered_specs(data) if isinstance(s, (int, float))]
    return round(sum(scores) / len(scores), 1) if scores else None


WEIGHT = {"required": 1.0, "big plus": 1.0, "nice to have": 0.5}


def score(checks):
    """0-10 from the criteria checks. Met = 1, unknown = 0.5, missed = 0, each times its weight (nice-to-haves count
    half); a missed deal breaker makes it 0. Returns None when there are no criteria to score against."""
    if not checks:
        return None
    if any(c["deal_breaker"] and c["result"] == "missed" for c in checks):
        return 0.0
    weight = lambda c: WEIGHT.get(c.get("importance"), 1.0)  # noqa: E731
    points = sum(weight(c) * {"met": 1.0, "unknown": 0.5}.get(c["result"], 0.0) for c in checks)
    return round(10 * points / sum(weight(c) for c in checks), 1)


def clean(criterion):
    """'Deal breaker: rust' -> 'rust' (the label is added when displayed)."""
    head, sep, rest = criterion.partition(":")
    return rest.strip() if sep and head.strip().lower() in ("deal breaker", "deal breakers") else criterion


def csv_row(n, data, old=None, link=""):
    old = old or {}
    checks = data.get("criteria_checks") or []
    count = lambda r: sum(1 for c in checks if c["result"] == r)  # noqa: E731
    s = score(checks)
    avg = specs_avg(data)
    row = {
        "number": n,
        "title": data.get("title", ""),
        "seller": data.get("seller_name") or "",
        "source": data.get("source") or "",
        "location": data.get("location") or "",
        "price_net": "yes" if data.get("price_net") else "",
        "fair_price_low": data.get("fair_price_low") or "",
        "fair_price_high": data.get("fair_price_high") or "",
        "link": link,
        "price_pln": data.get("price_pln") if data.get("price_pln") is not None else "",
        "model": data.get("model") or "",
        "year": data.get("year") or "",
        "mileage_km": data.get("mileage_km") if data.get("mileage_km") is not None else "",
        "score": "" if s is None else s,
        "specs_avg": "" if avg is None else avg,
        "met": count("met"),
        "missed": count("missed"),
        "unknown": count("unknown"),
        "deal_breakers_hit": "; ".join(clean(c["criterion"]) for c in checks
                                       if c["deal_breaker"] and c["result"] == "missed"),
        "missing": "; ".join(data.get("missing") or []),
        "red_flags": "; ".join(data.get("red_flags") or []),
        "verdict": data.get("verdict", ""),
        "status": old.get("status") or "new",
        "first_seen": old.get("first_seen") or time.strftime("%Y-%m-%d"),
        "processed": time.strftime("%Y-%m-%d %H:%M"),
    }
    for key in DOMAIN.categories:
        row[key] = data.get(key) or ""
    for name, value, sc in ordered_specs(data):
        row[name] = value if sc is None else "%s (%d/10)" % (value, sc)
    return row


def read_rows(path):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = {int(r["number"]): r for r in csv.DictReader(f) if r.get("number", "").isdigit()}
    for r in rows.values():  # files written before the "model" column was generalised from "base_vehicle"
        if not r.get("model") and r.get("base_vehicle"):
            r["model"] = r["base_vehicle"]
    return rows


def write_rows(path, rows):
    """utf-8-sig so Excel shows Polish letters correctly; Numbers and Google Sheets open it too."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_fields(), extrasaction="ignore")
        w.writeheader()
        for n in sorted(rows):
            w.writerow(rows[n])


def sort_key(row):
    s = row.get("score")
    return (s in ("", None), -float(s) if s not in ("", None) else 0, int(row["number"]))


def write_index(path, rows):
    lines = ["# Listings (generated by bot/process.py from listings.csv; edit status in the CSV)", "",
             "| # | Title | Price PLN | Model | Year | km | Score | Status | Verdict |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows.values(), key=sort_key):
        cells = [r["number"], r["title"], r["price_pln"] or "?", r["model"] or "?", r["year"] or "?",
                 r["mileage_km"] or "?", "-" if r["score"] in ("", None) else r["score"], r["status"], r["verdict"]]
        lines.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fmt_int(v):
    return "{:,}".format(int(v)).replace(",", " ")


def same_seller(n, rows):
    """Other listings from a seller with the same name: a hint of a dealer posing as a private seller, or a repost."""
    name = " ".join(str(rows[n].get("seller") or "").lower().split())
    if not name:
        return []
    return [m for m in sorted(rows) if m != n and " ".join(str(rows[m].get("seller") or "").lower().split()) == name]


def normalise_phone(raw):
    """'+48 600-123-456', '0048 600 123 456' or '600123456' -> '48600123456' (the wa.me format), else None."""
    text = str(raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if digits.startswith("00"):
        digits = digits[2:]
    elif len(digits) == 9 and not text.startswith("+"):
        digits = "48" + digits  # a Polish number written without the country code
    return digits if 10 <= len(digits) <= 15 else None


def whatsapp_url(phone, text):
    return "https://wa.me/%s?text=%s" % (phone, urllib.parse.quote(text, safe=""))


def contact_messages(data, phone):
    """The texts for the seller, each a separate Telegram message so it can be copied whole: the full message (with a
    WhatsApp button when the number is known), then the short SMS version. The number lives only in the button."""
    out = []
    full = (data.get("seller_message") or "").strip()
    if full:
        msg = {"text": full[:3900]}
        if phone:
            msg["buttons"] = [("Send on WhatsApp", whatsapp_url(phone, full))]
            msg["fallback_url"] = "https://wa.me/%s" % phone
        out.append(msg)
    sms = (data.get("seller_sms") or "").strip()
    if sms:
        # Telegram buttons can't open sms: links, so: the text in monospace (one tap copies it) and the number
        # written out (Telegram makes it tappable: call / SMS).
        how = ("Tap the text to copy it, then tap the number and choose SMS." if phone
               else "Tap the text to copy it. No phone number on the screenshots.")
        text = "<code>%s</code>\n\n%s%s" % (esc(sms[:3500]), "📱 %s\n" % pretty_phone(phone) if phone else "", how)
        out.append({"text": text, "html": True})
    return out


def pretty_phone(phone):
    """'48600123456' -> '+48 600 123 456' (a form Telegram recognises as a phone number)."""
    if phone.startswith("48") and len(phone) == 11:
        return "+48 %s %s %s" % (phone[2:5], phone[5:8], phone[8:])
    return "+" + phone


def contact_line(n, data, phone):
    pref = data.get("contact_preference") or "not stated"
    how = "listing asks for %s" % pref if pref != "not stated" else "no contact preference"
    if phone:
        return "💬 %s · tap below for the message to the seller" % how
    return "💬 %s · tap below for the message; no phone on the screenshots (/phone %d 600123456 adds one)" % (how, n)


def summary_buttons(n, data, phone):
    """Buttons under the summary. The message buttons are taps the bot answers (callback data); the phone number
    rides along inside the button, so it is never written to a file."""
    buttons = []
    if (data.get("seller_message") or "").strip():
        buttons.append(("💬 WhatsApp message", "msg:%d:%s" % (n, phone or "")))
    if (data.get("seller_sms") or "").strip():
        buttons.append(("✉️ SMS", "sms:%d:%s" % (n, phone or "")))
    if sheet.link():
        buttons.append(("📊 Open sheet", sheet.link()))
    return buttons


def seller_text(root, n, kind, phone=None):
    """What a message button sends: the full message (with the WhatsApp link when the number is known) or the SMS."""
    path = Path(root) / "listings" / ("%03d.json" % n)
    if not path.exists():
        raise ProcessError("#%d has not been analysed yet" % n)
    items = contact_messages(json.loads(path.read_text(encoding="utf-8")), normalise_phone(phone))
    if kind == "sms":
        items = [i for i in items if i.get("html")]
    else:
        items = items[:1]
    if not items:
        raise ProcessError("#%d has no %s draft; re-analyse it with /done %d" % (n, kind.upper(), n))
    return items


def esc(text):
    return html.escape(str(text), quote=False)


def dot(score):
    if score is None:
        return "⚪"
    return "🟢" if score >= 7 else "🟡" if score >= 4 else "🔴"


def summary_text(n, data, row, others=(), phone=None):
    """Telegram HTML: header, the fixed spec list (one line each, with a score), then flags and contact."""
    price = "<b>%s PLN</b>" % fmt_int(data["price_pln"]) if data.get("price_pln") is not None else "price not shown"
    where = [price] + [esc(data[k]) for k in ("location", "source") if data.get(k)]
    head = ["<b>#%d %s</b>" % (n, esc(data.get("title") or "listing")), " · ".join(where)]
    if data.get("seller_name"):
        head.append("Seller: %s%s" % (esc(data["seller_name"]),
                                      " (also #%s)" % ", #".join(map(str, others)) if others else ""))
    sc = "<b>%s/10</b>" % row["score"] if row["score"] != "" else "– (fill in criteria.md)"
    avg = row["specs_avg"]
    head.append("Score %s · specs <b>%s</b> · <b>%s</b>" % (sc, "%s/10" % avg if avg != "" else "–",
                                                          esc(data.get("verdict", "?"))))
    if data.get("summary"):
        head.append("<i>%s</i>" % esc(data["summary"]))

    specs = []
    for name, value, s in ordered_specs(data):
        if s is None:
            specs.append("%s %s: <i>%s</i>" % (dot(s), esc(name), esc(value)))
        else:
            specs.append("%s %s: %s · %d/10" % (dot(s), esc(name), esc(value), s))

    tail = []
    if row["deal_breakers_hit"]:
        tail.append("⛔ Deal breaker: " + esc(row["deal_breakers_hit"]))
    if data.get("red_flags"):
        tail.append("🚩 " + esc(" · ".join(data["red_flags"][:3])))
    if data.get("seller_message"):
        tail.append(contact_line(n, data, phone))
    tail.append("📄 listings/%03d.md" % n)
    return "\n".join(head) + "\n\n" + "\n".join(specs) + "\n\n" + "\n".join(tail)


def process_listing(root, n, model=None, runner=subprocess.run, pusher=sheet.push):
    root = Path(root)
    nnn = "%03d" % n
    if not (root / "inbox" / nnn).is_dir():
        raise ProcessError("inbox/%s does not exist" % nnn)
    listings = root / "listings"
    listings.mkdir(exist_ok=True)
    data = run_claude(root, nnn, model or os.environ.get("CLAUDE_MODEL") or "opus", runner=runner)
    phone = normalise_phone(data.pop("seller_phone", None))  # used for the button only, never saved
    (listings / (nnn + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = read_rows(listings / "listings.csv")
    links = root / "inbox" / nnn / "links.txt"
    link = links.read_text(encoding="utf-8").split()[0] if links.exists() and links.read_text().split() else ""
    rows[n] = csv_row(n, data, old=rows.get(n), link=link)
    write_rows(listings / "listings.csv", rows)
    write_index(listings / "INDEX.md", rows)
    try:
        market.write(listings)
    except Exception:  # statistics are a bonus; never lose an analysis over them
        log.exception("#%s market.md not rebuilt", nnn)
    summary = summary_text(n, data, rows[n], same_seller(n, rows), phone)
    if sheet.enabled():
        try:
            pusher(rows[n], csv_fields())
            log.info("#%s copied to the Google Sheet", nnn)
        except Exception as exc:  # the CSV is saved either way; say so and carry on
            log.error("#%s Google Sheet not updated: %s", nnn, exc)
            summary += "\n⚠️ Google Sheet not updated (%s). Retry: python3 bot/sheet.py" % esc(exc)
    item = {"text": summary[:4000], "html": True}
    buttons = summary_buttons(n, data, phone)
    if buttons:
        item["buttons"] = buttons
    return [item]


def contact_for(root, n, raw_phone):
    """/phone 7 600 123 456: the seller messages of an analysed listing, with a WhatsApp button for that number."""
    path = Path(root) / "listings" / ("%03d.json" % n)
    if not path.exists():
        raise ProcessError("#%d has not been analysed yet; send /done %d first" % (n, n))
    phone = normalise_phone(raw_phone)
    if not phone:
        raise ProcessError("%r doesn't look like a phone number" % raw_phone)
    return contact_messages(json.loads(path.read_text(encoding="utf-8")), phone)


def main(argv):
    if len(argv) != 2 or not argv[1].isdigit():
        sys.exit("usage: python3 bot/process.py <listing number>")
    from bot import load_env  # here, not at the top: bot.py imports this module
    load_env(ROOT / ".env")
    root = Path(os.environ.get("CAMPERVAN_ROOT", ROOT))
    if root.resolve() != ROOT.resolve() and os.environ.get("GOOGLE_SHEET_URL"):
        # a trial run on another folder must never write its rows into the real sheet
        os.environ["GOOGLE_SHEET_URL"] = ""
        print("(CAMPERVAN_ROOT is another folder: not copying to the Google Sheet)", file=sys.stderr)
    try:
        out = process_listing(root, int(argv[1]))
        plain = lambda t: html.unescape(re.sub(r"</?[biu]>", "", t))  # noqa: E731
        print("\n\n-----\n".join(plain(m["text"]) + "".join("\n[%s: %s]" % b for b in m.get("buttons", []))
                                   for m in out))
    except ProcessError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main(sys.argv)
