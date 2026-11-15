"""The 📝 Feedback screens: what the owner sees and which buttons they get. Pure; callback data uses indexes.

Callback data (always under 64 bytes, never names): fx:N open, fs:N:i a spec, fv:N:i:<0-10|x> its new score,
fc:N the checks list, fk:N:j a check, fm:N:j:<m|x|u> its new result, fb:N back to the menu, fr:N skip the reason,
fd:N done. process.fix() turns taps into FixReply values; the bot shows them.
"""
from dataclasses import dataclass

from deal_scout.feedback import check_marks, score_word, spec_marks
from deal_scout.prompts import Listing
from deal_scout.render import Button, Message, esc
from deal_scout.scoring import ordered_specs

RESULT_ICON = {"met": "✅", "missed": "❌"}
LABEL_MAX = 40


@dataclass(frozen=True)
class FixReply:
    """What a tap on a correction button should do: the screen to show, and what to send afterwards."""

    message: Message | None = None  # the screen to show
    edit: bool = False  # edit the tapped message (True) or send a new one
    extra: tuple[Message, ...] = ()  # sent after, e.g. the refreshed summary
    ask_reason: str | None = None  # the spec or criterion name whose reason the next text message gives


def _title(n: int, data: Listing) -> str:
    """Return the listing's heading for a screen."""
    return f"#{n} {esc(data.get('title') or 'listing')}"


def _short(text: str) -> str:
    """Cut a criterion to fit a button."""
    return text if len(text) <= LABEL_MAX else text[:LABEL_MAX - 1] + "…"


def menu(n: int, data: Listing, specs: list[str]) -> Message:
    """Build the top screen: every spec with its score (✏️ when changed), the checks, and Done."""
    marks = spec_marks(data)
    buttons: list[Button] = [(f"{'✏️ ' if name in marks else ''}{name} · {score_word(s)}", f"fs:{n}:{i}")
                             for i, (name, _, s) in enumerate(ordered_specs(data, specs))]
    if data.get("criteria_checks"):
        buttons.append(("Criteria checks ▸", f"fc:{n}"))
    buttons.append(("✅ Done", f"fd:{n}"))
    return Message(f"📝 Feedback on {_title(n, data)}: tap what's wrong:", html=True, buttons=tuple(buttons), columns=2)


def spec_screen(n: int, data: Listing, specs: list[str], i: int) -> Message:
    """Build the screen for one spec: the model's score, the current one, and 0-10 / no score buttons."""
    name, value, score = ordered_specs(data, specs)[i]
    mark = spec_marks(data).get(name)
    model = mark["was"] if mark else score
    text = f"{esc(name)}: {esc(value)}\nModel: {f'{model}/10' if model is not None else 'no score'}"
    if mark:
        text += f"; now {f'{score}/10' if score is not None else 'no score'}"
    buttons: list[Button] = [(str(v), f"fv:{n}:{i}:{v}") for v in range(11)]
    buttons += [("No score", f"fv:{n}:{i}:x"), ("◀ Back", f"fb:{n}")]
    return Message(text + ". Pick the right score:", html=True, buttons=tuple(buttons), columns=6)


def checks_screen(n: int, data: Listing) -> Message:
    """List every criteria check with its current result; tap one to change it."""
    buttons: list[Button] = [
        (f"{RESULT_ICON.get(c['result'], '❔')} {_short(c['criterion'])}", f"fk:{n}:{j}")
        for j, c in enumerate(data.get("criteria_checks") or [])]
    buttons.append(("◀ Back", f"fb:{n}"))
    return Message(f"✏️ {_title(n, data)} — which check is wrong?", html=True, buttons=tuple(buttons))


def check_screen(n: int, data: Listing, j: int) -> Message:
    """Build the screen for one check: the model's result, the current one, and Met / Missed / Unknown buttons."""
    check = (data.get("criteria_checks") or [])[j]
    mark = check_marks(data).get(check["criterion"])
    text = f"{esc(check['criterion'])}\nModel: {mark['was'] if mark else check['result']}"
    if mark:
        text += f"; now {check['result']}"
    buttons: tuple[Button, ...] = (("✅ Met", f"fm:{n}:{j}:m"), ("❌ Missed", f"fm:{n}:{j}:x"),
                                   ("❔ Unknown", f"fm:{n}:{j}:u"), ("◀ Back", f"fc:{n}"))
    return Message(text + ". Set:", html=True, buttons=buttons, columns=3)


def confirm(n: int, name: str, old: object, new: object, score_before: object, score_after: object,
            note: str = "") -> Message:
    """After a correction: what changed, the score before and after, and the ask for a short reason."""
    text = (f"✏️ #{n} {esc(name)}: {esc(old)} → {esc(new)} · score {esc(score_before)} → {esc(score_after)}\n"
            "Why? Reply with a short reason, or tap Skip.")
    buttons = (("Skip", f"fr:{n}"), ("◀ Back", f"fb:{n}"), ("✅ Done", f"fd:{n}"))
    return Message(text + note, html=True, buttons=buttons, columns=3)
