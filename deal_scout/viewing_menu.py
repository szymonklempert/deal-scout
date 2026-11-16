"""The viewing checklist: the items to check on site, the buttons and the results. Pure; callback data uses indexes."""

from collections.abc import Sequence

from deal_scout.prompts import Listing

MAX_ITEMS = 15


def fallback_checks(data: Listing) -> list[str]:
    """Build a checklist from the open questions and red flags, for listings analysed before viewing_checks existed."""
    items = [f"Verify: {item}" for item in data.get("missing") or []]
    items += [f"Check: {item}" for item in data.get("red_flags") or []]
    return items[:MAX_ITEMS]


def viewing_items(data: Listing) -> list[str]:
    """Return the checklist to freeze for a viewing: the model's own, else the fallback; never more than MAX_ITEMS."""
    items: Sequence[str] = [str(x).strip() for x in data.get("viewing_checks") or [] if str(x).strip()]
    return list(items)[:MAX_ITEMS] if items else fallback_checks(data)
