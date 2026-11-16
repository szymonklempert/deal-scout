"""The write side of the MCP server: a review comes in, is validated and scored here, and stored by apply_review."""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from deal_scout import sheet, storage
from deal_scout.contact import contact_messages, normalise_phone
from deal_scout.errors import ProcessError
from deal_scout.feedback import scrub_numbers
from deal_scout.inbox import Inbox
from deal_scout.mcp_server.reading import NUMBER_MAX, Reader, in_range
from deal_scout.mcp_server.types import ReplyRecorded, Submitted
from deal_scout.process import apply_review
from deal_scout.render import plain_text
from deal_scout.storage import Row
from deal_scout.validation import validate_review

log = logging.getLogger("dealscout")

MAX_NOTES, MAX_REPLY, MAX_CARD = 10_000, 10_000, 50_000  # characters
MAX_LINKS, MAX_LINK = 20, 500
URI_CARD = "deal-scout://listing/{n}"
REPLY_NEXT = "Re-review with these replies (they override the listing) and call submit_review(number={n}, ...)."


def check_caps(review: dict[str, Any], notes: str, links: list[str]) -> None:
    """Refuse oversized or odd input before anything is written; each message says what to change."""
    if len(notes) > MAX_NOTES:
        raise ToolError(f"notes is {len(notes)} characters; the limit is {MAX_NOTES}. Summarise it.")
    if len(links) > MAX_LINKS:
        raise ToolError(f"links has {len(links)} entries; the limit is {MAX_LINKS}.")
    for link in links:
        if not link.startswith("https://") or len(link) > MAX_LINK or any(c.isspace() for c in link):
            raise ToolError(f"links must be https:// URLs without spaces, at most {MAX_LINK} characters: {link[:60]!r}")
    card = review.get("card_markdown")
    if isinstance(card, str) and len(card) > MAX_CARD:
        raise ToolError(f"card_markdown is {len(card)} characters; the limit is {MAX_CARD}. Shorten the card.")


class Reviewer:
    """The write side: reviews come in, are validated and scored here, and stored by apply_review."""

    def __init__(self, reader: Reader, push: Callable[..., None] | None = None) -> None:
        """Bind a reader (data home, settings, domain); push copies a row to the Google Sheet (default: sheet.push)."""
        self.reader = reader
        self.home = reader.home
        self.settings = reader.settings
        self.domain = reader.domain
        self.listings = reader.listings
        self.push = push or functools.partial(sheet.push, settings=reader.settings)

    def prepare_inbox(self, number: int | None, notes: str, links: list[str]) -> int:
        """Return the listing number to review: a new inbox folder, or an existing one; save notes and links to it."""
        inbox = Inbox(self.home)
        with storage.rows_lock(self.listings):  # two clients must not take the same new number
            if number is None:
                n = inbox.next_number()
                inbox.folder(n)
            else:
                n = in_range(number, "number", 1, NUMBER_MAX)
                if not (inbox.dir / f"{n:03d}").is_dir():
                    raise ToolError(f"#{n} does not exist; leave number out to add a new listing")
            if notes.strip():
                inbox.add_note(n, scrub_numbers(notes.strip()), time.time())
            inbox.add_links(n, links)
        return n

    def submit_review(
        self, review: dict[str, Any], number: int | None = None, notes: str = "", links: list[str] | None = None
    ) -> Submitted:
        """Store your review of a listing; the server computes the score from your criteria checks.

        review follows the JSON schema that review_format returns (include card_markdown). Leave number out for a
        new listing, or give one to review it again (the owner's Feedback corrections are kept). notes: the owner's
        words about the listing (at most 10 000 characters). links: https:// URLs, at most 20. If the review has
        problems the call fails and lists them: fix them and call again.
        """
        links = links or []
        check_caps(review, notes, links)
        if errors := validate_review(review, self.domain):
            raise ToolError(
                f"The review has {len(errors)} problem(s); fix them and call submit_review again "
                "(review_format shows the expected shape):\n" + "\n".join(f"- {e}" for e in errors)
            )
        n = self.prepare_inbox(number, notes, links)
        data = dict(review)
        phone = normalise_phone(data.get("seller_phone"))  # apply_review drops it; it only builds the link
        drafts = contact_messages(data, phone)
        pushed: list[bool] = []

        def push(row: Row, fields: list[str]) -> None:
            self.push(row, fields)
            pushed.append(True)

        try:
            out = apply_review(self.home, n, data, settings=self.settings, domain=self.domain, pusher=push)
        except ProcessError as exc:
            raise ToolError(str(exc)) from exc
        log.info("#%d reviewed over MCP", n)
        shown = self.reader.show_listing(n)
        has_card = (self.listings / f"{n:03d}.md").is_file()
        return Submitted(
            number=n,
            score=shown["score"],
            specs_avg=shown["specs_avg"],
            verdict=shown["verdict"],
            deal_breakers_hit=shown["deal_breakers_hit"],
            missing=shown["missing"],
            red_flags=shown["red_flags"],
            summary_text=plain_text(out[0].text),
            whatsapp_url=drafts[0].buttons[0][1] if drafts and drafts[0].buttons else None,
            sheet_updated=bool(pushed),
            card_resource=URI_CARD.format(n=n) if has_card else None,
        )

    def record_seller_reply(self, number: int, reply: str) -> ReplyRecorded:
        """Save the seller's reply to an analysed listing (long digit runs are removed), then re-review it.

        reply is at most 10 000 characters. Returns the current review and what to do next; this tool does not
        re-analyse anything itself.
        """
        in_range(number, "number", 1, NUMBER_MAX)
        text = reply.strip()
        if not text or len(text) > MAX_REPLY:
            raise ToolError(f"reply must be 1 to {MAX_REPLY} characters, got {len(text)}")
        self.reader.stored(number)  # the listing must have been analysed
        with storage.rows_lock(self.listings):
            Inbox(self.home).add_reply(number, scrub_numbers(text), time.time())
        return ReplyRecorded(review=self.reader.show_listing(number), instruction=REPLY_NEXT.format(n=number))
