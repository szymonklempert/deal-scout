"""Apply the owner's Feedback taps to a listing's stored data: corrections, reasons, and the refreshed summary.

corrections.fix() is the one entry point: it turns a tap (see feedback_menu.py for the callback data) into a FixReply.
A correction is stored in listings/NNN.json under "overrides", and the CSV row, index, market page and sheet are
recomputed from it. Standard library only.
"""
import json
import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from deal_scout import sheet, storage
from deal_scout.config import Settings
from deal_scout.contact import normalise_phone
from deal_scout.domain import Domain
from deal_scout.errors import ProcessError
from deal_scout.feedback import Overrides, apply_overrides, log_feedback, override_count, score_word, scrub_numbers
from deal_scout.feedback_menu import FixReply, check_screen, checks_screen, confirm, menu, spec_screen
from deal_scout.process import load_listing, refresh_market, sheet_note, summary_message
from deal_scout.prompts import Listing
from deal_scout.render import Message, score_text
from deal_scout.scoring import ordered_specs
from deal_scout.storage import csv_fields, csv_row, read_rows, write_index, write_rows

log = logging.getLogger("dealscout")

MARKS = {"m": "met", "x": "missed", "u": "unknown"}


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
        phone = normalise_phone(args[0]) if args else None  # the Feedback button carried it; never stored
        return FixReply(done, edit=True,
                        extra=(summary_message(n, shown, rows, domain, phone, sheet.link(settings)),))
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
    column = "specs_avg" if kind == "spec" else "score"  # a spec correction leaves the criteria score alone
    return FixReply(confirm(n, name, score_word(old_value) if kind == "spec" else old_value,
                            score_word(new) if kind == "spec" else new, score_text(old_row.get(column)),
                            score_text(rows[n][column]), note, metric="specs" if kind == "spec" else "score"),
                    edit=True, ask_reason=name)


def before_value(before: Listing, kind: str, name: str) -> Any:
    """Return the value of a spec (its score) or check (its result) as the owner saw it before the correction."""
    if kind == "spec":
        return ordered_specs(before, [name])[0][2]
    return next(c["result"] for c in before["criteria_checks"] if c["criterion"] == name)
