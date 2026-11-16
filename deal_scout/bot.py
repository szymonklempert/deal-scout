"""The bot's conversation logic: grouping messages into listings, confirmations, commands, button taps.

Also the analysis queue and the poll loop. Messages from the private Telegram user in TELEGRAM_ALLOWED_USER_ID are
saved into inbox/<NNN>/ and answered with a short confirmation; everyone else is ignored. The Telegram transport is
injected (see telegram.py) and python3 -m deal_scout wires it up (see __main__.py).
"""

import functools
import hashlib
import json
import logging
import queue
import re
import threading
import time
import urllib.error
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, cast

from deal_scout import digest as digest_mod
from deal_scout import render, usage
from deal_scout import reports as reports_mod
from deal_scout.feedback_menu import FixReply
from deal_scout.inbox import Inbox
from deal_scout.render import Message

log = logging.getLogger("dealscout")
log.addHandler(logging.NullHandler())  # quiet unless __main__ adds real handlers


def code_version() -> str:
    """Fingerprint of the bot's code, to tell when the running bot is older than the files (restart needed)."""
    h = hashlib.sha1()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        h.update(path.read_bytes())
    return h.hexdigest()[:8]


URL_RE = re.compile(r"https?://[^\s<>\"']+")
REPLY_ENDED = "reply mode ended; what you sent is kept. Tap 📨 again to add more."
ANALYSIS_OFF = "Analysis is off. Set ANALYSIS=manual or ANALYSIS=auto in .env and restart."
HELP = (
    "Send me screenshots, links and notes about a listing. Everything sent close together lands in one listing.\n"
    "/new  start a new listing now\n"
    "/done  analyse the listing you just sent (/done 7 re-analyses #7)\n"
    "/phone 7 600 123 456  WhatsApp button for #7's seller message (the number is not saved)\n"
    "/list  show the latest listings\n"
    "/status  is the bot up to date, is anything being analysed\n"
    "/market  asking prices from your own listings\n"
    "/top  the best open listings (/top 10 shows more)\n"
    "/compare 7 12  listings side by side (2 to 4 numbers)\n"
    "/show 7  the summary of #7 again\n"
    "/digest  the morning digest now\n"
    "/sheet  open the Google Sheet\n"
    "/usage  how much Claude the analysis used (7 days, 30 days, all time)\n"
    "/help  this message"
)


def photo_ids(msg: dict[str, Any]) -> list[str]:
    """File ids of the images in a message: the largest photo size, and a document that is an image."""
    ids: list[str] = []
    if msg.get("photo"):
        ids.append(msg["photo"][-1]["file_id"])  # largest size
    doc = msg.get("document")
    if doc and str(doc.get("mime_type", "")).startswith("image/"):
        ids.append(doc["file_id"])
    return ids


class TelegramClient(Protocol):
    """What the bot needs from Telegram: telegram.TelegramAPI in production, a fake in tests."""

    def get_updates(self, offset: int, wait: int = 30) -> list[dict[str, Any]]:
        """Long-poll for new messages and button taps, starting at offset."""

    def send_message(
        self,
        chat_id: int | None,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
        html: bool = False,
        columns: int = 1,
    ) -> None:
        """Send one message, optionally HTML-formatted and with buttons, `columns` to a row."""

    def edit_message(
        self,
        chat_id: int | None,
        message_id: int,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
        html: bool = False,
        columns: int = 1,
    ) -> None:
        """Replace the text and buttons of a message sent earlier."""

    def answer_callback(self, callback_id: str) -> None:
        """Acknowledge a button tap."""

    def get_file_bytes(self, file_id: str) -> bytes:
        """Download a file the owner sent."""


class CurrentListing(TypedDict):
    """The listing that new messages are added to."""

    n: int
    last: float
    media_group: str | None


class BotState(TypedDict):
    """What survives a restart, as saved in .state.json."""

    offset: int
    current: CurrentListing | None
    last_digest: NotRequired[str]  # the date of the last morning digest; missing in files from before the digest


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
REASON_SECONDS = 600  # how long the next plain text message counts as the reason for a correction
ATTACH_SECONDS = 900  # how long reply mode lasts after the last message
REPLY_TAPS = ("sr", "sa", "sc")  # seller replied, re-analyse, cancel


@dataclass
class PendingReason:
    """A correction waiting for its optional reason: the next plain text message from the owner."""

    n: int
    name: str
    until: float


@dataclass
class PendingAck:
    """A "saved" confirmation waiting until the photos stop arriving."""

    n: int
    at: float
    reply: bool = False  # confirms the seller's reply rather than the listing's own photos


@dataclass
class PendingAttach:
    """Reply mode: the owner's messages are the seller's reply to listing n until `until`. Memory only."""

    n: int
    until: float
    saved: int = 0  # messages saved in this reply mode


def reanalyse_button(n: int) -> tuple[str, str]:
    """Return the button that sends listing n to re-analysis."""
    return ("✅ Re-analyse", f"sa:{n}")


class Bot:
    """The bot's brain: saves what the owner sends, answers commands and runs the analysis jobs."""

    def __init__(
        self,
        api: TelegramClient,
        root: Path,
        allowed_user_id: int | None,
        window_seconds: float = 180,
        clock: Callable[[], float] = time.time,
        processor: Callable[[int], list[Message]] | None = None,
        auto_analyse: bool = False,
        contact: Callable[[int, str], list[Message]] | None = None,
        sheet_link: str | None = None,
        seller_text: Callable[[int, str, str | None], list[Message]] | None = None,
        fixer: Callable[[int, str, list[str]], FixReply] | None = None,
        reports: Callable[[str, Sequence[str]], list[Message]] | None = None,
        digest: Callable[..., list[Message]] | None = None,
        digest_time: tuple[int, int] | None = None,
    ) -> None:
        """Wire the bot to its Telegram API, data folder, allowed user and the optional analysis callbacks."""
        self.api = api
        self.root = root
        self.inbox = Inbox(root)
        self.processor = processor  # callable(n) -> messages; None means analysis is off
        self.auto_analyse = auto_analyse and processor is not None
        self.contact = contact  # callable(n, phone_text) -> seller messages with a WhatsApp button
        self.sheet_link = sheet_link
        self.seller_text = seller_text  # callable(n, "msg"|"sms", phone) -> messages for a button tap
        self.fixer = fixer  # callable(n, action, args) -> what to show after a 📝 Feedback tap
        self.reports = reports  # callable(command, args) -> messages for /top, /compare and /show
        self.digest = digest  # callable(save=...) -> the digest's messages (none when there is nothing to say)
        self.digest_time = digest_time  # (hour, minute) of the daily digest in local time; None means off
        self.pending_reason: PendingReason | None = None  # in memory only; a restart forgets it
        self.attach: PendingAttach | None = None  # reply mode, in memory only
        self.feedback_phone: dict[int, str] = {}  # listing -> phone from a Feedback tap (memory only)
        self.version = code_version()
        self.started = time.time()
        # listing numbers waiting for analysis, handled one at a time by worker()
        self.jobs: queue.Queue[int] = queue.Queue()
        self.state_path = root / ".state.json"
        self.allowed = allowed_user_id
        self.window = window_seconds
        self.ack: PendingAck | None = None  # a "saved" confirmation waiting until the photos stop arriving
        self.clock = clock
        self.state: BotState = {"offset": 0, "current": None}
        if self.state_path.exists():
            self.state.update(cast(BotState, json.loads(self.state_path.read_text(encoding="utf-8"))))

    def save_state(self) -> None:
        """Write the poll offset and the open listing to .state.json so they survive a restart."""
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def reply(self, chat_id: int | None, text: str) -> None:
        """Send a plain-text reply to a chat."""
        self.api.send_message(chat_id, text)

    def current_listing(self, media_group: str | None, force_new: bool = False) -> tuple[int, bool]:
        """Which listing a new message belongs to: the current one if recent (or the same album), else a new one."""
        now = self.clock()
        cur = self.state.get("current")
        same_album = bool(cur and media_group and cur.get("media_group") == media_group)
        recent = bool(cur and now - cur["last"] <= self.window)
        if cur and not force_new and (same_album or recent):
            cur["last"] = now
            if media_group:
                cur["media_group"] = media_group
            return cur["n"], False
        n = self.inbox.next_number()
        self.state["current"] = {"n": n, "last": now, "media_group": media_group}
        return n, True

    def handle_update(self, update: dict[str, Any]) -> None:
        """Handle one Telegram update: ignore strangers, run commands, and save photos, notes and links."""
        if update.get("callback_query"):
            self.handle_callback(update["callback_query"])
            return
        msg = update.get("message")
        if not msg:
            return
        user = (msg.get("from") or {}).get("id")
        chat = msg["chat"]["id"]
        if self.allowed is None:
            # Setup mode: tell the person their id, save nothing.
            self.reply(
                chat,
                f"Setup: your Telegram user ID is {user}. Put TELEGRAM_ALLOWED_USER_ID={user} in the .env "
                "file and restart the bot. Nothing was saved.",
            )
            return
        if user != self.allowed:
            return  # strangers get no answer at all
        self.expire_attach()
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if self.consume_reason(msg, text):
            return
        if text.startswith("/"):
            words = text.split()
            self.handle_command(chat, words[0].split("@")[0].lower(), words[1:])
            return
        ids = photo_ids(msg)
        if not ids and not text:
            self.reply(chat, "I can save photos, screenshots, links and text.")
            return
        if self.attach:
            self.save_reply(self.attach, msg, text, ids)
        else:
            self.save_message(msg, text, ids)

    def consume_reason(self, msg: dict[str, Any], text: str) -> bool:
        """Take a plain text message as the reason for the last correction; any message ends the wait.

        Photos, documents, links and commands are not reasons: they are handled as usual.
        """
        pending, self.pending_reason = self.pending_reason, None
        plain = bool(msg.get("text")) and not (msg.get("photo") or msg.get("document"))
        if (
            pending is None
            or self.fixer is None
            or self.clock() > pending.until
            or not plain
            or text.startswith("/")
            or URL_RE.search(text)
        ):
            return False
        try:
            reply = self.fixer(pending.n, "reason", [pending.name, text])
        except Exception as exc:
            self.reply(self.allowed, f"#{pending.n}: {exc}")
            return True
        log.info("#%d: reason noted", pending.n)
        self.show(reply, None)
        return True

    def save_message(self, msg: dict[str, Any], text: str, ids: Sequence[str]) -> None:
        """Save a message's photos, note and links into its listing and queue the "saved" confirmation."""
        n, is_new = self.current_listing(msg.get("media_group_id"))
        failed = self.download_photos(n, ids, self.inbox.add_photo)
        if text:
            self.inbox.add_note(n, text, msg.get("date") or self.clock())
            self.inbox.add_links(n, URL_RE.findall(text))
        log.info("#%d: saved %d photo(s)%s", n, len(ids) - failed, " and text" if text else "")
        # Confirm once, after the photos stop arriving (an album comes as several messages), with the full count.
        self.start_ack(n, reply=False)

    def start_ack(self, n: int, reply: bool) -> None:
        """Queue a "saved" confirmation for listing n, first sending the one still pending for something else."""
        if self.ack and (self.ack.n != n or self.ack.reply != reply):
            self.flush_ack(force=True)
        self.ack = PendingAck(n=n, at=self.clock(), reply=reply)

    def save_reply(self, attach: PendingAttach, msg: dict[str, Any], text: str, ids: Sequence[str]) -> None:
        """Save a message as the seller's reply to the listing in reply mode, and keep the mode open."""
        n = attach.n
        failed = self.download_photos(n, ids, self.inbox.add_reply_photo)
        if text:
            self.inbox.add_reply(n, text, msg.get("date") or self.clock())
        log.info("#%d: saved %d reply photo(s)%s", n, len(ids) - failed, " and text" if text else "")
        attach.until = self.clock() + ATTACH_SECONDS
        attach.saved += 1
        self.start_ack(n, reply=True)

    def download_photos(self, n: int, ids: Sequence[str], save: Callable[[int, bytes], object]) -> int:
        """Download and save each photo of a message; tell the owner about any that failed, and return how many."""
        failed = 0
        for file_id in ids:
            try:
                save(n, self.api.get_file_bytes(file_id))
            except Exception as exc:  # one lost photo must not drop the rest of the message, or go unnoticed
                failed += 1
                log.warning("#%d: a photo could not be downloaded (%s)", n, type(exc).__name__)
        if failed:
            again = "it" if failed == 1 else "them"
            self.reply(
                self.allowed,
                f"#{n}: {failed} photo(s) couldn't be downloaded (Telegram didn't answer in "
                f"time). Please send {again} again.",
            )
        return failed

    ACK_DELAY = 2  # seconds of quiet before confirming what was saved

    def flush_ack(self, force: bool = False) -> None:
        """Send the pending "saved" confirmation once the photos stop arriving, or at once when forced."""
        if self.ack and (force or self.clock() - self.ack.at >= self.ACK_DELAY):
            n, reply = self.ack.n, self.ack.reply
            self.ack = None
            if reply:
                photos, texts = self.inbox.reply_summary(n)
                self.reply(self.allowed, f"📨 #{n}: {photos} photo(s), {texts} text(s) saved as the seller's reply.")
                return
            photos, links = self.inbox.summary(n)
            self.reply(self.allowed, f"#{n}: {photos} photo(s), {len(links)} link(s) saved.")

    def handle_command(self, chat: int | None, cmd: str, args: Sequence[str] = ()) -> None:
        """Answer a slash command from the owner."""
        self.flush_ack(force=True)  # confirm what was saved before answering (e.g. before /done starts)
        cmd, args = reports_mod.split_show_link(cmd, args)  # a tapped "/show_12" is "/show 12"
        if self.leave_reply_mode(cmd, args):
            return
        handlers: dict[str, Callable[[int | None, Sequence[str]], None]] = {
            "/start": self.cmd_help,
            "/help": self.cmd_help,
            "/new": self.cmd_new,
            "/done": self.done,
            "/phone": self.phone,
            "/status": self.cmd_status,
            "/usage": self.cmd_usage,
            "/sheet": self.cmd_sheet,
            "/market": self.cmd_market,
            "/list": self.cmd_list,
            "/top": functools.partial(self.run_reports, "/top"),
            "/compare": functools.partial(self.run_reports, "/compare"),
            "/show": functools.partial(self.run_reports, "/show"),
            "/digest": self.cmd_digest,
        }
        handler = handlers.get(cmd)
        if handler is None:
            self.reply(chat, "Unknown command. /help")
            return
        handler(chat, args)

    def cmd_help(self, chat: int | None, args: Sequence[str]) -> None:
        """Send the list of commands."""
        self.reply(chat, HELP)

    def cmd_new(self, chat: int | None, args: Sequence[str]) -> None:
        """Make the next message start a new listing."""
        self.state["current"] = None
        self.reply(chat, "Next message starts a new listing.")

    def cmd_status(self, chat: int | None, args: Sequence[str]) -> None:
        """Report the bot's version and queue."""
        self.reply(chat, self.status())

    def cmd_usage(self, chat: int | None, args: Sequence[str]) -> None:
        """Report how much Claude the analysis used."""
        self.reply(chat, usage.summary(self.root / "logs" / "usage.jsonl"))

    def cmd_sheet(self, chat: int | None, args: Sequence[str]) -> None:
        """Send the link to the Google Sheet."""
        if self.sheet_link:
            self.send(Message("Your listings sheet:", buttons=(("📊 Open sheet", self.sheet_link),)))
        else:
            self.reply(
                chat,
                "No sheet link yet: put the spreadsheet's address in .env as GOOGLE_SHEET_LINK and restart the bot.",
            )

    def cmd_market(self, chat: int | None, args: Sequence[str]) -> None:
        """Send market.md, cut to Telegram's size limit."""
        path = self.root / "listings" / "market.md"
        self.reply(
            chat,
            path.read_text(encoding="utf-8")[:4000]
            if path.exists()
            else "No market data yet: it is built after the first analysis.",
        )

    def cmd_list(self, chat: int | None, args: Sequence[str]) -> None:
        """List the latest inbox folders with their photo and link counts."""
        nums = self.inbox.latest()
        if not nums:
            self.reply(chat, "Nothing saved yet.")
            return
        lines = []
        for n in nums:
            photos, links = self.inbox.summary(n)
            lines.append(f"#{n}  {photos} photo(s)  {links[0] if links else 'no link'}")
        self.reply(chat, "\n".join(lines))

    def done(self, chat: int | None, args: Sequence[str]) -> None:
        """Queue the analysis of the open listing, or of the listing number given."""
        if self.processor is None:
            self.reply(chat, ANALYSIS_OFF)
            return
        if args and args[0].lstrip("#").isdigit():
            n = int(args[0].lstrip("#"))
            if not (self.inbox.dir / f"{n:03d}").is_dir():
                self.reply(chat, f"There is no #{n}.")
                return
        elif cur := self.state.get("current"):
            n = cur["n"]
            self.state["current"] = None  # whatever comes next is a new listing
        else:
            self.reply(chat, "No open listing. Send screenshots first, or /done 7 to re-analyse #7.")
            return
        self.queue_analysis(n)

    def phone(self, chat: int | None, args: Sequence[str]) -> None:
        """Build the seller message with a WhatsApp button for a listing from a phone number given in chat."""
        if len(args) < 2 or not args[0].lstrip("#").isdigit():
            self.reply(chat, "Usage: /phone 7 600 123 456")
            return
        n = int(args[0].lstrip("#"))
        if self.contact is None:
            self.reply(chat, "Not available.")
            return
        try:
            items = self.contact(n, " ".join(args[1:]))
        except Exception as exc:
            self.reply(chat, f"#{n}: {exc}")
            return
        for item in items:
            self.send(item)

    def cmd_digest(self, chat: int | None, args: Sequence[str]) -> None:
        """Send the morning digest now, without counting it as today's."""
        if self.digest is None:
            self.reply(chat, "Not available.")
            return
        try:
            items = self.digest(save=False)
        except Exception as exc:
            log.exception("digest failed")
            self.reply(chat, f"The digest failed: {exc}")
            return
        if not items:
            self.reply(chat, "Nothing to report today.")
        for item in items:
            self.send(item)

    def send_digest_if_due(self) -> None:
        """Send the day's digest once, the first tick after its time (so a restart later in the day catches up)."""
        now = datetime.fromtimestamp(self.clock())
        if not (
            self.digest and self.digest_time and digest_mod.due(now, self.digest_time, self.state.get("last_digest"))
        ):
            return
        self.state["last_digest"] = now.strftime(digest_mod.DATE_FORMAT)
        self.save_state()  # first, so a failing digest is not retried every tick
        try:
            for item in self.digest(save=True):
                self.send(item)
        except Exception:  # the digest is a bonus: log it and carry on
            log.exception("morning digest failed")

    def run_reports(self, cmd: str, chat: int | None, args: Sequence[str]) -> None:
        """Send what /top, /compare or /show (or a button's sh:N) answer; a problem comes back as one line."""
        if self.reports is None:
            self.reply(chat, "Not available.")
            return
        try:
            items = self.reports(cmd, args)
        except Exception as exc:
            self.reply(chat, str(exc))
            return
        for item in items:
            self.send(item)

    def handle_callback(self, cb: dict[str, Any]) -> None:
        """Answer a tap on a button under a summary: send the message to the seller, or the SMS version."""
        if self.allowed is None or (cb.get("from") or {}).get("id") != self.allowed:
            return  # strangers get no answer at all
        try:
            self.api.answer_callback(cb["id"])  # stops the button's loading spinner
        except Exception as exc:
            log.warning("answerCallbackQuery failed: %s", exc)
        parts = str(cb.get("data") or "").split(":")
        if parts[0] in REPLY_TAPS and len(parts) > 1 and parts[1].isdigit():
            self.handle_reply_tap(parts[0], int(parts[1]))
            return
        if parts[0] == "sh" and len(parts) > 1 and parts[1].isdigit():
            self.run_reports("/show", self.allowed, parts[1:2])
            return
        if parts[0] in FIX_PREFIXES and len(parts) > 1 and parts[1].isdigit():
            self.handle_fix(cb, FIX_PREFIXES[parts[0]], int(parts[1]), parts[2:])
            return
        if len(parts) < 2 or parts[0] not in ("msg", "sms") or not parts[1].isdigit() or self.seller_text is None:
            return
        n = int(parts[1])
        try:
            items = self.seller_text(n, parts[0], parts[2] if len(parts) > 2 else None)
        except Exception as exc:
            self.reply(self.allowed, f"#{n}: {exc}")
            return
        log.info("#%d: sent the %s draft", n, parts[0])
        for item in items:
            self.send(item)

    def handle_reply_tap(self, kind: str, n: int) -> None:
        """Answer a tap on 📨 Seller replied (sr), ✅ Re-analyse (sa) or Cancel (sc); the sender was already checked."""
        if not (self.inbox.dir / f"{n:03d}").is_dir():
            self.reply(self.allowed, f"There is no #{n}.")
            return
        self.flush_ack(force=True)
        if kind == "sr":
            self.enter_reply_mode(n)
            return
        if self.attach and self.attach.n == n:
            self.attach = None
        if kind == "sa":
            self.request_analysis(n)
        else:
            self.reply(self.allowed, f"📨 #{n}: {REPLY_ENDED}")

    def enter_reply_mode(self, n: int) -> None:
        """Make the owner's next messages the seller's reply to listing n."""
        self.pending_reason = None
        self.attach = PendingAttach(n, until=self.clock() + ATTACH_SECONDS)
        self.send(
            Message(
                f"📨 #{n}: send the seller's reply: paste the text or send screenshots. Tap Re-analyse when done.",
                buttons=(reanalyse_button(n), ("Cancel", f"sc:{n}")),
                columns=2,
            )
        )

    def leave_reply_mode(self, cmd: str, args: Sequence[str]) -> bool:
        """End reply mode before a command; True when the command was /done and became the re-analysis."""
        attach, self.attach = self.attach, None
        if attach is None:
            return False
        if cmd == "/done" and not args:
            self.request_analysis(attach.n)
            return True
        self.reply(self.allowed, f"📨 #{attach.n}: {REPLY_ENDED}")
        return False

    def expire_attach(self) -> None:
        """End reply mode after a quiet spell: re-analyse if something was saved and auto analysis is on."""
        attach = self.attach
        if attach is None or self.clock() <= attach.until:
            return
        self.attach = None
        self.flush_ack(force=True)
        if attach.saved and self.auto_analyse:
            self.queue_analysis(attach.n)
            return
        self.send(
            Message(
                f"📨 #{attach.n}: reply mode ended after 15 minutes of quiet. Re-analyse when you're ready:",
                buttons=(reanalyse_button(attach.n),),
            )
        )

    def request_analysis(self, n: int) -> None:
        """Queue listing n for analysis, or say that analysis is off."""
        if self.processor is None:
            self.reply(self.allowed, ANALYSIS_OFF)
            return
        self.queue_analysis(n)

    def handle_fix(self, cb: dict[str, Any], action: str, n: int, args: list[str]) -> None:
        """Answer a tap in the 📝 Feedback menu (the sender was already checked): show the next screen."""
        if self.fixer is None:
            return
        if action == "open" and args and args[0].isdigit():
            self.feedback_phone[n] = args[0]  # in memory only, for the summary shown after Done
        if action == "done" and n in self.feedback_phone:
            args = [self.feedback_phone.pop(n)]
        try:
            reply = self.fixer(n, action, args)
        except Exception as exc:
            self.pending_reason = None
            self.reply(self.allowed, f"#{n}: {exc}")
            return
        self.pending_reason = (
            PendingReason(n, reply.ask_reason, self.clock() + REASON_SECONDS) if reply.ask_reason else None
        )
        self.show(reply, cb.get("message"))

    def show(self, reply: FixReply, tapped: dict[str, Any] | None) -> None:
        """Show a fix reply: edit the tapped message (or send anew), then send the extra messages."""
        if reply.message is not None:
            chat, message_id = ((tapped or {}).get("chat") or {}).get("id"), (tapped or {}).get("message_id")
            if reply.edit and chat is not None and message_id is not None:
                self.edit(chat, message_id, reply.message)
            else:
                self.send(reply.message)
        for item in reply.extra:
            self.send(item)

    def status(self) -> str:
        """Describe the running version, the analysis mode and the queue."""
        now = code_version()
        started = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.started))
        lines = [f"Running since {started}, code {self.version}."]
        if now != self.version:
            lines.append(f"⚠️ The code changed since the bot started ({now}): restart it to use the new version.")
        analysis = "auto" if self.auto_analyse else "manual (/done)" if self.processor else "off"
        lines.append(f"Analysis: {analysis}.")
        cur = self.state.get("current")
        lines.append(f"Open listing: #{cur['n']}." if cur else "No open listing.")
        lines.append(f"Waiting for analysis: {self.jobs.qsize()}.")
        return "\n".join(lines)

    def send(self, item: str | Message) -> None:
        """Send a plain string, or a Message (text, optional HTML formatting, buttons and a fallback link)."""
        if isinstance(item, str):
            self.api.send_message(self.allowed, item)
            return
        try:
            self.api.send_message(
                self.allowed, item.text, list(item.buttons) or None, html=item.html, columns=item.columns
            )
        except RuntimeError as exc:
            self.send_plain_fallback(item, exc)

    def edit(self, chat_id: int | None, message_id: int, item: Message) -> None:
        """Edit a sent message in place; if Telegram refuses (message too old, same text), send it as a new one."""
        try:
            self.api.edit_message(
                chat_id, message_id, item.text, list(item.buttons) or None, html=item.html, columns=item.columns
            )
        except RuntimeError as exc:
            log.warning("Telegram refused a message edit (%s); sending a new message", exc)
            self.send(item)

    def send_plain_fallback(self, item: Message, exc: RuntimeError) -> None:
        """Resend a message Telegram refused as plain text, with its buttons' links as text. Re-raise if it had none."""
        if not (item.buttons or item.html):
            raise exc
        # Telegram refused the formatting or a very long button link: send plain text instead
        log.warning("Telegram refused a formatted message (%s); sending plain text", exc)
        plain = item.text
        if item.html:
            plain = render.plain_text(plain)
        self.api.send_message(self.allowed, plain)
        if item.fallback_url:
            self.api.send_message(self.allowed, f"Open {item.fallback_url} and paste the message above.")
        elif any(v.startswith("https://") for _, v in item.buttons):
            self.api.send_message(
                self.allowed, "\n".join(f"{b[0]}: {b[1]}" for b in item.buttons if b[1].startswith("https://"))
            )

    def queue_analysis(self, n: int) -> None:
        """Put listing n in the analysis queue and tell the owner."""
        self.jobs.put(n)
        self.reply(self.allowed, f"#{n}: analysing, usually about a minute...")

    def tick(self) -> None:
        """Confirm what was saved once photos stop arriving, after every poll.

        With auto analysis on, also analyse the open listing once its window has passed.
        """
        self.flush_ack()
        self.expire_attach()
        self.send_digest_if_due()
        cur = self.state.get("current")
        if self.auto_analyse and cur and self.clock() - cur["last"] > self.window:
            self.state["current"] = None
            self.save_state()
            self.queue_analysis(cur["n"])

    def run_job(self, n: int) -> None:
        """Run the analysis of listing n and send the result, or the failure, to the chat.

        processor(n) returns a list of messages (the summary, then the draft for the seller, sent separately
        so it can be copied in one go); a failure is reported as one plain string.
        """
        log.info("#%d: analysis started", n)
        t0 = time.time()
        result: str | list[Message]
        try:
            assert self.processor is not None  # run_job is only reached when a processor was given
            result = self.processor(n)
            log.info("#%d: analysis done in %.0f s", n, time.time() - t0)
        except Exception as exc:  # report it in the chat; the screenshots stay in the inbox
            log.exception("#%d: analysis failed after %.0f s", n, time.time() - t0)
            result = f"#{n}: analysis failed: {exc}\nTry again with /done {n}"
        for item in [result] if isinstance(result, str) else result:
            self.send(item)

    def worker(self) -> None:
        """Run queued analysis jobs one at a time, forever; meant for a background thread."""
        while True:
            n = self.jobs.get()
            try:
                self.run_job(n)
            except Exception as exc:  # e.g. Telegram down while replying; keep the worker alive
                log.exception("Could not report analysis of #%d: %s", n, exc)

    def run(self) -> None:
        """Poll Telegram forever, handling each update and then the periodic tick."""
        log.info("deal-scout running, code %s. Ctrl+C to stop.", self.version)
        if self.processor is not None:
            threading.Thread(target=self.worker, daemon=True).start()
        while True:
            try:
                # a short poll while a confirmation is pending, so it goes out ~2 s after the last photo
                updates = self.api.get_updates(self.state["offset"], wait=2 if self.ack else 30)
            except (urllib.error.URLError, OSError, RuntimeError) as exc:
                log.warning("Telegram problem (%s); retrying in 10 s", exc)
                time.sleep(10)
                continue
            for update in updates:
                try:
                    self.handle_update(update)
                except Exception as exc:  # keep running; the update is skipped
                    log.exception("Could not handle update %s: %s", update.get("update_id"), exc)
                self.state["offset"] = update["update_id"] + 1
                self.save_state()
            self.tick()
