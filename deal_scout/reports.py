"""Rankings and comparisons of stored listings as Telegram messages: /top, /compare and the numbers they share. Pure.

library.py reads the files and builds the Entry objects; nothing here touches the disk. Nothing is specific to
what is being bought: the compared specs are the domain's own.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from deal_scout.market import gross_price, num, same_item
from deal_scout.prompts import Listing
from deal_scout.render import Message, esc, fmt_int
from deal_scout.scoring import ordered_specs
from deal_scout.storage import Row, is_open

TOP_DEFAULT = 5
TOP_MAX = 10
TITLE_WIDTH = 40
LABEL_WIDTH = 14
COLUMN_WIDTH = 6
COMPARE_MIN, COMPARE_MAX = 2, 4
NONE = "–"
SHOW_LINK = re.compile(r"/show_(\d+)")


@dataclass(frozen=True)
class Entry:
    """What the reports need to know about one analysed listing (the owner's corrections already applied)."""

    n: int
    title: str
    verdict: str
    status: str
    score: float | None
    specs_avg: float | None
    price: float | None
    year: float | None
    km: float | None
    first_seen: str = ""
    specs: tuple[tuple[str, int | None], ...] = ()  # (spec name, score 0-10) in the domain's order
    contacted_at: str = ""
    replies_seen: int = 0

    @property
    def is_open(self) -> bool:
        """Whether the listing is still in play."""
        return is_open({"status": self.status})


def make_entry(n: int, row: Row, shown: Listing | None, spec_names: Sequence[str], stored: Listing | None) -> Entry:
    """Build an Entry from the CSV row, the overrides-applied data (for the spec scores) and the stored data.

    shown and stored are None when listings/NNN.json is missing or unreadable: the spec scores are then empty.
    """
    stored = stored or {}
    return Entry(
        n=n,
        title=str(row.get("title") or ""),
        verdict=str(row.get("verdict") or ""),
        status=str(row.get("status") or ""),
        score=num(row.get("score")),
        specs_avg=num(row.get("specs_avg")),
        price=num(row.get("price_pln")),
        year=num(row.get("year")),
        km=num(row.get("mileage_km")),
        first_seen=str(row.get("first_seen") or ""),
        specs=tuple((name, score) for name, _, score in ordered_specs(shown, list(spec_names))) if shown else (),
        contacted_at=str(stored.get("contacted_at") or ""),
        replies_seen=int(stored.get("replies_seen") or 0),
    )


def rank_key(entry: Entry) -> tuple[bool, float, bool, float, int]:
    """Sort key: best score first, then specs average, then the newest; unscored listings last."""
    return (
        entry.score is None,
        -(entry.score or 0),
        entry.specs_avg is None,
        -(entry.specs_avg or 0),
        -entry.n,
    )


def rank_open(entries: Sequence[Entry]) -> list[Entry]:
    """Return the listings still in play, best first."""
    return sorted((e for e in entries if e.is_open), key=rank_key)


def short(text: str, width: int = TITLE_WIDTH) -> str:
    """Cut text to at most width characters, ending in an ellipsis when something was cut."""
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def score_label(score: float | None) -> str:
    """'7.4/10', or an en dash when there is no score."""
    return NONE if score is None else f"{score:.1f}/10"


def show_link(n: int) -> str:
    """'/show_12': Telegram makes it a tappable link that sends the command."""
    return f"/show_{n}"


def split_show_link(cmd: str, args: Sequence[str]) -> tuple[str, list[str]]:
    """Turn the tapped '/show_12' into ('/show', ['12']); any other command passes through unchanged."""
    m = SHOW_LINK.fullmatch(cmd)
    return ("/show", [m.group(1), *args]) if m else (cmd, list(args))


def ranking_line(rank: int, entry: Entry) -> str:
    """One /top line: '1. Mercedes Sprinter 313 · 7.4/10 · /show_12' (Telegram HTML)."""
    title = esc(short(entry.title)) or f"#{entry.n}"
    return f"{rank}. {title} · {score_label(entry.score)} · {show_link(entry.n)}"


def parse_limit(args: Sequence[str]) -> int:
    """/top's optional count: the default for none or nonsense, at most TOP_MAX, at least 1."""
    arg = args[0].lstrip("#") if args else ""
    return max(1, min(TOP_MAX, int(arg))) if arg.isdigit() else TOP_DEFAULT


def top_message(entries: Sequence[Entry], limit: int = TOP_DEFAULT) -> Message:
    """Build the /top answer: the best open listings, one line each, with a tappable /show link."""
    best = rank_open(entries)[:limit]
    if not best:
        return Message("No open listings yet.")
    return Message("\n".join(ranking_line(i, e) for i, e in enumerate(best, start=1)), html=True)


def parse_numbers(args: Sequence[str]) -> list[int]:
    """Return the listing numbers from '7 12 #15' or '7,12'; duplicates dropped; anything else is ignored."""
    seen: list[int] = []
    for word in " ".join(args).replace(",", " ").split():
        word = word.lstrip("#")
        if word.isdigit() and int(word) not in seen:
            seen.append(int(word))
    return seen


def compare_problem(numbers: Sequence[int]) -> str:
    """Why /compare can't run with these numbers, as one line; '' when the count is fine."""
    if len(numbers) < COMPARE_MIN:
        return "Usage: /compare 7 12 15 (2 to 4 listing numbers)"
    if len(numbers) > COMPARE_MAX:
        return f"/compare takes at most {COMPARE_MAX} listings: /compare 7 12 15"
    return ""


def thousands(value: float | None) -> str:
    """'84k' for 84000 (one decimal below 10k: '1.4k'); a plain number below 1000; an en dash when unknown."""
    if value is None:
        return NONE
    if value < 1000:
        return str(round(value))
    if value < 10000:
        return f"{value / 1000:.1f}k".replace(".0k", "k")
    return f"{round(value / 1000)}k"


def number_text(value: float | None) -> str:
    """Format a score or a year without a trailing '.0', or an en dash."""
    return NONE if value is None else f"{value:g}"


def table_row(label: str, cells: Sequence[str]) -> str:
    """One table line: the label cut to LABEL_WIDTH, then every cell right-aligned in COLUMN_WIDTH."""
    return label[:LABEL_WIDTH].ljust(LABEL_WIDTH) + "".join(c.rjust(COLUMN_WIDTH) for c in cells)


def compare_table(entries: Sequence[Entry], spec_names: Sequence[str]) -> str:
    """Build the plain-text table: facts first, then every spec's score; at most 38 wide for 4 listings."""
    lookup = [dict(e.specs) for e in entries]
    lines = [
        table_row("", [f"#{e.n}" for e in entries]),
        table_row("Score", [number_text(e.score) for e in entries]),
        table_row("Specs avg", [number_text(e.specs_avg) for e in entries]),
        table_row("Price", [thousands(e.price) for e in entries]),
        table_row("Year", [number_text(e.year) for e in entries]),
        table_row("Km", [thousands(e.km) for e in entries]),
    ]
    for name in spec_names:
        lines.append(table_row(name, [number_text(scores.get(name)) for scores in lookup]))
    return "\n".join(lines)


def best_overall(entries: Sequence[Entry]) -> Entry | None:
    """Return the scored listing that ranks first, or None when none has a score."""
    scored = [e for e in entries if e.score is not None]
    return min(scored, key=rank_key) if scored else None


def compare_message(entries: Sequence[Entry], spec_names: Sequence[str]) -> Message:
    """Build the /compare answer: a monospace table, the best listing and links to open each one."""
    text = f"<pre>{esc(compare_table(entries, spec_names))}</pre>"
    best = best_overall(entries)
    if best is not None:
        text += f"\n\nBest overall: #{best.n} ({score_label(best.score)})"
    return Message(text + "\nOpen: " + " · ".join(show_link(e.n) for e in entries), html=True)


def price_change(old: float | None, new: float | None) -> str:
    """Describe a repost's price against the first one: '118 500 → 112 000 PLN (−5%)', 'same price', 'price up 3%'."""
    if old is None or new is None:
        return ""
    if new == old:
        return "same price"
    pct = round(100 * (new - old) / old)
    if pct > 0:
        return f"price up {pct}%: {fmt_int(old)} → {fmt_int(new)} PLN"
    shown = f"(−{-pct}%)" if pct < 0 else "(under 1%)"
    return f"price {fmt_int(old)} → {fmt_int(new)} PLN {shown}"


def repost_line(n: int, rows: Mapping[int, Row], same_item_key: str | None, unit: str) -> str:
    """Return the summary line pointing at an earlier listing of the same item, or '' when there is none.

    The most recent earlier match is the one compared; older ones follow as "(also #3)". Uses market.same_item.
    """
    earlier = [m for m in sorted(rows, reverse=True) if m < n and same_item(rows[m], rows[n], same_item_key)]
    if not earlier:
        return ""
    last, others = earlier[0], earlier[1:]
    seen = f" (first seen {esc(rows[last]['first_seen'])})" if rows[last].get("first_seen") else ""
    change = price_change(gross_price(rows[last]), gross_price(rows[n]))
    line = f"🔁 Same {esc(unit)} as #{last}{seen}: {change}" if change else f"🔁 Looks like #{last} again{seen}"
    return line + (f" (also {', '.join(f'#{m}' for m in others)})" if others else "")
