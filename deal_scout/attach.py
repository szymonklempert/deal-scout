"""Attach mode: the owner's next messages belong to an existing listing (the seller's reply, or a viewing).

Also the debounced "saved" confirmation, which every kind of saved message shares. The bot routes messages and taps
here; Telegram, the inbox and the clock are injected. Standard library only.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from deal_scout.inbox import Inbox
from deal_scout.render import Message
from deal_scout.viewing_menu import finish_button

log = logging.getLogger("dealscout")

Kind = Literal["reply", "viewing"]
AckKind = Literal["listing", "reply", "viewing"]

REPLY_ENDED = "reply mode ended; what you sent is kept. Tap 📨 again to add more."
ANALYSIS_OFF = "Analysis is off. Set ANALYSIS=manual or ANALYSIS=auto in .env and restart."
REPLY_SECONDS = 900  # how long reply mode lasts after the last message
VIEWING_SECONDS = 7200  # how long viewing mode lasts after the last message
ACK_DELAY = 2  # seconds of quiet before confirming what was saved
REPLY_TAPS = ("sr", "sa", "sc")  # seller replied, re-analyse, cancel


@dataclass
class PendingAttach:
    """Attach mode: the owner's messages go to listing n of this kind until `until`. Memory only."""

    n: int
    until: float
    kind: Kind = "reply"
    saved: int = 0  # messages saved in this mode


@dataclass
class PendingAck:
    """A "saved" confirmation waiting until the photos stop arriving."""

    n: int
    at: float
    kind: AckKind = "listing"  # what was saved: the listing's own photos, the seller's reply or a viewing


def reanalyse_button(n: int) -> tuple[str, str]:
    """Return the button that sends listing n to re-analysis."""
    return ("✅ Re-analyse", f"sa:{n}")


class Attach:
    """Owns attach mode and the pending confirmation; the bot asks it what to do with messages and taps."""

    def __init__(
        self,
        inbox: Inbox,
        clock: Callable[[], float],
        fetch: Callable[[str], bytes],
        reply: Callable[[str], None],
        send: Callable[[str | Message], None],
        request_analysis: Callable[[int], None],
        queue_analysis: Callable[[int], None],
        auto_analyse: bool,
    ) -> None:
        """Wire the helper to the inbox, the clock, Telegram's file download and the bot's reply and queue calls."""
        self.inbox = inbox
        self.clock = clock
        self.fetch = fetch
        self.reply = reply
        self.send = send
        self.request_analysis = request_analysis
        self.queue_analysis = queue_analysis
        self.auto_analyse = auto_analyse
        self.pending: PendingAttach | None = None
        self.ack: PendingAck | None = None

    def download_photos(self, n: int, ids: Sequence[str], save: Callable[[int, bytes], object]) -> int:
        """Download and save each photo of a message; tell the owner about any that failed, and return how many."""
        failed = 0
        for file_id in ids:
            try:
                save(n, self.fetch(file_id))
            except Exception as exc:  # one lost photo must not drop the rest of the message, or go unnoticed
                failed += 1
                log.warning("#%d: a photo could not be downloaded (%s)", n, type(exc).__name__)
        if failed:
            again = "it" if failed == 1 else "them"
            self.reply(
                f"#{n}: {failed} photo(s) couldn't be downloaded (Telegram didn't answer in "
                f"time). Please send {again} again."
            )
        return failed

    def start_ack(self, n: int, kind: AckKind) -> None:
        """Queue a "saved" confirmation for listing n, first sending the one still pending for something else."""
        if self.ack and (self.ack.n != n or self.ack.kind != kind):
            self.flush_ack(force=True)
        self.ack = PendingAck(n=n, at=self.clock(), kind=kind)

    def flush_ack(self, force: bool = False) -> None:
        """Send the pending "saved" confirmation once the photos stop arriving, or at once when forced."""
        if not (self.ack and (force or self.clock() - self.ack.at >= ACK_DELAY)):
            return
        n, kind = self.ack.n, self.ack.kind
        self.ack = None
        if kind == "reply":
            photos, texts = self.inbox.reply_summary(n)
            self.reply(f"📨 #{n}: {photos} photo(s), {texts} text(s) saved as the seller's reply.")
            return
        if kind == "viewing":
            photos, notes = self.inbox.viewing_summary(n)
            self.reply(f"🔍 #{n}: {photos} photo(s), {notes} note(s) saved from the viewing.")
            return
        photos, links = self.inbox.summary(n)
        self.reply(f"#{n}: {photos} photo(s), {len(links)} link(s) saved.")

    def save(self, msg: dict[str, Any], text: str, ids: Sequence[str]) -> None:
        """Save a message into the listing of the open attach mode, and keep the mode open."""
        attach = self.pending
        assert attach is not None  # the bot only calls this while a mode is open
        n = attach.n
        viewing = attach.kind == "viewing"
        failed = self.download_photos(n, ids, self.inbox.add_viewing_photo if viewing else self.inbox.add_reply_photo)
        if text:
            add_text = self.inbox.add_viewing_note if viewing else self.inbox.add_reply
            add_text(n, text, msg.get("date") or self.clock())
        log.info("#%d: saved %d %s photo(s)%s", n, len(ids) - failed, attach.kind, " and text" if text else "")
        attach.until = self.clock() + (VIEWING_SECONDS if viewing else REPLY_SECONDS)
        attach.saved += 1
        self.start_ack(n, attach.kind)

    def tap(self, action: str, n: int) -> None:
        """Answer a tap on 📨 Seller replied (sr), ✅ Re-analyse (sa) or Cancel (sc); the sender was already checked."""
        if not (self.inbox.dir / f"{n:03d}").is_dir():
            self.reply(f"There is no #{n}.")
            return
        self.flush_ack(force=True)
        if action == "sr":
            self.enter_reply_mode(n)
            return
        if self.pending and self.pending.n == n:
            self.pending = None
        if action == "sa":
            self.request_analysis(n)
        else:
            self.reply(f"📨 #{n}: {REPLY_ENDED}")

    def enter_reply_mode(self, n: int) -> None:
        """Make the owner's next messages the seller's reply to listing n."""
        self.pending = PendingAttach(n, until=self.clock() + REPLY_SECONDS)
        self.send(
            Message(
                f"📨 #{n}: send the seller's reply: paste the text or send screenshots. Tap Re-analyse when done.",
                buttons=(reanalyse_button(n), ("Cancel", f"sc:{n}")),
                columns=2,
            )
        )

    def leave_for_command(self, cmd: str, args: Sequence[str]) -> bool:
        """End attach mode before a command; True when the command was /done and became the re-analysis."""
        attach = self.pending
        if attach is None or attach.kind == "viewing":
            return False  # a viewing lasts through other commands; /done there is the bot's call (it finishes it)
        self.pending = None
        if cmd == "/done" and not args:
            self.request_analysis(attach.n)
            return True
        self.reply(f"📨 #{attach.n}: {REPLY_ENDED}")
        return False

    def expire(self) -> None:
        """End attach mode after a quiet spell: re-analyse if something was saved and auto analysis is on."""
        attach = self.pending
        if attach is None or self.clock() <= attach.until:
            return
        self.pending = None
        self.flush_ack(force=True)
        if attach.kind == "viewing":
            self.send(
                Message(
                    f"🔍 #{attach.n}: viewing mode ended after 2 hours of quiet. Finish when you're ready:",
                    buttons=(finish_button(attach.n),),
                )
            )
            return
        if attach.saved and self.auto_analyse:
            self.queue_analysis(attach.n)
            return
        self.send(
            Message(
                f"📨 #{attach.n}: reply mode ended after 15 minutes of quiet. Re-analyse when you're ready:",
                buttons=(reanalyse_button(attach.n),),
            )
        )

    def viewing_tap(self, action: str, n: int) -> None:
        """Follow a tap in the viewing checklist: "start" opens viewing mode for listing n, finish or cancel end it."""
        if action == "start":
            self.pending = PendingAttach(n, until=self.clock() + VIEWING_SECONDS, kind="viewing")
        elif action in ("finish", "cancel") and self.pending and self.pending.kind == "viewing" and self.pending.n == n:
            self.pending = None
