#!/usr/bin/env python3
"""deal-scout Telegram bot.

You send screenshots, links and notes to a private Telegram bot. It saves them into inbox/<NNN>/ (one folder per
listing) and answers with a short confirmation. Only the Telegram user in TELEGRAM_ALLOWED_USER_ID is served.
Standard library only. Configuration comes from the environment or a .env file in the project root.
"""
import hashlib
import json
import logging
import logging.handlers
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("dealscout")
log.addHandler(logging.NullHandler())  # quiet unless setup_logging() adds real handlers


def setup_logging(root):
    """Log to the terminal (or systemd journal) and to logs/bot.log (rotated, 3 x 1 MB). Never message contents."""
    (root / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.handlers.RotatingFileHandler(root / "logs" / "bot.log", maxBytes=1_000_000, backupCount=3,
                                                         encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def code_version():
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


def load_env(path):
    """Read KEY=VALUE lines from a .env file into os.environ (without overriding existing values)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class TelegramAPI:
    """The few Telegram Bot API calls we need. Outgoing HTTPS only (long polling): no open ports."""

    def __init__(self, token):
        self.base = "https://api.telegram.org/bot%s/" % token
        self.file_base = "https://api.telegram.org/file/bot%s/" % token

    def call(self, method, params=None, timeout=60):
        data = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(self.base + method, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError("Telegram error in %s: %s" % (method, body.get("description")))
        return body["result"]

    def get_updates(self, offset, wait=30):
        params = {"offset": offset, "timeout": wait, "allowed_updates": ["message", "callback_query"]}
        return self.call("getUpdates", params, timeout=wait + 15)

    def send_message(self, chat_id, text, buttons=None, html=False):
        params = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if html:
            params["parse_mode"] = "HTML"
        if buttons:  # [(label, url), ...] shown as link buttons under the message
            # (label, "https://...") opens a link; (label, "msg:7:...") is a tap the bot answers in handle_callback
            params["reply_markup"] = {"inline_keyboard": [
                [{"text": label, "url": value} if value.startswith("https://") else
                 {"text": label, "callback_data": value[:64]}] for label, value in buttons]}
        self.call("sendMessage", params)

    def answer_callback(self, callback_id):
        self.call("answerCallbackQuery", {"callback_query_id": callback_id})

    def get_file_bytes(self, file_id):
        info = self.call("getFile", {"file_id": file_id})
        with urllib.request.urlopen(self.file_base + info["file_path"], timeout=60) as resp:
            return resp.read()


class Inbox:
    """inbox/<NNN>/ holds photo_01.jpg..., notes.txt, links.txt and meta.json for one listing."""

    def __init__(self, root):
        self.dir = root / "inbox"
        self.dir.mkdir(parents=True, exist_ok=True)

    def next_number(self):
        nums = [int(p.name) for p in self.dir.iterdir() if p.is_dir() and p.name.isdigit()]
        return (max(nums) + 1) if nums else 1

    def folder(self, n):
        path = self.dir / ("%03d" % n)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add_photo(self, n, data, ext=".jpg"):
        folder = self.folder(n)
        count = len(list(folder.glob("photo_*")))
        path = folder / ("photo_%02d%s" % (count + 1, ext))
        path.write_bytes(data)
        return path

    def add_note(self, n, text, when):
        with open(self.folder(n) / "notes.txt", "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(when)), text))

    def add_links(self, n, links):
        path = self.folder(n) / "links.txt"
        known = set(path.read_text(encoding="utf-8").split()) if path.exists() else set()
        new = [u for u in links if u not in known]
        if new:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(new) + "\n")
        return new

    def summary(self, n):
        folder = self.dir / ("%03d" % n)
        photos = len(list(folder.glob("photo_*")))
        links = (folder / "links.txt").read_text(encoding="utf-8").split() if (folder / "links.txt").exists() else []
        return photos, links

    def latest(self, count=10):
        nums = sorted(int(p.name) for p in self.dir.iterdir() if p.is_dir() and p.name.isdigit())
        return nums[-count:]


class Bot:
    def __init__(self, api, root, allowed_user_id, window_seconds=180, clock=time.time, processor=None,
                 auto_analyse=False, contact=None, sheet_link=None, seller_text=None):
        self.api = api
        self.root = root
        self.inbox = Inbox(root)
        self.processor = processor  # callable(n) -> summary text; None means analysis is off
        self.auto_analyse = auto_analyse and processor is not None
        self.contact = contact  # callable(n, phone_text) -> seller messages with a WhatsApp button
        self.sheet_link = sheet_link
        self.seller_text = seller_text  # callable(n, "msg"|"sms", phone) -> messages for a button tap
        self.version = code_version()
        self.started = time.time()
        self.jobs = queue.Queue()  # listing numbers waiting for analysis, handled one at a time by worker()
        self.state_path = root / ".state.json"
        self.allowed = allowed_user_id
        self.window = window_seconds
        self.ack = None  # {"n", "at"}: a "saved" confirmation waiting until the photos stop arriving
        self.clock = clock
        self.state = {"offset": 0, "current": None}
        if self.state_path.exists():
            self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))

    def save_state(self):
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def reply(self, chat_id, text):
        self.api.send_message(chat_id, text)

    def current_listing(self, media_group, force_new=False):
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

    def handle_update(self, update):
        if update.get("callback_query"):
            return self.handle_callback(update["callback_query"])
        msg = update.get("message")
        if not msg:
            return
        user = (msg.get("from") or {}).get("id")
        chat = msg["chat"]["id"]
        if self.allowed is None:
            # Setup mode: tell the person their id, save nothing.
            self.reply(chat, "Setup: your Telegram user ID is %s. Put TELEGRAM_ALLOWED_USER_ID=%s in the .env file and "
                             "restart the bot. Nothing was saved." % (user, user))
            return
        if user != self.allowed:
            return  # strangers get no answer at all
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text.startswith("/"):
            words = text.split()
            return self.handle_command(chat, words[0].split("@")[0].lower(), words[1:])
        photo_ids = []
        if msg.get("photo"):
            photo_ids.append(msg["photo"][-1]["file_id"])  # largest size
        doc = msg.get("document")
        if doc and str(doc.get("mime_type", "")).startswith("image/"):
            photo_ids.append(doc["file_id"])
        if not photo_ids and not text:
            self.reply(chat, "I can save photos, screenshots, links and text.")
            return
        n, is_new = self.current_listing(msg.get("media_group_id"))
        log.info("#%d: saved %d photo(s)%s", n, len(photo_ids), " and text" if text else "")
        for file_id in photo_ids:
            self.inbox.add_photo(n, self.api.get_file_bytes(file_id))
        if text:
            self.inbox.add_note(n, text, msg.get("date") or self.clock())
            self.inbox.add_links(n, URL_RE.findall(text))
        # Confirm once, after the photos stop arriving (an album comes as several messages), with the full count.
        if self.ack and self.ack["n"] != n:
            self.flush_ack(force=True)
        self.ack = {"n": n, "at": self.clock()}

    ACK_DELAY = 2  # seconds of quiet before confirming what was saved

    def flush_ack(self, force=False):
        if self.ack and (force or self.clock() - self.ack["at"] >= self.ACK_DELAY):
            n = self.ack["n"]
            self.ack = None
            photos, links = self.inbox.summary(n)
            self.reply(self.allowed, "#%d: %d photo(s), %d link(s) saved." % (n, photos, len(links)))

    def handle_command(self, chat, cmd, args=()):
        self.flush_ack(force=True)  # confirm what was saved before answering (e.g. before /done starts)
        if cmd in ("/start", "/help"):
            self.reply(chat, HELP)
        elif cmd == "/new":
            self.state["current"] = None
            self.reply(chat, "Next message starts a new listing.")
        elif cmd == "/done":
            self.done(chat, args)
        elif cmd == "/phone":
            self.phone(chat, args)
        elif cmd == "/status":
            self.reply(chat, self.status())
        elif cmd == "/usage":
            import usage
            self.reply(chat, usage.summary(self.root / "logs" / "usage.jsonl"))
        elif cmd == "/sheet":
            if self.sheet_link:
                self.send({"text": "Your listings sheet:", "buttons": [("📊 Open sheet", self.sheet_link)]})
            else:
                self.reply(chat, "No sheet link yet: put the spreadsheet's address in .env as GOOGLE_SHEET_LINK "
                                 "and restart the bot.")
        elif cmd == "/market":
            path = self.root / "listings" / "market.md"
            self.reply(chat, path.read_text(encoding="utf-8")[:4000] if path.exists()
                       else "No market data yet: it is built after the first analysis.")
        elif cmd == "/list":
            nums = self.inbox.latest()
            if not nums:
                self.reply(chat, "Nothing saved yet.")
                return
            lines = []
            for n in nums:
                photos, links = self.inbox.summary(n)
                lines.append("#%d  %d photo(s)  %s" % (n, photos, links[0] if links else "no link"))
            self.reply(chat, "\n".join(lines))
        else:
            self.reply(chat, "Unknown command. /help")

    def done(self, chat, args):
        if self.processor is None:
            self.reply(chat, "Analysis is off. Set ANALYSIS=manual or ANALYSIS=auto in .env and restart.")
            return
        if args and args[0].lstrip("#").isdigit():
            n = int(args[0].lstrip("#"))
            if not (self.inbox.dir / ("%03d" % n)).is_dir():
                self.reply(chat, "There is no #%d." % n)
                return
        elif self.state.get("current"):
            n = self.state["current"]["n"]
            self.state["current"] = None  # whatever comes next is a new listing
        else:
            self.reply(chat, "No open listing. Send screenshots first, or /done 7 to re-analyse #7.")
            return
        self.queue_analysis(n)

    def phone(self, chat, args):
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
            self.reply(chat, "#%d: %s" % (n, exc))
            return
        for item in items:
            self.send(item)

    def handle_callback(self, cb):
        """A tap on a button under a summary: send the message to the seller, or the SMS version."""
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
            self.reply(self.allowed, "#%d: %s" % (n, exc))
            return
        log.info("#%d: sent the %s draft", n, parts[0])
        for item in items:
            self.send(item)

    def status(self):
        now = code_version()
        lines = ["Running since %s, code %s." % (time.strftime("%Y-%m-%d %H:%M", time.localtime(self.started)),
                                                 self.version)]
        if now != self.version:
            lines.append("⚠️ The code changed since the bot started (%s): restart it to use the new version." % now)
        lines.append("Analysis: %s." % ("auto" if self.auto_analyse else "manual (/done)" if self.processor else "off"))
        cur = self.state.get("current")
        lines.append("Open listing: #%d." % cur["n"] if cur else "No open listing.")
        lines.append("Waiting for analysis: %d." % self.jobs.qsize())
        return "\n".join(lines)

    def send(self, item):
        """A plain string, or {"text", "html", "buttons", "fallback_url"} from process.py."""
        if isinstance(item, str):
            self.api.send_message(self.allowed, item)
            return
        try:
            self.api.send_message(self.allowed, item["text"], item.get("buttons"), html=item.get("html", False))
        except RuntimeError as exc:
            if not (item.get("buttons") or item.get("html")):
                raise
            # Telegram refused the formatting or a very long button link: send plain text instead
            log.warning("Telegram refused a formatted message (%s); sending plain text", exc)
            plain = item["text"]
            if item.get("html"):
                import html
                plain = html.unescape(re.sub(r"</?[biu]>", "", plain))
            self.api.send_message(self.allowed, plain)
            if item.get("fallback_url"):
                self.api.send_message(self.allowed, "Open %s and paste the message above." % item["fallback_url"])
            elif any(v.startswith("https://") for _, v in item.get("buttons") or []):
                self.api.send_message(self.allowed, "\n".join("%s: %s" % b for b in item["buttons"]
                                                               if b[1].startswith("https://")))

    def queue_analysis(self, n):
        self.jobs.put(n)
        self.reply(self.allowed, "#%d: analysing, usually about a minute..." % n)

    def tick(self):
        """Called after every poll: confirm what was saved once photos stop arriving; with auto analysis on,
        analyse the open listing once its window has passed."""
        self.flush_ack()
        cur = self.state.get("current")
        if self.auto_analyse and cur and self.clock() - cur["last"] > self.window:
            self.state["current"] = None
            self.save_state()
            self.queue_analysis(cur["n"])

    def run_job(self, n):
        """processor(n) returns one message or a list (the summary, then the draft for the seller, sent separately
        so it can be copied in one go)."""
        log.info("#%d: analysis started", n)
        t0 = time.time()
        try:
            result = self.processor(n)
            log.info("#%d: analysis done in %.0f s", n, time.time() - t0)
        except Exception as exc:  # report it in the chat; the screenshots stay in the inbox
            log.exception("#%d: analysis failed after %.0f s", n, time.time() - t0)
            result = "#%d: analysis failed: %s\nTry again with /done %d" % (n, exc, n)
        for item in [result] if isinstance(result, str) else result:
            self.send(item)

    def worker(self):
        while True:
            n = self.jobs.get()
            try:
                self.run_job(n)
            except Exception as exc:  # e.g. Telegram down while replying; keep the worker alive
                log.exception("Could not report analysis of #%d: %s", n, exc)

    def run(self):
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


def main():
    setup_logging(ROOT)
    load_env(ROOT / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    allowed = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
    mode = os.environ.get("ANALYSIS", "off").strip().lower()
    processor = None
    import process
    if mode in ("manual", "auto"):
        processor = lambda n: process.process_listing(ROOT, n)  # noqa: E731
    bot = Bot(TelegramAPI(token), ROOT, int(allowed) if allowed else None,
              int(os.environ.get("GROUP_WINDOW_SECONDS", "180")), processor=processor, auto_analyse=(mode == "auto"),
              contact=lambda n, phone: process.contact_for(ROOT, n, phone), sheet_link=process.sheet.link(),
              seller_text=lambda n, kind, phone: process.seller_text(ROOT, n, kind, phone))
    import providers
    try:
        engine = "%s, model %s" % (providers.name(), providers.model_for(providers.name()))
    except providers.ProviderError as exc:
        engine = "misconfigured: %s" % exc
    log.info("Domain: %s. Analysis: %s (%s). Google Sheet %s", process.DOMAIN.name, mode if processor else "off",
             engine, "on" if os.environ.get("GOOGLE_SHEET_URL") else "off")
    bot.run()


if __name__ == "__main__":
    main()
