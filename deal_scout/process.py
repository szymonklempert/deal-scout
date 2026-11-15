"""Analyse one saved listing and update the cards, the CSV, the index and the spreadsheet.

    python3 -m deal_scout.process 7       # analyse inbox/007/

This module only ties one listing's analysis together: analysis.py asks the model (Claude Code by default, or an API
provider), scoring.py and storage.py turn its checks into the score, the CSV row and INDEX.md, render.py formats the
Telegram summary, contact.py handles the seller's phone number and message drafts, and market.py and sheet.py update
the market prices and the Google Sheet. Nothing here is specific to what is being bought: that comes from the domain
(deal_scout/domain.py). Standard library only.
"""
import dataclasses
import functools
import json
import logging
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from deal_scout import domain as domains
from deal_scout import market, sheet
from deal_scout.analysis import analyse
from deal_scout.config import ROOT, Settings, load_env
from deal_scout.contact import normalise_phone, scrub_phone
from deal_scout.domain import Domain
from deal_scout.errors import ProcessError
from deal_scout.render import Message, esc, plain_text, summary_buttons, summary_text
from deal_scout.storage import csv_fields, csv_row, read_rows, same_seller, write_index, write_rows

log = logging.getLogger("dealscout")
log.addHandler(logging.NullHandler())  # quiet unless setup_logging() adds real handlers


def process_listing(root: Path | str, n: int, *, settings: Settings, domain: Domain, model: str | None = None,
                    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                    pusher: Callable[..., None] | None = None, client: Any = None) -> list[Message]:
    """Analyse inbox listing n and update its card, the CSV, the index, the market page and the sheet."""
    root = Path(root)
    nnn = f"{n:03d}"
    if not (root / "inbox" / nnn).is_dir():
        raise ProcessError(f"inbox/{nnn} does not exist")
    listings = root / "listings"
    listings.mkdir(exist_ok=True)
    if model:  # an explicit model (tests, a one-off run) wins over .env
        settings = dataclasses.replace(settings, model=model)
    if pusher is None:
        pusher = functools.partial(sheet.push, settings=settings)
    data = analyse(root, nnn, domain, settings, runner=runner, client=client)
    raw_phone = data.pop("seller_phone", None)
    phone = normalise_phone(raw_phone)  # used for the button only, never saved
    scrub_phone(listings / (nnn + ".md"), raw_phone)
    (listings / (nnn + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = read_rows(listings / "listings.csv")
    links = root / "inbox" / nnn / "links.txt"
    link = links.read_text(encoding="utf-8").split()[0] if links.exists() and links.read_text().split() else ""
    rows[n] = csv_row(n, data, domain, old=rows.get(n), link=link)
    write_rows(listings / "listings.csv", rows, csv_fields(domain))
    write_index(listings / "INDEX.md", rows)
    try:
        market.write(listings, domain.market)
    except Exception:  # statistics are a bonus; never lose an analysis over them
        log.exception("#%s market.md not rebuilt", nnn)
    summary = summary_text(n, data, rows[n], domain.specs, same_seller(n, rows), phone)
    if sheet.enabled(settings):
        try:
            pusher(rows[n], csv_fields(domain))
            log.info("#%s copied to the Google Sheet", nnn)
        except Exception as exc:  # the CSV is saved either way; say so and carry on
            log.error("#%s Google Sheet not updated: %s", nnn, exc)
            summary += f"\n⚠️ Google Sheet not updated ({esc(exc)}). Retry: python3 bot/sheet.py"
    buttons = summary_buttons(n, data, phone, sheet.link(settings))
    return [Message(text=summary[:4000], html=True, buttons=tuple(buttons))]


def main(argv: Sequence[str]) -> None:
    """Run the analysis of one listing from the command line and print the summary."""
    if len(argv) != 2 or not argv[1].isdigit():
        sys.exit("usage: python3 bot/process.py <listing number>")
    load_env(ROOT / ".env")
    settings = Settings.from_env()
    root = Path(settings.data_root or ROOT)
    if root.resolve() != ROOT.resolve() and settings.sheet_url:
        # a trial run on another folder must never write its rows into the real sheet
        settings = dataclasses.replace(settings, sheet_url="")
        print("(CAMPERVAN_ROOT is another folder: not copying to the Google Sheet)", file=sys.stderr)
    try:
        d = domains.load(settings.domain)
    except domains.DomainError as exc:
        sys.exit(str(exc))
    try:
        out = process_listing(root, int(argv[1]), settings=settings, domain=d)
        print("\n\n-----\n".join(plain_text(m.text) + "".join(f"\n[{b[0]}: {b[1]}]" for b in m.buttons)
                                   for m in out))
    except ProcessError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main(sys.argv)
