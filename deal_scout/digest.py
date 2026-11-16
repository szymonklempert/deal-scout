"""The morning digest: what the owner should look at today, as one Telegram message. Pure.

library.py reads the files and keeps the market snapshot; this module only decides what to say. A section with
nothing to say is left out, and with no section at all there is no message.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from deal_scout.market import k
from deal_scout.render import Message, esc
from deal_scout.reports import Entry, rank_open, ranking_line, short, show_link

SECTION_LINES = 5
TOP_LINES = 3
WAITING_DAYS = 3
MARKET_MOVE = 0.03
MIN_ITEMS = 4  # a group needs this many items in both snapshots before its move counts
TIME_RE = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
DATE_FORMAT = "%Y-%m-%d"
Medians = Mapping[str, tuple[float, int]]


def parse_time(text: str) -> tuple[int, int] | None:
    """Parse DIGEST_TIME ('09:00') as (hour, minute); None for an unusable value."""
    m = TIME_RE.fullmatch(text.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def is_off(text: str) -> bool:
    """Whether DIGEST_TIME switches the digest off on purpose (empty or 'off')."""
    return text.strip().lower() in ("", "off")


def due(now: datetime, at: tuple[int, int], last_sent: str | None) -> bool:
    """Whether the day's digest should go out now: the time has come and none was sent today."""
    return (now.hour, now.minute) >= at and last_sent != now.strftime(DATE_FORMAT)


def parse_stamp(text: str) -> datetime | None:
    """Parse the 'YYYY-MM-DD HH:MM' stamps the bot writes; None for anything else."""
    try:
        return datetime.strptime(text, f"{DATE_FORMAT} %H:%M")
    except ValueError:
        return None


def waiting_lines(entries: Sequence[Entry], now: datetime) -> list[str]:
    """Open listings whose seller was contacted at least WAITING_DAYS ago and has not replied, longest wait first."""
    waiting: list[tuple[datetime, Entry]] = []
    for e in entries:
        stamp = parse_stamp(e.contacted_at)
        if e.is_open and stamp and not e.replies_seen and now - stamp >= timedelta(days=WAITING_DAYS):
            waiting.append((stamp, e))
    waiting.sort(key=lambda pair: pair[0])
    return [
        f"#{e.n} {esc(short(e.title, 30))} · contacted {(now - stamp).days} days ago · {show_link(e.n)}"
        for stamp, e in waiting[:SECTION_LINES]
    ]


def top_lines(entries: Sequence[Entry]) -> list[str]:
    """Return the best open listings, in /top's own format."""
    return [ranking_line(i, e) for i, e in enumerate(rank_open(entries)[:TOP_LINES], start=1)]


def new_count(entries: Sequence[Entry], now: datetime) -> int:
    """How many listings were first seen yesterday or today (first_seen is a date, so the window is whole days)."""
    since = (now - timedelta(days=1)).strftime(DATE_FORMAT)
    return sum(1 for e in entries if e.first_seen >= since)


def market_lines(now: Medians, before: Medians) -> list[str]:
    """Return the groups whose median asking price moved MARKET_MOVE or more since the last digest."""
    moves: list[tuple[float, str]] = []
    for label, (median, n) in now.items():
        old_median, old_n = before.get(label, (0.0, 0))
        if min(n, old_n) < MIN_ITEMS or not old_median:
            continue
        change = (median - old_median) / old_median
        if abs(change) >= MARKET_MOVE:
            sign = "+" if change > 0 else "−"
            moves.append(
                (abs(change), f"{esc(label)}: {k(old_median)} → {k(median)} ({sign}{round(100 * abs(change))}%)")
            )
    return [line for _, line in sorted(moves, reverse=True)[:SECTION_LINES]]


def section(title: str, lines: Sequence[str]) -> str:
    """Return a section's title with its lines underneath; '' when there are none, so it can be skipped."""
    return "\n".join([title, *lines]) if lines else ""


def build(entries: Sequence[Entry], now: datetime, medians: Medians, before: Medians) -> list[Message]:
    """Build the digest (one message, or none when every section is empty)."""
    fresh = new_count(entries, now)
    blocks = [
        section("⏳ Waiting on the seller", waiting_lines(entries, now)),
        section("⭐ Top open listings", top_lines(entries)),
        f"🆕 New since yesterday: {fresh}" if fresh else "",
        section("📈 Market", market_lines(medians, before)),
    ]
    blocks = [b for b in blocks if b]
    if not blocks:
        return []
    header = f"☀️ Morning digest · {now.strftime('%A %Y-%m-%d')}"
    return [Message("\n\n".join([header, *blocks]), html=True)]
