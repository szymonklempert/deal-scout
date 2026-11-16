"""The bot's button taps: seller drafts, Feedback, seller replies, viewing checklists and the summary button."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from deal_scout.attach import REPLY_TAPS
from deal_scout.inbox import URL_RE

if TYPE_CHECKING:  # the bot owns its callbacks; importing it here at run time would be a cycle
    from deal_scout.bot import Bot

log = logging.getLogger("dealscout")

FIX_PREFIXES = {
    "fx": "open",
    "fb": "back",
    "fs": "spec",
    "fv": "value",
    "fc": "checks",
    "fk": "check",
    "fm": "mark",
    "fr": "skip",
    "fd": "done",
}  # callback prefix -> corrections.fix action
VIEW_PREFIXES = {"vw": "start", "vc": "toggle", "vf": "finish", "vx": "cancel"}  # callback prefix -> viewing action
REASON_SECONDS = 600  # how long the next plain text message counts as the reason for a correction


@dataclass
class PendingReason:
    """A correction waiting for its optional reason: the next plain text message from the owner."""

    n: int
    name: str
    until: float


class Callbacks:
    """Routes a tap on a button to what it asks for, for one bot. Strangers' taps are ignored."""

    def __init__(self, bot: "Bot") -> None:
        """Bind the bot whose state and senders the taps use."""
        self.bot = bot

    def consume_reason(self, msg: dict[str, Any], text: str) -> bool:
        """Take a plain text message as the reason for the last correction; any message ends the wait.

        Photos, documents, links and commands are not reasons: they are handled as usual.
        """
        pending, self.bot.pending_reason = self.bot.pending_reason, None
        plain = bool(msg.get("text")) and not (msg.get("photo") or msg.get("document"))
        if (
            pending is None
            or self.bot.fixer is None
            or self.bot.clock() > pending.until
            or not plain
            or text.startswith("/")
            or URL_RE.search(text)
        ):
            return False
        try:
            reply = self.bot.fixer(pending.n, "reason", [pending.name, text])
        except Exception as exc:
            self.bot.reply(self.bot.allowed, f"#{pending.n}: {exc}")
            return True
        log.info("#%d: reason noted", pending.n)
        self.bot.show(reply, None)
        return True

    def handle_callback(self, cb: dict[str, Any]) -> None:
        """Answer a tap on a button under a summary: send the message to the seller, or the SMS version."""
        if self.bot.allowed is None or (cb.get("from") or {}).get("id") != self.bot.allowed:
            return  # strangers get no answer at all
        try:
            self.bot.api.answer_callback(cb["id"])  # stops the button's loading spinner
        except Exception as exc:
            log.warning("answerCallbackQuery failed: %s", exc)
        parts = str(cb.get("data") or "").split(":")
        if parts[0] in REPLY_TAPS and len(parts) > 1 and parts[1].isdigit():
            if parts[0] == "sr":
                self.bot.pending_reason = None  # the next messages are the seller's reply
            self.bot.attach.tap(parts[0], int(parts[1]))
            return
        if parts[0] in VIEW_PREFIXES and len(parts) > 1 and parts[1].isdigit():
            self.handle_viewing(cb.get("message"), VIEW_PREFIXES[parts[0]], int(parts[1]), parts[2:])
            return
        if parts[0] == "sh" and len(parts) > 1 and parts[1].isdigit():
            self.bot.commands.run_reports("/show", self.bot.allowed, parts[1:2])
            return
        if parts[0] in FIX_PREFIXES and len(parts) > 1 and parts[1].isdigit():
            self.handle_fix(cb, FIX_PREFIXES[parts[0]], int(parts[1]), parts[2:])
            return
        if len(parts) < 2 or parts[0] not in ("msg", "sms") or not parts[1].isdigit() or self.bot.seller_text is None:
            return
        n = int(parts[1])
        try:
            items = self.bot.seller_text(n, parts[0], parts[2] if len(parts) > 2 else None)
        except Exception as exc:
            self.bot.reply(self.bot.allowed, f"#{n}: {exc}")
            return
        log.info("#%d: sent the %s draft", n, parts[0])
        for item in items:
            self.bot.send(item)

    def handle_viewing(self, tapped: dict[str, Any] | None, action: str, n: int, args: list[str]) -> None:
        """Answer a tap in the 🔍 Viewing checklist (the sender was already checked), or a /done that finishes it."""
        if self.bot.viewer is None:
            return
        self.bot.attach.flush_ack(force=True)
        try:
            reply = self.bot.viewer(n, action, args)
        except Exception as exc:
            self.bot.reply(self.bot.allowed, f"#{n}: {exc}")
            return
        if action == "start":
            self.bot.pending_reason = None  # the next messages are the owner's viewing notes
        self.bot.attach.viewing_tap(action, n)
        self.bot.show(reply, tapped)
        if action == "finish":
            self.bot.request_analysis(n)

    def handle_fix(self, cb: dict[str, Any], action: str, n: int, args: list[str]) -> None:
        """Answer a tap in the 📝 Feedback menu (the sender was already checked): show the next screen."""
        if self.bot.fixer is None:
            return
        if action == "open" and args and args[0].isdigit():
            self.bot.feedback_phone[n] = args[0]  # in memory only, for the summary shown after Done
        if action == "done" and n in self.bot.feedback_phone:
            args = [self.bot.feedback_phone.pop(n)]
        try:
            reply = self.bot.fixer(n, action, args)
        except Exception as exc:
            self.bot.pending_reason = None
            self.bot.reply(self.bot.allowed, f"#{n}: {exc}")
            return
        self.bot.pending_reason = (
            PendingReason(n, reply.ask_reason, self.bot.clock() + REASON_SECONDS) if reply.ask_reason else None
        )
        self.bot.show(reply, cb.get("message"))
