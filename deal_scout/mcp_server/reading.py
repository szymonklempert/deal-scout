"""The read side of the MCP server: resources and read-only tools over one data home."""

from pathlib import Path

from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError

from deal_scout import library, market, prompts, reports, storage
from deal_scout.config import Settings
from deal_scout.domain import Domain
from deal_scout.feedback import apply_overrides, corrections_text
from deal_scout.mcp_server.types import (
    Check,
    ChecklistItem,
    ComparedListing,
    Comparison,
    GroupMedian,
    ListingPage,
    ListingSummary,
    MarketPrices,
    ReviewFormat,
    ShownListing,
    SpecValue,
    ViewingChecklist,
)
from deal_scout.process import read_listing
from deal_scout.prompts import Listing
from deal_scout.reports import Entry
from deal_scout.scoring import clean, ordered_specs, score, specs_avg
from deal_scout.storage import Row, read_rows
from deal_scout.validation import OPTIONAL_KEYS
from deal_scout.viewing_menu import viewing_items

LIST_MAX, TOP_MAX = 100, reports.TOP_MAX
NUMBER_MAX = 9999


def in_range(value: int, name: str, low: int, high: int) -> int:
    """Return the argument if it is an integer from low to high, else raise a ToolError the model can act on."""
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ToolError(f"{name} must be a whole number from {low} to {high}, got {value!r}")
    return value


def summarise(entry: Entry) -> ListingSummary:
    """Return the headline fields of a listing."""
    return ListingSummary(
        number=entry.n,
        title=entry.title,
        price_pln=entry.price,
        score=entry.score,
        specs_avg=entry.specs_avg,
        verdict=entry.verdict,
        status=entry.status,
        first_seen=entry.first_seen,
    )


def spec_values(shown: Listing, domain: Domain) -> list[SpecValue]:
    """Every spec of the domain in order, with the value and the score (overrides already applied in shown)."""
    return [SpecValue(name=n, value=v, score=s) for n, v, s in ordered_specs(shown, domain.specs)]


class Reader:
    """The server's read side: resources and read-only tools over one data home. Registered by create_server."""

    def __init__(self, home: Path, settings: Settings, domain: Domain) -> None:
        """Bind the data home, the settings and the active domain."""
        self.home = home
        self.settings = settings
        self.domain = domain
        self.listings = home / "listings"

    # --- resources ---------------------------------------------------------------------------------------------

    def domain_files(self) -> dict[str, Path]:
        """Return the files the domain resource may serve, by name: the allowlist (criteria.md has its own resource)."""
        names = ["instructions.md", "checklist.md", "card-template.md", *self.domain.reference_files]
        return {name: self.domain.dir / name for name in names}

    def criteria(self) -> str:
        """Return the owner's criteria.md: hard limits, must-haves, nice-to-haves and deal breakers."""
        path = self.domain.dir / "criteria.md"
        if not path.is_file():
            return "criteria.md is missing: run `deal-scout init` and fill it in before scoring."
        return path.read_text(encoding="utf-8")

    def domain_file(self, name: str) -> str:
        """Return one of the domain's files: instructions.md, checklist.md, card-template.md or a reference file."""
        allowed = self.domain_files()
        if name not in allowed:
            raise ResourceNotFoundError(f"unknown domain file {name!r}; available: {', '.join(allowed)}")
        path = allowed[name]
        if not path.is_file():
            raise ResourceNotFoundError(f"domain file {name!r} is missing")
        return path.read_text(encoding="utf-8")

    def listing_card(self, number: str) -> str:
        """Return the card (listings/NNN.md) of an analysed listing."""
        try:
            n = int(number)
        except ValueError:
            raise ResourceError(f"listing number must be a positive integer, got {number!r}") from None
        if not 1 <= n <= NUMBER_MAX:
            raise ResourceError(f"listing number must be from 1 to {NUMBER_MAX}, got {n}")
        path = self.listings / f"{n:03d}.md"
        if not path.is_file():
            raise ResourceNotFoundError(f"#{n} has no card yet")
        return path.read_text(encoding="utf-8")

    def market_page(self) -> str:
        """Return the asking prices of the owner's own listings, grouped (listings/market.md)."""
        path = self.listings / "market.md"
        return path.read_text(encoding="utf-8") if path.is_file() else "No market page yet: no listing has a price."

    # --- read-only tools ---------------------------------------------------------------------------------------

    def stored(self, n: int) -> tuple[Listing, Row]:
        """Read the stored review of listing n and its CSV row under the lock; a ToolError when not analysed."""
        with storage.rows_lock(self.listings):
            row = read_rows(self.listings / "listings.csv").get(n)
            data = read_listing(self.listings / f"{n:03d}.json")
        if row is None or data is None:
            raise ToolError(f"#{n} has not been analysed yet; list_listings shows the numbers that have")
        return data, row

    def list_listings(self, limit: int = 20, open_only: bool = True) -> ListingPage:
        """List analysed listings, newest first. open_only hides rejected, sold, bought, skipped and closed ones.

        limit is 1 to 100. Use show_listing for the details of one.
        """
        in_range(limit, "limit", 1, LIST_MAX)
        entries = sorted(library.load_entries(self.home, self.domain).values(), key=lambda e: -e.n)
        found = [e for e in entries if e.is_open or not open_only]
        return ListingPage(total=len(found), listings=[summarise(e) for e in found[:limit]])

    def top(self, n: int = 5) -> ListingPage:
        """Return the best open listings by score, best first (the Telegram /top ranking). n is 1 to 10."""
        in_range(n, "n", 1, TOP_MAX)
        ranked = reports.rank_open(list(library.load_entries(self.home, self.domain).values()))
        return ListingPage(total=len(ranked), listings=[summarise(e) for e in ranked[:n]])

    def compare(self, numbers: list[int]) -> Comparison:
        """Compare 2 to 4 analysed listings: headline fields and every spec's value and score, and the best one."""
        wanted = list(dict.fromkeys(in_range(n, "numbers", 1, NUMBER_MAX) for n in numbers))
        if not reports.COMPARE_MIN <= len(wanted) <= reports.COMPARE_MAX:
            raise ToolError(
                f"numbers must hold {reports.COMPARE_MIN} to {reports.COMPARE_MAX} different listing numbers, "
                f"got {len(wanted)}"
            )
        entries = library.load_entries(self.home, self.domain)
        if missing := [n for n in wanted if n not in entries]:
            raise ToolError("Not analysed yet: " + ", ".join(f"#{n}" for n in missing))
        compared = [
            ComparedListing(**summarise(entries[n]), specs=spec_values(self.shown(n), self.domain)) for n in wanted
        ]
        best = reports.best_overall([entries[n] for n in wanted])
        return Comparison(listings=compared, best=best.n if best else None)

    def shown(self, n: int) -> Listing:
        """Return the stored review of listing n with the owner's Feedback corrections applied."""
        data, _ = self.stored(n)
        return apply_overrides(data, data.get("overrides") or {})

    def show_listing(self, number: int) -> ShownListing:
        """Return the stored review of one listing, with the owner's Feedback corrections applied (no phone number)."""
        in_range(number, "number", 1, NUMBER_MAX)
        data, row = self.stored(number)
        shown = apply_overrides(data, data.get("overrides") or {})
        checks = shown.get("criteria_checks") or []
        return ShownListing(
            number=number,
            title=str(shown.get("title") or ""),
            price_pln=shown.get("price_pln"),
            score=score(checks),
            specs_avg=specs_avg(shown, self.domain.specs),
            verdict=str(shown.get("verdict") or ""),
            status=str(row.get("status") or ""),
            summary=str(shown.get("summary") or ""),
            specs=spec_values(shown, self.domain),
            missing=list(shown.get("missing") or []),
            red_flags=list(shown.get("red_flags") or []),
            checks=[
                Check(
                    criterion=str(c["criterion"]),
                    result=str(c["result"]),
                    importance=str(c.get("importance") or "required"),
                    deal_breaker=bool(c["deal_breaker"]),
                )
                for c in checks
            ],
            deal_breakers_hit=[clean(c["criterion"]) for c in checks if c["deal_breaker"] and c["result"] == "missed"],
            corrections=corrections_text(shown),
            seller_message=str(shown.get("seller_message") or ""),
        )

    def viewing_checklist(self, number: int) -> ViewingChecklist:
        """Return what to check on site: the frozen checklist and marks if a viewing started, else the review's own."""
        in_range(number, "number", 1, NUMBER_MAX)
        data, _ = self.stored(number)
        frozen = data.get("viewing") if isinstance(data.get("viewing"), dict) else None
        if frozen and frozen.get("items"):
            marks = frozen.get("marks") or {}
            items = [ChecklistItem(item=str(x), mark=str(marks.get(str(i), ""))) for i, x in enumerate(frozen["items"])]
            started = str(frozen.get("started") or "")
        else:
            items = [ChecklistItem(item=x, mark="") for x in viewing_items(data)]
            started = ""
        return ViewingChecklist(
            number=number, title=str(data.get("title") or ""), frozen=bool(frozen), started=started, items=items
        )

    def market_prices(self) -> MarketPrices:
        """Return the market page (the owner's own asking prices) and each group's median price and count."""
        with storage.rows_lock(self.listings):
            rows = read_rows(self.listings / "listings.csv")
        medians = market.group_medians(list(rows.values()), self.domain.market) if rows else {}
        groups = [GroupMedian(group=g, median_pln=m, items=n) for g, (m, n) in medians.items()]
        return MarketPrices(text=self.market_page(), groups=groups)

    def review_format(self) -> ReviewFormat:
        """Return what submit_review expects: the review's JSON schema, the spec names in order, the category values."""
        return ReviewFormat(
            json_schema=prompts.schema(self.domain, card=True),
            optional_keys=list(OPTIONAL_KEYS),
            specs=list(self.domain.specs),
            categories={key: list(cat["values"]) for key, cat in self.domain.categories.items()},
        )
