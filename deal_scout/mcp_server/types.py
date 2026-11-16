"""The structured results the MCP tools return (their output schemas come from these types)."""

from typing import Any, TypedDict


class ListingSummary(TypedDict):
    """One listing in a list or ranking (the owner's corrections applied)."""

    number: int
    title: str
    price_pln: float | None
    score: float | None
    specs_avg: float | None
    verdict: str
    status: str
    first_seen: str


class ListingPage(TypedDict):
    """Listings, newest or best first, and how many matched before the limit."""

    total: int
    listings: list[ListingSummary]


class SpecValue(TypedDict):
    """One spec of a listing: what the listing says and the score 0-10 (null: not scored)."""

    name: str
    value: str
    score: int | None


class ComparedListing(ListingSummary):
    """A listing in a comparison, with every spec."""

    specs: list[SpecValue]


class Comparison(TypedDict):
    """2 to 4 listings side by side and the number of the best one (null when none has a score)."""

    listings: list[ComparedListing]
    best: int | None


class Check(TypedDict):
    """One criterion of criteria.md and how the listing fares (met, missed or unknown)."""

    criterion: str
    result: str
    importance: str
    deal_breaker: bool


class ShownListing(TypedDict):
    """A stored review with the owner's Feedback corrections applied. Holds no phone number."""

    number: int
    title: str
    price_pln: float | None
    score: float | None
    specs_avg: float | None
    verdict: str
    status: str
    summary: str
    specs: list[SpecValue]
    missing: list[str]
    red_flags: list[str]
    checks: list[Check]
    deal_breakers_hit: list[str]
    corrections: str
    seller_message: str


class GroupMedian(TypedDict):
    """The median asking price of one market group."""

    group: str
    median_pln: float
    items: int


class MarketPrices(TypedDict):
    """market.md as text and its group medians."""

    text: str
    groups: list[GroupMedian]


class ReviewFormat(TypedDict):
    """What submit_review expects."""

    json_schema: dict[str, Any]
    optional_keys: list[str]
    specs: list[str]
    categories: dict[str, list[str]]


class ChecklistItem(TypedDict):
    """One on-site check and its mark: ok, bad or empty (not checked)."""

    item: str
    mark: str


class ViewingChecklist(TypedDict):
    """The on-site checklist of a listing: the owner's frozen one if a viewing started, else the review's own."""

    number: int
    title: str
    frozen: bool
    started: str
    items: list[ChecklistItem]


class Submitted(TypedDict):
    """What submit_review stored, with the score the server computed."""

    number: int
    score: float | None
    specs_avg: float | None
    verdict: str
    deal_breakers_hit: list[str]
    missing: list[str]
    red_flags: list[str]
    summary_text: str
    whatsapp_url: str | None
    sheet_updated: bool
    card_resource: str | None


class ReplyRecorded(TypedDict):
    """The seller's reply was saved; the listing's current review and what to do next."""

    review: ShownListing
    instruction: str
