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
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from deal_scout import domain as domains
from deal_scout import market, sheet, storage
from deal_scout.analysis import analyse
from deal_scout.config import ROOT, Settings, load_env
from deal_scout.contact import normalise_phone, scrub_phone
from deal_scout.domain import Domain
from deal_scout.errors import ProcessError
from deal_scout.feedback import (
    Overrides,
    apply_overrides,
    carry_overrides,
    log_feedback,
    override_count,
    score_word,
    scrub_numbers,
)
from deal_scout.fixmenu import FixReply, check_screen, checks_screen, confirm, menu, spec_screen
from deal_scout.prompts import Listing
from deal_scout.render import Message, esc, plain_text, summary_buttons, summary_text
from deal_scout.scoring import ordered_specs
from deal_scout.storage import Row, csv_fields, csv_row, read_rows, same_seller, write_index, write_rows

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
    with storage.ROWS_LOCK:  # a score correction in the polling thread must not interleave with this update
        overrides, dropped = carry_overrides(read_overrides(listings / (nnn + ".json")), data)
        if dropped:
            log.info("#%s: %d check correction(s) dropped, the criteria changed", nnn, dropped)
        if overrides:
            data["overrides"] = overrides
        (listings / (nnn + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        rows = read_rows(listings / "listings.csv")
        links = root / "inbox" / nnn / "links.txt"
        link = links.read_text(encoding="utf-8").split()[0] if links.exists() and links.read_text().split() else ""
        shown = apply_overrides(data, overrides)
        rows[n] = csv_row(n, shown, domain, old=rows.get(n), link=link)
        write_rows(listings / "listings.csv", rows, csv_fields(domain))
        write_index(listings / "INDEX.md", rows)
        refresh_market(listings, domain)
    note = sheet_note(rows[n], domain, settings, pusher)
    return [summary_message(n, shown, rows, domain, phone, sheet.link(settings), note)]


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


def summary_message(n: int, shown: Listing, rows: dict[int, Row], domain: Domain, phone: str | None,
                    sheet_link: str | None, note: str = "") -> Message:
    """Build the summary Telegram message of listing n, from its (overridden) data and its CSV row."""
    text = summary_text(n, shown, rows[n], domain.specs, same_seller(n, rows), phone) + note
    return Message(text=text[:4000], html=True, buttons=tuple(summary_buttons(n, shown, phone, sheet_link)))


FIX_ACTIONS = ("open", "back", "spec", "value", "checks", "check", "mark", "skip", "done", "reason")
MARKS = {"m": "met", "x": "missed", "u": "unknown"}


def load_listing(root: Path, n: int) -> Listing:
    """Read listings/NNN.json, or say the listing has not been analysed."""
    path = root / "listings" / f"{n:03d}.json"
    try:
        data: Listing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProcessError(f"#{n} has not been analysed yet") from exc
    return data


def pick(args: Sequence[str], k: int, limit: int) -> int:
    """Argument k of a button tap as an index below limit; an old or broken button raises a ProcessError."""
    try:
        value = int(args[k])
    except (IndexError, ValueError):
        value = -1
    if not 0 <= value < limit:
        raise ProcessError("That button is out of date. Tap 📝 Feedback on the summary again.")
    return value


def fix(root: Path | str, n: int, action: str, args: Sequence[str], *, settings: Settings, domain: Domain,
        pusher: Callable[..., None] | None = None) -> FixReply:
    """Handle one tap in the 📝 Feedback menu of listing n and say what to show next."""
    root = Path(root)
    data = load_listing(root, n)
    overrides: Overrides = data.get("overrides") or {}
    shown = apply_overrides(data, overrides)
    specs, checks = domain.specs, shown.get("criteria_checks") or []
    if action in ("open", "back", "skip"):
        return FixReply(menu(n, shown, specs), edit=action != "open")
    if action == "spec":
        return FixReply(spec_screen(n, shown, specs, pick(args, 0, len(specs))), edit=True)
    if action == "checks":
        return FixReply(checks_screen(n, shown), edit=True)
    if action == "check":
        return FixReply(check_screen(n, shown, pick(args, 0, len(checks))), edit=True)
    if action == "value":
        i = pick(args, 0, len(specs))
        value = None if args[1:] == ["x"] else pick(args, 1, 11)
        return correct(root, n, "spec", specs[i], value, settings=settings, domain=domain, pusher=pusher)
    if action == "mark":
        j = pick(args, 0, len(checks))
        result = MARKS.get(args[1]) if len(args) > 1 else None
        if result is None:
            pick([], 0, 0)  # an unknown result letter is an out-of-date button too
        return correct(root, n, "check", checks[j]["criterion"], result, settings=settings, domain=domain,
                       pusher=pusher)
    if action == "done":
        count = override_count(overrides)
        done = Message(f"📝 #{n}: {count} correction(s) saved.", html=True)
        rows = read_rows(root / "listings" / "listings.csv")
        if n not in rows:
            raise ProcessError(f"#{n} is not in listings.csv; re-analyse it with /done {n}")
        return FixReply(done, edit=True, extra=(summary_message(n, shown, rows, domain, None, sheet.link(settings)),))
    if action == "reason":
        return log_reason(root, n, domain, args)
    raise ProcessError(f"unknown action {action}")


def log_reason(root: Path, n: int, domain: Domain, args: Sequence[str]) -> FixReply:
    """Record the owner's reason for the last correction (numbers scrubbed) and acknowledge it."""
    if len(args) < 2:
        raise ProcessError("no reason given")
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "listing": n, "kind": "reason", "name": args[0],
              "reason": scrub_numbers(args[1])}
    with storage.ROWS_LOCK:
        log_feedback(root / "listings" / "feedback.jsonl", record)
    return FixReply(extra=(Message("Noted."),))


def correct(root: Path, n: int, kind: str, name: str, new: Any, *, settings: Settings, domain: Domain,
            pusher: Callable[..., None] | None) -> FixReply:
    """Store one correction, recompute the CSV row, index, market page and sheet, and ask for a reason."""
    listings = root / "listings"
    with storage.ROWS_LOCK:
        data = load_listing(root, n)
        rows = read_rows(listings / "listings.csv")
        if n not in rows:
            raise ProcessError(f"#{n} is not in listings.csv; re-analyse it with /done {n}")
        overrides: Overrides = data.get("overrides") or {}
        before = apply_overrides(data, overrides)
        model = (ordered_specs(data, [name])[0][2] if kind == "spec"
                 else next(c["result"] for c in data["criteria_checks"] if c["criterion"] == name))
        group = overrides.setdefault("specs" if kind == "spec" else "checks", {})
        if new == model:
            group.pop(name, None)  # back to what the model said: no correction left
        else:
            group[name] = new
        data["overrides"] = {k: v for k, v in overrides.items() if v}
        if not data["overrides"]:
            del data["overrides"]
        (listings / f"{n:03d}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        shown = apply_overrides(data, data.get("overrides") or {})
        old_row = rows[n]
        rows[n] = csv_row(n, shown, domain, old=old_row, link=old_row.get("link", ""))
        rows[n]["processed"] = old_row.get("processed", rows[n]["processed"])  # a correction is not a new analysis
        write_rows(listings / "listings.csv", rows, csv_fields(domain))
        write_index(listings / "INDEX.md", rows)
        refresh_market(listings, domain)
        log_feedback(listings / "feedback.jsonl", {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "listing": n, "kind": kind, "name": name, "model": model,
            "owner": new, "domain": domain.name})
    log.info("#%d: correction to a %s", n, kind)
    note = sheet_note(rows[n], domain, settings, pusher)
    old_value = before_value(before, kind, name)
    return FixReply(confirm(n, name, score_word(old_value) if kind == "spec" else old_value,
                            score_word(new) if kind == "spec" else new, score_text(old_row.get("score")),
                            score_text(rows[n]["score"]), note), edit=True, ask_reason=name)


def before_value(before: Listing, kind: str, name: str) -> Any:
    """Return the value of a spec (its score) or check (its result) as the owner saw it before the correction."""
    if kind == "spec":
        return ordered_specs(before, [name])[0][2]
    return next(c["result"] for c in before["criteria_checks"] if c["criterion"] == name)


def score_text(score: object) -> str:
    """Format a row's score for display: the number, or an en dash when there is none."""
    return "–" if score in ("", None) else str(score)


def main(argv: Sequence[str]) -> None:
    """Run the analysis of one listing from the command line and print the summary."""
    if len(argv) != 2 or not argv[1].isdigit():
        sys.exit("usage: python3 -m deal_scout.process <listing number>")
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
