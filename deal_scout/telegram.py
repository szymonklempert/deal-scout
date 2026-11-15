"""The few Telegram Bot API calls the bot uses: outgoing HTTPS only (long polling, no open ports)."""
import json
import urllib.request
from typing import Any, cast


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
