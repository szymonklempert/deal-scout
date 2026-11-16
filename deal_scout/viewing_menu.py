"""The viewing checklist: its items, buttons, results and the summary line about what it changed. Pure.

Callback data (always under 64 bytes, never item text): vw:N start or reopen, vc:N:i an item, vf:N finish, vx:N
cancel. viewing.handle() turns taps into FixReply values; the bot shows them.
"""

from collections.abc import Mapping, Sequence
from typing import TypedDict

from deal_scout.prompts import Listing
from deal_scout.render import Button, Message, esc
from deal_scout.replies import score_change

MAX_ITEMS = 15
LABEL_MAX = 45
MAX_PROBLEMS_SHOWN = 3
OK, BAD = "ok", "bad"
ICONS = {OK: "✅", BAD: "❌"}
NEXT_MARK = {"": OK, OK: BAD, BAD: ""}  # ☐ -> ✅ -> ❌ -> ☐


class Viewing(TypedDict):
    """What listings/NNN.json keeps about a viewing: the frozen checklist, the owner's marks and when it began."""

    items: list[str]
    marks: dict[str, str]  # item index -> "ok" or "bad"; an unchecked item has no entry
    started: str


def fallback_checks(data: Listing) -> list[str]:
    """Build a checklist from the open questions and red flags, for listings analysed before viewing_checks existed."""
    items = [f"Verify: {item}" for item in data.get("missing") or []]
    items += [f"Check: {item}" for item in data.get("red_flags") or []]
    return items[:MAX_ITEMS]


def viewing_items(data: Listing) -> list[str]:
    """Return the checklist to freeze for a viewing: the model's own, else the fallback; never more than MAX_ITEMS."""
    items = [str(x).strip() for x in data.get("viewing_checks") or [] if str(x).strip()]
    return items[:MAX_ITEMS] if items else fallback_checks(data)


def toggled(marks: Mapping[str, str], i: int) -> dict[str, str]:
    """Return the marks after a tap on item i: unchecked -> fine -> problem -> unchecked."""
    out = dict(marks)
    new = NEXT_MARK[out.get(str(i), "")]
    if new:
        out[str(i)] = new
    else:
        out.pop(str(i), None)
    return out


def _short(text: str) -> str:
    """Cut an item to fit a button."""
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1] + "…"


def finish_button(n: int) -> Button:
    """Return the button that finishes the viewing of listing n and sends it to re-analysis."""
    return ("✅ Finish viewing", f"vf:{n}")


def mark_icon(viewing: Viewing, i: int) -> str:
    """Return the box in front of item i: ☐ not checked, ✅ fine or ❌ a problem."""
    return ICONS.get(viewing["marks"].get(str(i), ""), "☐")


def checklist(n: int, title: str, viewing: Viewing) -> Message:
    """Build the checklist screen: one button per item with its mark, then Finish and Cancel."""
    buttons: list[Button] = [
        (f"{mark_icon(viewing, i)} {_short(item)}", f"vc:{n}:{i}") for i, item in enumerate(viewing["items"])
    ]
    buttons += [finish_button(n), ("Cancel", f"vx:{n}")]
    text = (
        f"🔍 Viewing #{n} {esc(title)}: tap an item when you've checked it (✅ fine, tap again ❌ a problem, "
        "again to clear). Send photos or notes any time."
    )
    return Message(text, html=True, buttons=tuple(buttons))


def counts(viewing: Viewing) -> tuple[int, int, int]:
    """Return how many items are fine, are problems and are not checked."""
    values = list(viewing["marks"].values())
    fine, bad = values.count(OK), values.count(BAD)
    return fine, bad, len(viewing["items"]) - fine - bad


def final_message(n: int, viewing: Viewing) -> Message:
    """Build the checklist's last screen: the tally, no buttons."""
    fine, bad, unchecked = counts(viewing)
    problem = "problem" if bad == 1 else "problems"
    return Message(f"🔍 Viewing #{n} finished: {fine} fine, {bad} {problem}, {unchecked} not checked.")


def results_text(viewing: Viewing) -> str:
    """Return the results the model reads (viewing_checks.txt): one item per line, in checklist order."""
    words = {"☐": "☐ not checked", "✅": "✅ fine", "❌": "❌ problem"}
    lines = [f"{words[mark_icon(viewing, i)]}: {item}" for i, item in enumerate(viewing["items"])]
    return "\n".join(lines) + "\n"


def problems(viewing: Viewing) -> list[str]:
    """Return the items the owner marked as a problem, in checklist order."""
    return [item for i, item in enumerate(viewing["items"]) if viewing["marks"].get(str(i)) == BAD]


def viewing_note(previous: Listing | None, data: Listing, old_score: object, new_score: object) -> str:
    """Return the summary line for a re-analysis after new viewing findings, or '' without them or an earlier one."""
    if previous is None or (data.get("viewing_seen") or 0) <= (previous.get("viewing_seen") or 0):
        return ""
    found: Sequence[str] = problems(data["viewing"]) if data.get("viewing") else []
    shown = ", ".join(esc(item) for item in found[:MAX_PROBLEMS_SHOWN])
    if len(found) > MAX_PROBLEMS_SHOWN:
        shown += ", …"
    tail = f"problems: {shown}" if found else "no problems found"
    return f"🔍 After the viewing: {score_change(old_score, new_score)} · {tail}"
