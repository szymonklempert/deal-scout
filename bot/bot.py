#!/usr/bin/env python3
"""deal-scout Telegram bot.

You send screenshots, links and notes to a private Telegram bot. It saves them into inbox/<NNN>/ (one folder per
listing) and answers with a short confirmation. Only the Telegram user in TELEGRAM_ALLOWED_USER_ID is served.
Standard library only. Configuration comes from the environment or a .env file in the project root.
"""
import functools
import hashlib
import json
import logging
import logging.handlers
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast

import contact
import domain
import process
import providers
import render
import sheet
import usage
from config import ROOT, Settings, load_env
from render import Message

log = logging.getLogger("dealscout")
log.addHandler(logging.NullHandler())  # quiet unless setup_logging() adds real handlers


def setup_logging(root: Path) -> None:
    """Log to the terminal (or systemd journal) and to logs/bot.log (rotated, 3 x 1 MB). Never message contents."""
    (root / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.handlers.RotatingFileHandler(root / "logs" / "bot.log", maxBytes=1_000_000, backupCount=3,
                                                         encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def code_version() -> str:
    """Fingerprint of the bot's code, to tell when the running bot is older than the files (restart needed)."""
    h = hashlib.sha1()
    for path in sorted((ROOT / "bot").glob("*.py")):
        h.update(path.read_bytes())
    return h.hexdigest()[:8]
URL_RE = re.compile(r"https?://[^\s<>\"']+")
HELP = (
    "Send me screenshots, links and notes about a listing. Everything sent close together lands in one listing.\n"
    "/new  start a new listing now\n"
    "/done  analyse the listing you just sent (/done 7 re-analyses #7)\n"
    "/phone 7 600 123 456  WhatsApp button for #7's seller message (the number is not saved)\n"
    "/list  show the latest listings\n"
    "/status  is the bot up to date, is anything being analysed\n"
    "/market  asking prices from your own listings\n"
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


class TelegramAPI:
    """The few Telegram Bot API calls we need. Outgoing HTTPS only (long polling): no open ports."""

    def __init__(self, token: str) -> None:
        """Build the API and file URLs for a bot token."""
        self.base = f"https://api.telegram.org/bot{token}/"
        self.file_base = f"https://api.telegram.org/file/bot{token}/"

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 60) -> Any:
        """Call one Bot API method and return its result, raising RuntimeError if Telegram says it failed."""
        data = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(self.base + method, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"Telegram error in {method}: {body.get('description')}")
        return body["result"]

    def get_updates(self, offset: int, wait: int = 30) -> list[dict[str, Any]]:
        """Long-poll for new messages and button taps, starting at offset."""
        params = {"offset": offset, "timeout": wait, "allowed_updates": ["message", "callback_query"]}
        return cast(list[dict[str, Any]], self.call("getUpdates", params, timeout=wait + 15))

    def send_message(self, chat_id: int | None, text: str, buttons: list[tuple[str, str]] | None = None,
                     html: bool = False) -> None:
        """Send a text, optionally HTML-formatted and with link or callback buttons under it."""
        params: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if html:
            params["parse_mode"] = "HTML"
        if buttons:  # [(label, url), ...] shown as link buttons under the message
            # (label, "https://...") opens a link; (label, "msg:7:...") is a tap the bot answers in handle_callback
            params["reply_markup"] = {"inline_keyboard": [
                [{"text": label, "url": value} if value.startswith("https://") else
                 {"text": label, "callback_data": value[:64]}] for label, value in buttons]}
        self.call("sendMessage", params)

    def answer_callback(self, callback_id: str) -> None:
        """Acknowledge a button tap so Telegram stops its loading spinner."""
        self.call("answerCallbackQuery", {"callback_query_id": callback_id})

    def get_file_bytes(self, file_id: str) -> bytes:
        """Download a file the owner sent, by its Telegram file id."""
        info = self.call("getFile", {"file_id": file_id})
        with urllib.request.urlopen(self.file_base + info["file_path"], timeout=60) as resp:
            return cast(bytes, resp.read())


class Inbox:
    """inbox/<NNN>/ holds photo_01.jpg..., notes.txt, links.txt and meta.json for one listing."""

    def __init__(self, root: Path) -> None:
        """Open (and create if needed) the inbox folder under the project root."""
        self.dir = root / "inbox"
        self.dir.mkdir(parents=True, exist_ok=True)

    def next_number(self) -> int:
        """Return the number the next new listing gets."""
        nums = [int(p.name) for p in self.dir.iterdir() if p.is_dir() and p.name.isdigit()]
        return (max(nums) + 1) if nums else 1

    def folder(self, n: int) -> Path:
        """Return listing n's folder, creating it if needed."""
        path = self.dir / f"{n:03d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add_photo(self, n: int, data: bytes, ext: str = ".jpg") -> Path:
        """Save an image as the listing's next photo_NN file and return its path."""
        folder = self.folder(n)
        count = len(list(folder.glob("photo_*")))
        path = folder / f"photo_{count + 1:02d}{ext}"
        path.write_bytes(data)
        return path

    def add_note(self, n: int, text: str, when: float) -> None:
        """Append a timestamped note to the listing's notes.txt."""
        with open(self.folder(n) / "notes.txt", "a", encoding="utf-8") as f:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(when))
            f.write(f"[{stamp}] {text}\n")

    def add_links(self, n: int, links: Sequence[str]) -> list[str]:
        """Append the links not seen before to links.txt and return the new ones."""
        path = self.folder(n) / "links.txt"
        known = set(path.read_text(encoding="utf-8").split()) if path.exists() else set()
        new = [u for u in links if u not in known]
        if new:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(new) + "\n")
        return new

    def summary(self, n: int) -> tuple[int, list[str]]:
        """Return the listing's photo count and its saved links."""
        folder = self.dir / f"{n:03d}"
        photos = len(list(folder.glob("photo_*")))
        links = (folder / "links.txt").read_text(encoding="utf-8").split() if (folder / "links.txt").exists() else []
        return photos, links

    def latest(self, count: int = 10) -> list[int]:
        """Return the numbers of the most recent listings, oldest first."""
        nums = sorted(int(p.name) for p in self.dir.iterdir() if p.is_dir() and p.name.isdigit())
        return nums[-count:]


class CurrentListing(TypedDict):
    """The listing that new messages are added to."""

    n: int
    last: float
    media_group: str | None


class BotState(TypedDict):
    """What survives a restart, as saved in .state.json."""

    offset: int
    current: CurrentListing | None


@dataclass
class PendingAck:
    """A "saved" confirmation waiting until the photos stop arriving."""

    n: int
    at: float


class Bot:
    """The bot's brain: saves what the owner sends, answers commands and runs the analysis jobs."""

    def __init__(self, api: TelegramAPI, root: Path, allowed_user_id: int | None, window_seconds: float = 180,
                 clock: Callable[[], float] = time.time,
                 processor: Callable[[int], list[Message]] | None = None, auto_analyse: bool = False,
                 contact: Callable[[int, str], list[Message]] | None = None, sheet_link: str | None = None,
                 seller_text: Callable[[int, str, str | None], list[Message]] | None = None) -> None:
        """Wire the bot to its Telegram API, data folder, allowed user and the optional analysis callbacks."""
        self.api = api
        self.root = root
        self.inbox = Inbox(root)
        self.processor = processor  # callable(n) -> messages; None means analysis is off
        self.auto_analyse = auto_analyse and processor is not None
        self.contact = contact  # callable(n, phone_text) -> seller messages with a WhatsApp button
        self.sheet_link = sheet_link
        self.seller_text = seller_text  # callable(n, "msg"|"sms", phone) -> messages for a button tap
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
            self.reply(chat, f"Setup: your Telegram user ID is {user}. Put TELEGRAM_ALLOWED_USER_ID={user} in the .env "
                             "file and restart the bot. Nothing was saved.")
            return
        if user != self.allowed:
            return  # strangers get no answer at all
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text.startswith("/"):
            words = text.split()
            self.handle_command(chat, words[0].split("@")[0].lower(), words[1:])
            return
        ids = photo_ids(msg)
        if not ids and not text:
            self.reply(chat, "I can save photos, screenshots, links and text.")
            return
        self.save_message(msg, text, ids)

    def save_message(self, msg: dict[str, Any], text: str, ids: Sequence[str]) -> None:
        """Save a message's photos, note and links into its listing and queue the "saved" confirmation."""
        n, is_new = self.current_listing(msg.get("media_group_id"))
        log.info("#%d: saved %d photo(s)%s", n, len(ids), " and text" if text else "")
        for file_id in ids:
            self.inbox.add_photo(n, self.api.get_file_bytes(file_id))
        if text:
            self.inbox.add_note(n, text, msg.get("date") or self.clock())
            self.inbox.add_links(n, URL_RE.findall(text))
        # Confirm once, after the photos stop arriving (an album comes as several messages), with the full count.
        if self.ack and self.ack.n != n:
            self.flush_ack(force=True)
        self.ack = PendingAck(n=n, at=self.clock())

    ACK_DELAY = 2  # seconds of quiet before confirming what was saved

    def flush_ack(self, force: bool = False) -> None:
        """Send the pending "saved" confirmation once the photos stop arriving, or at once when forced."""
        if self.ack and (force or self.clock() - self.ack.at >= self.ACK_DELAY):
            n = self.ack.n
            self.ack = None
            photos, links = self.inbox.summary(n)
            self.reply(self.allowed, f"#{n}: {photos} photo(s), {len(links)} link(s) saved.")

    def handle_command(self, chat: int | None, cmd: str, args: Sequence[str] = ()) -> None:
        """Answer a slash command from the owner."""
        self.flush_ack(force=True)  # confirm what was saved before answering (e.g. before /done starts)
        handlers: dict[str, Callable[[int | None, Sequence[str]], None]] = {
            "/start": self.cmd_help, "/help": self.cmd_help, "/new": self.cmd_new, "/done": self.done,
            "/phone": self.phone, "/status": self.cmd_status, "/usage": self.cmd_usage, "/sheet": self.cmd_sheet,
            "/market": self.cmd_market, "/list": self.cmd_list}
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
            self.reply(chat, "No sheet link yet: put the spreadsheet's address in .env as GOOGLE_SHEET_LINK "
                             "and restart the bot.")

    def cmd_market(self, chat: int | None, args: Sequence[str]) -> None:
        """Send market.md, cut to Telegram's size limit."""
        path = self.root / "listings" / "market.md"
        self.reply(chat, path.read_text(encoding="utf-8")[:4000] if path.exists()
                   else "No market data yet: it is built after the first analysis.")

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
            self.reply(chat, "Analysis is off. Set ANALYSIS=manual or ANALYSIS=auto in .env and restart.")
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

    def handle_callback(self, cb: dict[str, Any]) -> None:
        """Answer a tap on a button under a summary: send the message to the seller, or the SMS version."""
        if self.allowed is None or (cb.get("from") or {}).get("id") != self.allowed:
            return  # strangers get no answer at all
        try:
            self.api.answer_callback(cb["id"])  # stops the button's loading spinner
        except Exception as exc:
            log.warning("answerCallbackQuery failed: %s", exc)
        parts = str(cb.get("data") or "").split(":")
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
            self.api.send_message(self.allowed, item.text, list(item.buttons) or None, html=item.html)
        except RuntimeError as exc:
            self.send_plain_fallback(item, exc)

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
            self.api.send_message(self.allowed, "\n".join(f"{b[0]}: {b[1]}" for b in item.buttons
                                                           if b[1].startswith("https://")))

    def queue_analysis(self, n: int) -> None:
        """Put listing n in the analysis queue and tell the owner."""
        self.jobs.put(n)
        self.reply(self.allowed, f"#{n}: analysing, usually about a minute...")

    def tick(self) -> None:
        """Confirm what was saved once photos stop arriving, after every poll.

        With auto analysis on, also analyse the open listing once its window has passed.
        """
        self.flush_ack()
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


def main() -> None:
    """Start the bot: read the settings once, wire the analysis and contact helpers in, and poll Telegram."""
    setup_logging(ROOT)
    load_env(ROOT / ".env")
    settings = Settings.from_env()
    if not settings.telegram_token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    mode = settings.analysis
    try:
        d = domain.load(settings.domain)
    except domain.DomainError as exc:
        sys.exit(str(exc))

    processor = functools.partial(process.process_listing, ROOT, settings=settings, domain=d)
    analyse = mode in ("manual", "auto")
    bot = Bot(TelegramAPI(settings.telegram_token), ROOT, settings.allowed_user_id, settings.group_window_seconds,
              processor=processor if analyse else None, auto_analyse=(mode == "auto"),
              contact=functools.partial(contact.contact_for, ROOT), sheet_link=sheet.link(settings),
              seller_text=functools.partial(contact.seller_text, ROOT))
    try:
        provider = providers.name(settings)
        engine = f"{provider}, model {providers.model_for(provider, settings)}"
    except providers.ProviderError as exc:
        engine = f"misconfigured: {exc}"
    log.info("Domain: %s. Analysis: %s (%s). Google Sheet %s", d.name, mode if analyse else "off",
             engine, "on" if settings.sheet_url else "off")
    bot.run()


if __name__ == "__main__":
    main()
