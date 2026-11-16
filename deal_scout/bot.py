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
import threading
import time
import urllib.error
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, cast

from deal_scout import digest as digest_mod
from deal_scout import render
from deal_scout.attach import ANALYSIS_OFF, Attach
from deal_scout.callbacks import Callbacks, PendingReason
from deal_scout.commands import Commands
from deal_scout.feedback_menu import FixReply
from deal_scout.inbox import URL_RE, Inbox
from deal_scout.render import Message

log = logging.getLogger("dealscout")
log.addHandler(logging.NullHandler())  # quiet unless __main__ adds real handlers


def code_version() -> str:
    """Fingerprint of the bot's code, to tell when the running bot is older than the files (restart needed)."""
    h = hashlib.sha1()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        h.update(path.read_bytes())
    return h.hexdigest()[:8]


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
        viewer: Callable[[int, str, list[str]], FixReply] | None = None,
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
        self.viewer = viewer  # callable(n, action, args) -> what to show after a 🔍 Viewing tap
        self.pending_reason: PendingReason | None = None  # in memory only; a restart forgets it
        self.feedback_phone: dict[int, str] = {}  # listing -> phone from a Feedback tap (memory only)
        self.version = code_version()
        self.started = time.time()
        # listing numbers waiting for analysis, handled one at a time by worker()
        self.jobs: queue.Queue[int] = queue.Queue()
        self.state_path = root / ".state.json"
        self.allowed = allowed_user_id
        self.window = window_seconds
        self.clock = clock
        self.attach = Attach(  # reply mode and the "saved" confirmation
            self.inbox,
            clock,
            api.get_file_bytes,
            functools.partial(self.reply, allowed_user_id),
            self.send,
            self.request_analysis,
            self.queue_analysis,
            self.auto_analyse,
        )
        self.commands = Commands(self)
        self.callbacks = Callbacks(self)
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
            self.callbacks.handle_callback(update["callback_query"])
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
        self.attach.expire()
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if self.callbacks.consume_reason(msg, text):
            return
        if text.startswith("/"):
            words = text.split()
            self.commands.handle(chat, words[0].split("@")[0].lower(), words[1:])
            return
        ids = photo_ids(msg)
        if not ids and not text:
            self.reply(chat, "I can save photos, screenshots, links and text.")
            return
        if self.attach.pending:
            self.attach.save(msg, text, ids)
        else:
            self.save_message(msg, text, ids)

    def save_message(self, msg: dict[str, Any], text: str, ids: Sequence[str]) -> None:
        """Save a message's photos, note and links into its listing and queue the "saved" confirmation."""
        n, is_new = self.current_listing(msg.get("media_group_id"))
        failed = self.attach.download_photos(n, ids, self.inbox.add_photo)
        if text:
            self.inbox.add_note(n, text, msg.get("date") or self.clock())
            self.inbox.add_links(n, URL_RE.findall(text))
        log.info("#%d: saved %d photo(s)%s", n, len(ids) - failed, " and text" if text else "")
        # Confirm once, after the photos stop arriving (an album comes as several messages), with the full count.
        self.attach.start_ack(n, "listing")

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

    def request_analysis(self, n: int) -> None:
        """Queue listing n for analysis, or say that analysis is off."""
        if self.processor is None:
            self.reply(self.allowed, ANALYSIS_OFF)
            return
        self.queue_analysis(n)

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
        self.attach.flush_ack()
        self.attach.expire()
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
                updates = self.api.get_updates(self.state["offset"], wait=2 if self.attach.ack else 30)
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
