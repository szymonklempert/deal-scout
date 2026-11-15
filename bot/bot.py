#!/usr/bin/env python3
"""Campervan capture bot.

You send screenshots, links and notes to a private Telegram bot. It saves them into inbox/<NNN>/ (one folder per
listing) and answers with a short confirmation. Only the Telegram user in TELEGRAM_ALLOWED_USER_ID is served.
Standard library only. Configuration comes from the environment or a .env file in the project root.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL_RE = re.compile(r"https?://[^\s<>\"']+")
HELP = (
    "Send me screenshots, links and notes about a listing. Everything sent close together lands in one listing.\n"
    "/new  start a new listing now\n"
    "/list  show the latest listings\n"
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
        return self.call("getUpdates", {"offset": offset, "timeout": wait, "allowed_updates": ["message"]}, timeout=wait + 15)

    def send_message(self, chat_id, text):
        self.call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})

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
    def __init__(self, api, root, allowed_user_id, window_seconds=180, clock=time.time):
        self.api = api
        self.inbox = Inbox(root)
        self.state_path = root / ".state.json"
        self.allowed = allowed_user_id
        self.window = window_seconds
        self.clock = clock
        self.state = {"offset": 0, "current": None}
        if self.state_path.exists():
            self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))

    def save_state(self):
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def reply(self, chat_id, text):
        self.api.send_message(chat_id, text)

    def current_listing(self, media_group, force_new=False):
        """Which listing a new message belongs to: the current one if it is recent (or the same album), else a new one."""
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
            return self.handle_command(chat, text.split()[0].split("@")[0].lower())
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
        for file_id in photo_ids:
            self.inbox.add_photo(n, self.api.get_file_bytes(file_id))
        if text:
            self.inbox.add_note(n, text, msg.get("date") or self.clock())
            self.inbox.add_links(n, URL_RE.findall(text))
        photos, links = self.inbox.summary(n)
        if is_new or not msg.get("media_group_id"):
            self.reply(chat, "#%d: %d photo(s), %d link(s) saved." % (n, photos, len(links)))

    def handle_command(self, chat, cmd):
        if cmd in ("/start", "/help"):
            self.reply(chat, HELP)
        elif cmd == "/new":
            self.state["current"] = None
            self.reply(chat, "Next message starts a new listing.")
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

    def run(self):
        print("Campervan bot running. Ctrl+C to stop.", flush=True)
        while True:
            try:
                updates = self.api.get_updates(self.state["offset"])
            except (urllib.error.URLError, OSError, RuntimeError) as exc:
                print("Telegram problem (%s); retrying in 10 s" % exc, flush=True)
                time.sleep(10)
                continue
            for update in updates:
                try:
                    self.handle_update(update)
                except Exception as exc:  # keep running; the update is skipped
                    print("Could not handle update %s: %s" % (update.get("update_id"), exc), flush=True)
                self.state["offset"] = update["update_id"] + 1
                self.save_state()


def main():
    load_env(ROOT / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    allowed = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
    bot = Bot(TelegramAPI(token), ROOT, int(allowed) if allowed else None,
              int(os.environ.get("GROUP_WINDOW_SECONDS", "180")))
    bot.run()


if __name__ == "__main__":
    main()
