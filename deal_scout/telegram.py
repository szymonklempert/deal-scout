"""The few Telegram Bot API calls the bot uses: outgoing HTTPS only (long polling, no open ports)."""

import http.client
import json
import time
import urllib.request
from collections.abc import Callable
from typing import Any, cast

DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_TIMEOUT = 20  # seconds per attempt: one stuck download must not hold up the bot for long


class TelegramAPI:
    """The few Telegram Bot API calls we need. Outgoing HTTPS only (long polling): no open ports."""

    def __init__(self, token: str, sleep: Callable[[float], None] = time.sleep) -> None:
        """Build the API and file URLs for a bot token."""
        self.sleep = sleep
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

    @staticmethod
    def _message_params(text: str, buttons: list[tuple[str, str]] | None, html: bool, columns: int) -> dict[str, Any]:
        """Build the text, parse mode and keyboard shared by sendMessage and editMessageText."""
        params: dict[str, Any] = {"text": text, "disable_web_page_preview": True}
        if html:
            params["parse_mode"] = "HTML"
        if buttons:  # [(label, url), ...] shown as link buttons under the message
            # (label, "https://...") opens a link; (label, "msg:7:...") is a tap the bot answers in handle_callback
            keys: list[dict[str, str]] = [
                {"text": label, "url": value}
                if value.startswith("https://")
                else {"text": label, "callback_data": value[:64]}
                for label, value in buttons
            ]
            width = max(1, columns)
            params["reply_markup"] = {"inline_keyboard": [keys[i : i + width] for i in range(0, len(keys), width)]}
        return params

    def send_message(
        self,
        chat_id: int | None,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
        html: bool = False,
        columns: int = 1,
    ) -> None:
        """Send a text, optionally HTML-formatted and with link or callback buttons, `columns` to a row."""
        params = self._message_params(text, buttons, html, columns)
        self.call("sendMessage", {"chat_id": chat_id, **params})

    def edit_message(
        self,
        chat_id: int | None,
        message_id: int,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
        html: bool = False,
        columns: int = 1,
    ) -> None:
        """Replace the text and buttons of a message the bot sent earlier (editMessageText)."""
        params = self._message_params(text, buttons, html, columns)
        self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, **params})

    def answer_callback(self, callback_id: str) -> None:
        """Acknowledge a button tap so Telegram stops its loading spinner."""
        self.call("answerCallbackQuery", {"callback_query_id": callback_id})

    def get_file_bytes(self, file_id: str) -> bytes:
        """Download a file the owner sent, by its Telegram file id, retrying timeouts and dropped connections."""
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                return self.download(file_id)
            except (OSError, http.client.HTTPException):  # timeouts, resets, HTTP 5xx; not Telegram's "ok": false
                if attempt == DOWNLOAD_ATTEMPTS:
                    raise
                self.sleep(attempt)
        raise AssertionError("unreachable")

    def download(self, file_id: str) -> bytes:
        """Fetch a file once: ask Telegram where it is, then download it."""
        info = self.call("getFile", {"file_id": file_id}, timeout=DOWNLOAD_TIMEOUT)
        with urllib.request.urlopen(self.file_base + info["file_path"], timeout=DOWNLOAD_TIMEOUT) as resp:
            return cast(bytes, resp.read())
