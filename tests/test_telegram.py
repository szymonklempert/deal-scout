import unittest

from deal_scout.telegram import DOWNLOAD_ATTEMPTS, TelegramAPI


class SendMessageTest(unittest.TestCase):
    def test_buttons_become_url_or_callback(self):
        api = TelegramAPI("token")
        calls = []
        api.call = lambda method, params=None, timeout=60: calls.append((method, params))  # type: ignore[method-assign]
        api.send_message(1, "hi", [("Open", "https://example.com"), ("Tap", "msg:7:x")])
        method, params = calls[0]
        self.assertEqual(method, "sendMessage")
        keyboard = params["reply_markup"]["inline_keyboard"]
        self.assertEqual(keyboard[0][0], {"text": "Open", "url": "https://example.com"})
        self.assertEqual(keyboard[1][0], {"text": "Tap", "callback_data": "msg:7:x"})

    def fake(self):
        api = TelegramAPI("token")
        calls = []
        api.call = lambda method, params=None, timeout=60: calls.append((method, params))  # type: ignore[method-assign]
        return api, calls

    def test_columns_chunk_the_keyboard_into_rows(self):
        api, calls = self.fake()
        buttons = [(str(i), f"fv:1:{i}") for i in range(5)]
        api.send_message(1, "hi", buttons, columns=2)
        rows = calls[0][1]["reply_markup"]["inline_keyboard"]
        self.assertEqual([len(r) for r in rows], [2, 2, 1])
        api.send_message(1, "hi", buttons)
        self.assertEqual([len(r) for r in calls[1][1]["reply_markup"]["inline_keyboard"]], [1] * 5)

    def test_edit_message_uses_edit_message_text(self):
        api, calls = self.fake()
        api.edit_message(7, 42, "<b>x</b>", [("A", "fb:1"), ("B", "fd:1")], html=True, columns=2)
        method, params = calls[0]
        self.assertEqual(method, "editMessageText")
        self.assertEqual((params["chat_id"], params["message_id"], params["parse_mode"]), (7, 42, "HTML"))
        self.assertEqual(
            params["reply_markup"]["inline_keyboard"],
            [[{"text": "A", "callback_data": "fb:1"}, {"text": "B", "callback_data": "fd:1"}]],
        )


class DownloadTest(unittest.TestCase):
    def api(self, outcomes):
        """A TelegramAPI whose single download attempts follow `outcomes` (an exception or bytes each)."""
        api = TelegramAPI("token", sleep=self.waits.append)
        attempts = iter(outcomes)

        def download(file_id):
            outcome = next(attempts)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        api.download = download  # type: ignore[method-assign]
        return api

    def setUp(self):
        self.waits = []

    def test_a_timeout_is_retried(self):
        api = self.api([TimeoutError("timed out"), ConnectionResetError("reset"), b"IMG"])
        self.assertEqual(api.get_file_bytes("f"), b"IMG")
        self.assertEqual(self.waits, [1, 2])

    def test_gives_up_after_the_last_attempt(self):
        api = self.api([TimeoutError("timed out")] * DOWNLOAD_ATTEMPTS)
        with self.assertRaises(TimeoutError):
            api.get_file_bytes("f")
        self.assertEqual(len(self.waits), DOWNLOAD_ATTEMPTS - 1)

    def test_a_telegram_error_is_not_retried(self):
        api = self.api([RuntimeError("Telegram error in getFile: file is too big"), b"IMG"])
        with self.assertRaises(RuntimeError):
            api.get_file_bytes("f")
        self.assertEqual(self.waits, [])


if __name__ == "__main__":
    unittest.main()
