"""Format a listing's summary as Telegram HTML."""
import html
import re

from scoring import ordered_specs


def esc(text):
    return html.escape(str(text), quote=False)


def dot(score):
    if score is None:
        return "⚪"
    return "🟢" if score >= 7 else "🟡" if score >= 4 else "🔴"


def fmt_int(v):
    return "{:,}".format(int(v)).replace(",", " ")


def contact_line(n, data, phone):
    pref = data.get("contact_preference") or "not stated"
    how = "listing asks for %s" % pref if pref != "not stated" else "no contact preference"
    if phone:
        return "💬 %s · tap below for the message to the seller" % how
    return "💬 %s · tap below for the message; no phone on the screenshots (/phone %d 600123456 adds one)" % (how, n)


def summary_buttons(n, data, phone, sheet_link: str | None):
    """Buttons under the summary. The message buttons are taps the bot answers (callback data); the phone number
    rides along inside the button, so it is never written to a file."""
    buttons = []
    if (data.get("seller_message") or "").strip():
        buttons.append(("💬 WhatsApp message", "msg:%d:%s" % (n, phone or "")))
    if (data.get("seller_sms") or "").strip():
        buttons.append(("✉️ SMS", "sms:%d:%s" % (n, phone or "")))
    if sheet_link:
        buttons.append(("📊 Open sheet", sheet_link))
    return buttons


def summary_text(n, data, row, specs: list[str], others=(), phone=None):
    """Telegram HTML: header, the fixed spec list (one line each, with a score), then flags and contact."""
    price = "<b>%s PLN</b>" % fmt_int(data["price_pln"]) if data.get("price_pln") is not None else "price not shown"
    where = [price] + [esc(data[k]) for k in ("location", "source") if data.get(k)]
    head = ["<b>#%d %s</b>" % (n, esc(data.get("title") or "listing")), " · ".join(where)]
    if data.get("seller_name"):
        head.append("Seller: %s%s" % (esc(data["seller_name"]),
                                      " (also #%s)" % ", #".join(map(str, others)) if others else ""))
    sc = "<b>%s/10</b>" % row["score"] if row["score"] != "" else "– (fill in criteria.md)"
    avg = row["specs_avg"]
    head.append("Score %s · specs <b>%s</b> · <b>%s</b>" % (sc, "%s/10" % avg if avg != "" else "–",
                                                          esc(data.get("verdict", "?"))))
    if data.get("summary"):
        head.append("<i>%s</i>" % esc(data["summary"]))

    spec_lines = []
    for name, value, s in ordered_specs(data, specs):
        if s is None:
            spec_lines.append("%s %s: <i>%s</i>" % (dot(s), esc(name), esc(value)))
        else:
            spec_lines.append("%s %s: %s · %d/10" % (dot(s), esc(name), esc(value), s))

    tail = []
    if row["deal_breakers_hit"]:
        tail.append("⛔ Deal breaker: " + esc(row["deal_breakers_hit"]))
    if data.get("red_flags"):
        tail.append("🚩 " + esc(" · ".join(data["red_flags"][:3])))
    if data.get("seller_message"):
        tail.append(contact_line(n, data, phone))
    tail.append("📄 listings/%03d.md" % n)
    return "\n".join(head) + "\n\n" + "\n".join(spec_lines) + "\n\n" + "\n".join(tail)


def plain_text(html_text: str) -> str:
    """Telegram HTML as plain text, for the terminal and as the fallback when Telegram rejects the markup."""
    return html.unescape(re.sub(r"</?[biu]>", "", html_text))
