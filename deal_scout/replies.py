"""What the seller's reply changed: the answered questions and the score, as one line of the summary. Pure."""

import re
from collections.abc import Sequence

from deal_scout.prompts import Listing
from deal_scout.render import esc, score_text


def _norm(item: str) -> str:
    """Return a missing-list item in comparable form: lower case, whitespace collapsed."""
    return re.sub(r"\s+", " ", str(item)).strip().casefold()


def answered_items(old_missing: Sequence[str], new_missing: Sequence[str]) -> list[str]:
    """Return the items that were missing before and are not any more, as the old analysis worded them."""
    still = {_norm(item) for item in new_missing}
    return [item for item in old_missing if _norm(item) not in still]


def score_change(old_score: object, new_score: object) -> str:
    """'score 6.2 → 7.4', 'score 6.2 (unchanged)', or 'score –' when the listing has no score."""
    old, new = score_text(old_score), score_text(new_score)
    if new == "–":
        return "score –"
    return f"score {old} (unchanged)" if old == new else f"score {old} → {new}"


def reply_note(previous: Listing | None, data: Listing, old_score: object, new_score: object) -> str:
    """Return the summary line for a re-analysis after new replies, or '' without an earlier analysis or a new reply."""
    if previous is None or (data.get("replies_seen") or 0) <= (previous.get("replies_seen") or 0):
        return ""
    answered = answered_items(previous.get("missing") or [], data.get("missing") or [])
    tail = f" · answered: {', '.join(esc(item) for item in answered)}" if answered else ""
    return f"📨 After the seller's reply: {score_change(old_score, new_score)}{tail}"
