import unittest

from deal_scout.telegram import TelegramAPI


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


if __name__ == "__main__":
    unittest.main()
