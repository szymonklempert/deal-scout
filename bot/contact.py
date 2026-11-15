"""Phone numbers and the drafts of the first message to a seller."""
import json
import re
import urllib.parse
from pathlib import Path

from errors import ProcessError
from prompts import Listing
from render import Message, esc


def normalise_phone(raw: object) -> str | None:
    """'+48 600-123-456', '0048 600 123 456' or '600123456' -> '48600123456' (the wa.me format), else None."""
    text = str(raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if digits.startswith("00"):
        digits = digits[2:]
    elif len(digits) == 9 and not text.startswith("+"):
        digits = "48" + digits  # a Polish number written without the country code
    return digits if 10 <= len(digits) <= 15 else None


def whatsapp_url(phone: str, text: str) -> str:
    return "https://wa.me/%s?text=%s" % (phone, urllib.parse.quote(text, safe=""))


def pretty_phone(phone: str) -> str:
    """'48600123456' -> '+48 600 123 456' (a form Telegram recognises as a phone number)."""
    if phone.startswith("48") and len(phone) == 11:
        return "+48 %s %s %s" % (phone[2:5], phone[5:8], phone[8:])
    return "+" + phone


def scrub_phone(path: Path, raw: object) -> None:
    """Remove the seller's phone number from the card if the model wrote it there anyway."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < 9 or not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    # optional "+48" / "0048" prefix, an optional "(", then the last 9 digits with any separators between them
    cleaned = re.sub(r"(?:(?:\+|00)?\d{2}[\s\-.]*)?\(?" + r"[\s\-.()]*".join(digits[-9:]), "[phone removed]", text)
    if cleaned != text:
        path.write_text(cleaned, encoding="utf-8")


def contact_messages(data: Listing, phone: str | None) -> list[Message]:
    """The texts for the seller, each a separate Telegram message so it can be copied whole: the full message (with a
    WhatsApp button when the number is known), then the short SMS version. The number lives only in the button."""
    out: list[Message] = []
    full = (data.get("seller_message") or "").strip()
    if full:
        if phone:
            out.append(Message(text=full[:3900], buttons=(("Send on WhatsApp", whatsapp_url(phone, full)),),
                               fallback_url="https://wa.me/%s" % phone))
        else:
            out.append(Message(text=full[:3900]))
    sms = (data.get("seller_sms") or "").strip()
    if sms:
        # Telegram buttons can't open sms: links, so: the text in monospace (one tap copies it) and the number
        # written out (Telegram makes it tappable: call / SMS).
        how = ("Tap the text to copy it, then tap the number and choose SMS." if phone
               else "Tap the text to copy it. No phone number on the screenshots.")
        text = "<code>%s</code>\n\n%s%s" % (esc(sms[:3500]), "📱 %s\n" % pretty_phone(phone) if phone else "", how)
        out.append(Message(text=text, html=True))
    return out


def seller_text(root: Path | str, n: int, kind: str, phone: str | None = None) -> list[Message]:
    """What a message button sends: the full message (with the WhatsApp link when the number is known) or the SMS."""
    path = Path(root) / "listings" / ("%03d.json" % n)
    if not path.exists():
        raise ProcessError("#%d has not been analysed yet" % n)
    items = contact_messages(json.loads(path.read_text(encoding="utf-8")), normalise_phone(phone))
    if kind == "sms":
        items = [i for i in items if i.html]
    else:
        items = items[:1]
    if not items:
        raise ProcessError("#%d has no %s draft; re-analyse it with /done %d" % (n, kind.upper(), n))
    return items


def contact_for(root: Path | str, n: int, raw_phone: str) -> list[Message]:
    """/phone 7 600 123 456: the seller messages of an analysed listing, with a WhatsApp button for that number."""
    path = Path(root) / "listings" / ("%03d.json" % n)
    if not path.exists():
        raise ProcessError("#%d has not been analysed yet; send /done %d first" % (n, n))
    phone = normalise_phone(raw_phone)
    if not phone:
        raise ProcessError("%r doesn't look like a phone number" % raw_phone)
    return contact_messages(json.loads(path.read_text(encoding="utf-8")), phone)
