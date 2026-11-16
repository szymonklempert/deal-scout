"""Read the stored listings and answer /show, /top and /compare: the I/O around reports.py's pure builders."""

from collections.abc import Sequence
from pathlib import Path

from deal_scout import reports, storage
from deal_scout.config import Settings
from deal_scout.domain import Domain
from deal_scout.errors import ProcessError
from deal_scout.feedback import apply_overrides
from deal_scout.process import read_listing, stored_summary
from deal_scout.render import Message
from deal_scout.reports import Entry
from deal_scout.storage import read_rows


def load_entries(root: Path, domain: Domain) -> dict[int, Entry]:
    """Every listing in listings.csv as an Entry, with the owner's corrections applied to its spec scores."""
    listings = root / "listings"
    with storage.ROWS_LOCK:  # an analysis or a correction must not rewrite the files while they are read
        rows = read_rows(listings / "listings.csv")
        stored = {n: read_listing(listings / f"{n:03d}.json") for n in rows}
    out: dict[int, Entry] = {}
    for n, row in rows.items():
        data = stored[n]
        shown = apply_overrides(data, data.get("overrides") or {}) if data else None
        out[n] = reports.make_entry(n, row, shown, domain.specs, data)
    return out


def show(root: Path, args: Sequence[str], *, settings: Settings, domain: Domain) -> list[Message]:
    """/show 7: the summary of an analysed listing, rebuilt from the stored data."""
    numbers = reports.parse_numbers(args[:1])
    if not numbers:
        raise ProcessError("Usage: /show 7")
    with storage.ROWS_LOCK:
        return [stored_summary(root, numbers[0], domain, settings, stored=True)]


def top(root: Path, args: Sequence[str], *, domain: Domain) -> list[Message]:
    """/top or /top 10: the best open listings."""
    return [reports.top_message(list(load_entries(root, domain).values()), reports.parse_limit(args))]


def compare(root: Path, args: Sequence[str], *, domain: Domain) -> list[Message]:
    """/compare 7 12 15: a side-by-side table of 2 to 4 analysed listings."""
    numbers = reports.parse_numbers(args)
    if problem := reports.compare_problem(numbers):
        raise ProcessError(problem)
    entries = load_entries(root, domain)
    unknown = [n for n in numbers if n not in entries]
    if unknown:
        raise ProcessError("Not analysed yet: " + ", ".join(f"#{n}" for n in unknown))
    return [reports.compare_message([entries[n] for n in numbers], domain.specs)]


def reports_for(root: Path, cmd: str, args: Sequence[str], *, settings: Settings, domain: Domain) -> list[Message]:
    """Answer /show, /top or /compare (the bot's one entry point for them); unknown commands raise a ProcessError."""
    if cmd == "/show":
        return show(root, args, settings=settings, domain=domain)
    if cmd == "/top":
        return top(root, args, domain=domain)
    if cmd == "/compare":
        return compare(root, args, domain=domain)
    raise ProcessError(f"unknown report {cmd}")
