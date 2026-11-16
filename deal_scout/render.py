"""Format a listing's summary as Telegram HTML."""

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from deal_scout.feedback import spec_marks
from deal_scout.prompts import Listing
from deal_scout.scoring import ordered_specs
from deal_scout.storage import Row

Button = tuple[str, str]  # (label, an https:// URL or callback data such as "msg:7:48600123456")


@dataclass(frozen=True)
class Message:
    """One Telegram message the bot sends: text, optional HTML formatting and buttons."""

    text: str
    html: bool = False
    buttons: tuple[Button, ...] = ()
    fallback_url: str | None = None  # where to paste the text if Telegram refuses the buttons
    columns: int = 1  # buttons per row


def esc(text: object) -> str:
    """Escape text for Telegram's HTML mode."""
    return html.escape(str(text), quote=False)


def score_text(score: object) -> str:
    """Format a row's score for display: the number, or an en dash when there is none."""
    return "–" if score in ("", None) else str(score)


def dot(score: float | None) -> str:
    """Return the traffic-light emoji for a 0-10 score."""
    if score is None:
        return "⚪"
    return "🟢" if score >= 7 else "🟡" if score >= 4 else "🔴"


def fmt_int(v: Any) -> str:
    """Format a number with a space as the thousands separator."""
    return f"{int(v):,}".replace(",", " ")


def contact_line(n: int, data: Listing, phone: str | None, stored: bool = False) -> str:
    """Return the line that says how the seller wants to be contacted and what the buttons do.

    stored: rebuilt from saved data, which never holds the phone: don't claim the screenshots had none.
    """
    pref = data.get("contact_preference") or "not stated"
    how = f"listing asks for {pref}" if pref != "not stated" else "no contact preference"
    if phone:
        return f"💬 {how} · tap below for the message to the seller"
    if stored:
        return (
            f"💬 {how} · tap below for the message; "
            f"/phone {n} 600123456 adds the seller's number to the WhatsApp button"
        )
    return f"💬 {how} · tap below for the message; no phone on the screenshots (/phone {n} 600123456 adds one)"


def summary_buttons(n: int, data: Listing, phone: str | None, sheet_link: str | None) -> list[Button]:
    """Return the buttons under the summary.

    The message buttons are taps the bot answers (callback data); the phone number rides along inside the button,
    so it is never written to a file. The Feedback button carries it too, so the summary refreshed after a correction
    can offer the same buttons.
    """
    buttons: list[Button] = []
    if (data.get("seller_message") or "").strip():
        buttons.append(("💬 WhatsApp message", f"msg:{n}:{phone or ''}"))
    if (data.get("seller_sms") or "").strip():
        buttons.append(("✉️ SMS", f"sms:{n}:{phone or ''}"))
    buttons.append(("📨 Seller replied", f"sr:{n}"))
    if data.get("specs") or data.get("criteria_checks"):
        buttons.append(("📝 Feedback", f"fx:{n}:{phone or ''}"))
    if sheet_link:
        buttons.append(("📊 Open sheet", sheet_link))
    return buttons


def summary_text(
    n: int,
    data: Listing,
    row: Row,
    specs: list[str],
    others: Sequence[int] = (),
    phone: str | None = None,
    reply_note: str = "",
    stored: bool = False,
) -> str:
    """Telegram HTML: header, the fixed spec list (one line each, with a score), then flags and contact.

    reply_note, when given, is a line about what the seller's reply changed; it follows the summary sentence.
    stored: rebuilt from saved data (/show), where the phone is unknown rather than missing from the screenshots.
    """
    price = f"<b>{fmt_int(data['price_pln'])} PLN</b>" if data.get("price_pln") is not None else "price not shown"
    where = [price] + [esc(data[k]) for k in ("location", "source") if data.get(k)]
    head = [f"<b>#{n} {esc(data.get('title') or 'listing')}</b>", " · ".join(where)]
    if data.get("seller_name"):
        also = f" (also #{', #'.join(map(str, others))})" if others else ""
        head.append(f"Seller: {esc(data['seller_name'])}{also}")
    sc = f"<b>{row['score']}/10</b>" if row["score"] != "" else "– (fill in criteria.md)"
    avg = row["specs_avg"]
    specs_text = f"{avg}/10" if avg != "" else "–"
    head.append(f"Score {sc} · specs <b>{specs_text}</b> · <b>{esc(data.get('verdict', '?'))}</b>")
    if data.get("summary"):
        head.append(f"<i>{esc(data['summary'])}</i>")
    if reply_note:
        head.append(reply_note)

    marks = spec_marks(data)
    spec_lines = []
    for name, value, s in ordered_specs(data, specs):
        if name in marks:
            was = marks[name]["was"]
            now = "no score" if s is None else f"{s}/10"
            before = "no score" if was is None else was
            spec_lines.append(f"{dot(s)} {esc(name)}: {esc(value)} · {now} ✏️ (was {before})")
        elif s is None:
            spec_lines.append(f"{dot(s)} {esc(name)}: <i>{esc(value)}</i>")
        else:
            spec_lines.append(f"{dot(s)} {esc(name)}: {esc(value)} · {s}/10")

    tail = []
    if row["deal_breakers_hit"]:
        tail.append("⛔ Deal breaker: " + esc(row["deal_breakers_hit"]))
    if data.get("red_flags"):
        tail.append("🚩 " + esc(" · ".join(data["red_flags"][:3])))
    if data.get("seller_message"):
        tail.append(contact_line(n, data, phone, stored))
    tail.append(f"📄 listings/{n:03d}.md")
    return "\n".join(head) + "\n\n" + "\n".join(spec_lines) + "\n\n" + "\n".join(tail)


def plain_text(html_text: str) -> str:
    """Telegram HTML as plain text, for the terminal and as the fallback when Telegram rejects the markup."""
    return html.unescape(re.sub(r"</?(?:b|i|u|pre)>", "", html_text))
