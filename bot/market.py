#!/usr/bin/env python3
"""Market prices from your own listings: listings/market.md, rebuilt after every analysis. No AI, no network.

    python3 bot/market.py        # rebuild it by hand

It groups the items in listings/listings.csv the way the domain's domain.json says (e.g. model family, year band,
mileage band, build) and reports the asking prices per group (median and the middle half). An item reposted in
several groups or reposted later counts once, at its latest price, and price drops are counted. Listings without a
price, and prices the analysis scored 0 (suspiciously low, the scam pattern), are left out. Groups with fewer than
MIN_N items are not shown. It never edits knowledge.md: it flags where the "fair" ranges in your reference files and
the asking prices disagree, and you decide.
Standard library only.
"""
import re
import statistics
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import config
import domain
from storage import Row, read_rows

MIN_N = 4
VAT = 1.23
# Grouping comes from the domain's domain.json ("market"); these are used when it says nothing.
DEFAULTS = {"same_item_key": None, "group_by": ["years"], "detail_by": [], "finest_by": [],
            "year_bands": [[0, "any year"]], "km_bands": [[None, "any km"]]}


def num(value: object) -> float | None:
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def year_band(year: object, bands: Sequence[Sequence[Any]]) -> str:
    """bands: [[start_year, label], ...] newest first; the first whose start the year reaches."""
    y = num(year)
    if y is None:
        return "year unknown"
    return next((label for start, label in bands if y >= start), "older")


def km_band(km: object, bands: Sequence[Sequence[Any]]) -> str:
    """bands: [[upper_limit or null, label], ...] lowest first; null = no upper limit."""
    k = num(km)
    if k is None:
        return "km unknown"
    return next((label for limit, label in bands if limit is None or k < limit), "more km")


def price_score(row: Row) -> int | None:
    m = re.search(r"\((\d+)/10\)\s*$", str(row.get("Price vs market") or ""))
    return int(m.group(1)) if m else None


def gross_price(row: Row) -> float | None:
    """Asking price in PLN including VAT: a net ("netto") price is +23% for a private buyer."""
    p = num(row.get("price_pln"))
    if p is None:
        return None
    return round(p * VAT) if str(row.get("price_net")).strip().lower() in ("1", "true", "yes") else p


def norm(text: object) -> str:
    return " ".join(str(text or "").lower().split())


def same_item(a: Row, b: Row, key: str | None = "model_family") -> bool:
    """The same item posted twice: same family (the domain's same_item_key, else the model) and year, mileage
    within 3 000 km when there is one, and the same seller if both are known."""
    fam = norm(a.get(key or "model"))
    if not fam or fam == "unknown" or fam != norm(b.get(key or "model")) or norm(a.get("year")) != norm(b.get("year")):
        return False
    ka, kb = num(a.get("mileage_km")), num(b.get("mileage_km"))
    if (ka is None) != (kb is None) or (ka is not None and kb is not None and abs(ka - kb) > 3000):
        return False
    sa, sb = norm(a.get("seller")), norm(b.get("seller"))
    return not (sa and sb and sa != sb)


def items(rows: Iterable[Row], key: str | None = "model_family") -> list[list[Row]]:
    """Group listing rows into items (reposts together), oldest listing first."""
    out: list[list[Row]] = []
    for row in sorted(rows, key=lambda r: int(r["number"])):
        for van in out:
            if same_item(van[0], row, key):
                van.append(row)
                break
        else:
            out.append([row])
    return out


def k(value: float) -> str:
    return f"{value / 1000:.1f}k".replace(".0k", "k")


def group_line(label: str, members: Sequence[tuple[list[Row], float]]) -> str:
    """members: [(van rows, latest price)]"""
    prices = sorted(p for _, p in members)
    med = statistics.median(prices)
    q1, _, q3 = statistics.quantiles(prices, n=4, method="inclusive")
    parts = [f"median {k(med)} PLN, middle half {k(q1)}-{k(q3)}, n={len(prices)}"]
    drops: list[float] = []
    for rows, _ in members:
        ps = [p for p in map(gross_price, rows) if p is not None]
        if len(ps) > 1 and ps[-1] < ps[0]:
            drops.append((ps[0] - ps[-1]) / ps[0])
    if drops:
        parts.append(f"{len(drops)} price drop(s), avg -{round(100 * sum(drops) / len(drops))}%")
    low_vals = [num(rows[-1].get("fair_price_low")) for rows, _ in members]
    high_vals = [num(rows[-1].get("fair_price_high")) for rows, _ in members]
    lows, highs = [x for x in low_vals if x], [x for x in high_vals if x]
    line = f"- {label}: {'; '.join(parts)}"
    if lows and highs:
        lo, hi = sum(lows) / len(lows), sum(highs) / len(highs)
        line += f" · your fair {k(lo)}-{k(hi)}"
        if med > hi * 1.05:
            line += f"  ⚠️ asking median {round(100 * (med - hi) / hi)}% above your fair range"
        elif med < lo * 0.95:
            line += f"  ⚠️ asking median {round(100 * (lo - med) / lo)}% below your fair range"
    return line


Group = list[tuple[list[Row], float]]


def select_members(all_vans: Sequence[list[Row]]) -> tuple[Group, int, int]:
    """Split items into those with a usable price, and counts of those without a price or suspiciously low."""
    members: Group = []
    no_price = suspicious = 0
    for van in all_vans:
        latest = van[-1]
        price = gross_price(latest)
        if price is None:
            no_price += 1
        elif price_score(latest) == 0:
            suspicious += 1
        else:
            members.append((van, price))
    return members, no_price, suspicious


def group_key(van: list[Row], fields: Sequence[str], cfg: Mapping[str, Any]) -> tuple[Any, ...]:
    """The group an item falls in for the given fields: band names for years and km, the raw value otherwise."""
    r = van[-1]
    special = {"years": lambda: year_band(r.get("year"), cfg["year_bands"]),
               "km": lambda: km_band(r.get("mileage_km"), cfg["km_bands"])}
    return tuple(special[f]() if f in special else (r.get(f) or "unknown") for f in fields)


def groups(members: Group, fields: Sequence[str], cfg: Mapping[str, Any]) -> dict[tuple[Any, ...], Group]:
    """Group the members by the fields, keeping only groups with at least MIN_N items."""
    g: dict[tuple[Any, ...], Group] = {}
    for van, price in members:
        g.setdefault(group_key(van, fields, cfg), []).append((van, price))
    return {kk: v for kk, v in g.items() if len(v) >= MIN_N}


def header_lines(today: str | None, n_items: int, n_rows: int, no_price: int, suspicious: int) -> list[str]:
    """The title, the caveats and the one-line summary of what was counted and left out."""
    return ["# Market prices from your own listings",
            "",
            "Generated by bot/market.py after every analysis; don't edit (changes are overwritten). Asking prices, "
            f"not sale prices. Net prices +23%. Reposts count once, at the latest price. Groups with fewer than "
            f"{MIN_N} items are not shown.",
            "",
            f"Updated {today or time.strftime('%Y-%m-%d')}: {n_items} item(s) from {n_rows} listing(s); "
            f"left out: {no_price} without a price, {suspicious} suspiciously low.",
            ""]


def section_lines(g: tuple[Any, ...], top: Mapping[tuple[Any, ...], Group], detail: Mapping[tuple[Any, ...], Group],
                  finest: Mapping[tuple[Any, ...], Group], detail_by: Sequence[str]) -> list[str]:
    """One top-level group's heading, its "all" line, and the detail and finest sub-group lines that differ."""
    width = len(g)
    lines = ["## " + " · ".join(g), group_line("all", top[g])]
    for d in sorted(x for x in detail if x[:width] == g and len(detail[x]) < len(top[g])):
        label = ", ".join(f"{v} {k}" for k, v in zip(detail_by, d[width:], strict=False))
        lines.append(group_line(label, detail[d]))
    for f in sorted(x for x in finest if x[:width] == g):
        if len(finest[f]) < len(top[g]):
            lines.append(group_line(", ".join(f[width:]), finest[f]))
    lines.append("")
    return lines


def build(rows: Sequence[Row], cfg: Mapping[str, Any] | None = None, today: str | None = None) -> str:
    """market.md text. cfg: the domain's "market" settings from domain.json."""
    cfg = dict(DEFAULTS, **(cfg or {}))
    all_vans = items(rows, cfg["same_item_key"])
    members, no_price, suspicious = select_members(all_vans)
    lines = header_lines(today, len(all_vans), len(rows), no_price, suspicious)
    gb, db, fb = list(cfg["group_by"]), list(cfg["detail_by"]), list(cfg["finest_by"])
    top = groups(members, gb, cfg)
    if not top:
        lines.append(f"Not enough data yet: no group has {MIN_N} items with a price.")
    detail = groups(members, gb + db, cfg) if db else {}
    finest = groups(members, gb + fb, cfg) if fb else {}
    for g in sorted(top):
        lines.extend(section_lines(g, top, detail, finest, db))
    return "\n".join(lines).rstrip() + "\n"


def write(listings_dir: Path | str, cfg: Mapping[str, Any]) -> None:
    """Rebuild market.md in listings_dir; cfg is the domain's "market" settings."""
    rows = list(read_rows(Path(listings_dir) / "listings.csv").values())
    (Path(listings_dir) / "market.md").write_text(build(rows, cfg), encoding="utf-8")


def main() -> None:
    root = config.ROOT
    config.load_env(root / ".env")
    settings = config.Settings.from_env()
    try:
        cfg = domain.load(settings.domain).market
    except domain.DomainError as exc:
        sys.exit(str(exc))
    write(root / "listings", cfg)
    print((root / "listings" / "market.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
