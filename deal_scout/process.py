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
from deal_scout import market, sheet, storage
from deal_scout.analysis import analyse
from deal_scout.config import Settings, is_trial_home, load_settings
from deal_scout.contact import normalise_phone, scrub_phone
from deal_scout.domain import Domain
from deal_scout.errors import ProcessError
from deal_scout.feedback import Overrides, apply_overrides, carry_overrides
from deal_scout.inbox import reply_counts, viewing_files
from deal_scout.prompts import Listing
from deal_scout.render import Message, esc, plain_text, summary_buttons, summary_text
from deal_scout.replies import reply_note
from deal_scout.reports import repost_line
from deal_scout.storage import Row, csv_fields, csv_row, read_rows, same_seller, write_index, write_rows
from deal_scout.viewing_menu import viewing_note

log = logging.getLogger("dealscout")
log.addHandler(logging.NullHandler())  # quiet unless setup_logging() adds real handlers


def process_listing(
    root: Path | str,
    n: int,
    *,
    settings: Settings,
    domain: Domain,
    model: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    pusher: Callable[..., None] | None = None,
    client: Any = None,
) -> list[Message]:
    """Analyse inbox listing n and update its card, the CSV, the index, the market page and the sheet."""
    root = Path(root)
    nnn = f"{n:03d}"
    if not (root / "inbox" / nnn).is_dir():
        raise ProcessError(f"inbox/{nnn} does not exist")
    if model:  # an explicit model (tests, a one-off run) wins over .env
        settings = dataclasses.replace(settings, model=model)
    data = analyse(root, nnn, domain, settings, runner=runner, client=client)
    return apply_review(root, n, data, settings=settings, domain=domain, pusher=pusher)


def apply_review(
    root: Path | str,
    n: int,
    data: Listing,
    *,
    settings: Settings,
    domain: Domain,
    pusher: Callable[..., None] | None = None,
) -> list[Message]:
    """Turn a structured review of listing n into stored state (card, JSON, CSV, index, market, sheet) and its summary.

    The one place where a review, whoever produced it, becomes stored state. A non-empty `card_markdown` in data is
    written as the card; without it the card is already on disk (Claude Code writes it itself).
    """
    root = Path(root)
    nnn = f"{n:03d}"
    listings = root / "listings"
    listings.mkdir(exist_ok=True)
    if pusher is None:
        pusher = functools.partial(sheet.push, settings=settings)
    card = (data.pop("card_markdown", "") or "").strip()
    if card:
        (listings / (nnn + ".md")).write_text(card + "\n", encoding="utf-8")
    raw_phone = data.pop("seller_phone", None)
    phone = normalise_phone(raw_phone)  # used for the button only, never saved
    scrub_phone(listings / (nnn + ".md"), raw_phone)
    seen = sum(reply_counts(root / "inbox" / nnn))
    if seen:
        data["replies_seen"] = seen  # lets the next analysis tell whether the seller has replied since
    if viewed := viewing_files(root / "inbox" / nnn):
        data["viewing_seen"] = viewed  # the same for the owner's findings at a viewing
    with storage.ROWS_LOCK:  # a score correction in the polling thread must not interleave with this update
        previous = read_listing(listings / (nnn + ".json"))
        overrides, dropped = carry_overrides(read_overrides(listings / (nnn + ".json")), data)
        if dropped:
            log.info("#%s: %d check correction(s) dropped, the criteria changed", nnn, dropped)
        if overrides:
            data["overrides"] = overrides
        if previous and previous.get("contacted_at"):
            data["contacted_at"] = previous["contacted_at"]  # a re-analysis does not forget the seller was contacted
        if previous and previous.get("viewing"):
            data["viewing"] = previous["viewing"]  # nor the checklist and the owner's marks
        (listings / (nnn + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        rows = read_rows(listings / "listings.csv")
        links = root / "inbox" / nnn / "links.txt"
        link = links.read_text(encoding="utf-8").split()[0] if links.exists() and links.read_text().split() else ""
        shown = apply_overrides(data, overrides)
        old_score = (rows.get(n) or {}).get("score")
        rows[n] = csv_row(n, shown, domain, old=rows.get(n), link=link)
        write_rows(listings / "listings.csv", rows, csv_fields(domain))
        write_index(listings / "INDEX.md", rows)
        refresh_market(listings, domain)
    note = sheet_note(rows[n], domain, settings, pusher)
    notes = (reply_note, viewing_note)
    changed = "\n".join(x for x in (f(previous, data, old_score, rows[n]["score"]) for f in notes) if x)
    return [summary_message(n, shown, rows, domain, phone, sheet.link(settings), note, changed)]


def read_listing(path: Path) -> Listing | None:
    """Read listings/NNN.json as it was before this analysis; None when there is no readable earlier one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_overrides(path: Path) -> Overrides:
    """Read the owner's corrections stored in listings/NNN.json; empty when there is no file or none."""
    try:
        overrides = json.loads(path.read_text(encoding="utf-8")).get("overrides")
    except (OSError, ValueError):
        return {}
    return overrides if isinstance(overrides, dict) else {}


def refresh_market(listings: Path, domain: Domain) -> None:
    """Rebuild market.md; statistics are a bonus, so never lose an analysis or a correction over them."""
    try:
        market.write(listings, domain.market)
    except Exception:
        log.exception("market.md not rebuilt")


def sheet_note(row: Row, domain: Domain, settings: Settings, pusher: Callable[..., None] | None) -> str:
    """Copy the row to the Google Sheet if it is set up; return a warning line when that failed, else ''."""
    if not sheet.enabled(settings):
        return ""
    push = pusher or functools.partial(sheet.push, settings=settings)
    try:
        push(row, csv_fields(domain))
        log.info("#%s copied to the Google Sheet", row["number"])
    except Exception as exc:  # the CSV is saved either way; say so and carry on
        log.error("#%s Google Sheet not updated: %s", row["number"], exc)
        return f"\n⚠️ Google Sheet not updated ({esc(exc)}). Retry: python3 -m deal_scout.sheet"
    return ""


def summary_message(
    n: int,
    shown: Listing,
    rows: dict[int, Row],
    domain: Domain,
    phone: str | None,
    sheet_link: str | None,
    note: str = "",
    reply_note: str = "",
    stored: bool = False,
) -> Message:
    """Build the summary Telegram message of listing n, from its (overridden) data and its CSV row."""
    repost = repost_line(n, rows, domain.market.get("same_item_key"), domain.unit)
    text = summary_text(n, shown, rows[n], domain.specs, same_seller(n, rows), phone, reply_note, stored, repost) + note
    return Message(text=text[:4000], html=True, buttons=tuple(summary_buttons(n, shown, phone, sheet_link)))


def stored_summary(
    root: Path, n: int, domain: Domain, settings: Settings, phone: str | None = None, stored: bool = False
) -> Message:
    """Rebuild listing n's summary from listings/NNN.json (the owner's corrections applied) and its CSV row.

    Used where nothing was just analysed: after Feedback "Done" (phone: the number carried by the button) and /show
    (stored=True: the phone is unknown, and the contact line says how to add it).
    """
    data = load_listing(root, n)
    rows = read_rows(root / "listings" / "listings.csv")
    if n not in rows:
        raise ProcessError(f"#{n} is not in listings.csv; re-analyse it with /done {n}")
    shown = apply_overrides(data, data.get("overrides") or {})
    return summary_message(n, shown, rows, domain, phone, sheet.link(settings), stored=stored)


def load_listing(root: Path, n: int) -> Listing:
    """Read listings/NNN.json, or say the listing has not been analysed."""
    path = root / "listings" / f"{n:03d}.json"
    try:
        data: Listing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProcessError(f"#{n} has not been analysed yet") from exc
    return data


def main(argv: Sequence[str]) -> None:
    """Run the analysis of one listing from the command line and print the summary."""
    if len(argv) != 2 or not argv[1].isdigit():
        sys.exit("usage: python3 -m deal_scout.process <listing number>")
    root, settings = load_settings()
    if is_trial_home() and settings.sheet_url:
        # a trial run on another folder must never write its rows into the real sheet
        settings = dataclasses.replace(settings, sheet_url="")
        print("(CAMPERVAN_ROOT is another folder: not copying to the Google Sheet)", file=sys.stderr)
    try:
        d = domains.load(settings.domain, roots=[root])
    except domains.DomainError as exc:
        sys.exit(str(exc))
    try:
        out = process_listing(root, int(argv[1]), settings=settings, domain=d)
        print("\n\n-----\n".join(plain_text(m.text) + "".join(f"\n[{b[0]}: {b[1]}]" for b in m.buttons) for m in out))
    except ProcessError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main(sys.argv)
