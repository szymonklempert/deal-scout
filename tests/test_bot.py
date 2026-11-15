import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))
import bot as botmod  # noqa: E402


class FakeAPI:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append(text)

    def get_file_bytes(self, file_id):
        return b"IMG:" + file_id.encode()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def msg(user=1, text=None, photo=None, group=None, caption=None):
    m = {"from": {"id": user}, "chat": {"id": user}, "date": 1000}
    if text is not None:
        m["text"] = text
    if caption is not None:
        m["caption"] = caption
    if photo:
        m["photo"] = [{"file_id": photo + "-small"}, {"file_id": photo}]
    if group:
        m["media_group_id"] = group
    return {"update_id": 1, "message": m}


class BotTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.api = FakeAPI()
        self.clock = Clock()
        self.bot = botmod.Bot(self.api, self.root, 1, window_seconds=180, clock=self.clock)

    def tearDown(self):
        self.dir.cleanup()

    def test_stranger_gets_nothing(self):
        self.bot.handle_update(msg(user=99, text="hi"))
        self.assertEqual(self.api.sent, [])
        self.assertEqual(list((self.root / "inbox").iterdir()), [])

    def test_setup_mode_reveals_id_and_saves_nothing(self):
        bot = botmod.Bot(self.api, self.root, None, clock=self.clock)
        bot.handle_update(msg(user=42, text="hi"))
        self.assertIn("42", self.api.sent[0])
        self.assertEqual(list((self.root / "inbox").iterdir()), [])

    def test_photo_and_link_group_into_one_listing(self):
        self.bot.handle_update(msg(photo="a"))
        self.clock.t += 20
        self.bot.handle_update(msg(text="https://www.facebook.com/marketplace/item/123 nice van"))
        folders = sorted(p.name for p in (self.root / "inbox").iterdir())
        self.assertEqual(folders, ["001"])
        self.assertEqual(len(list((self.root / "inbox/001").glob("photo_*"))), 1)
        self.assertIn("https://www.facebook.com/marketplace/item/123",
                      (self.root / "inbox/001/links.txt").read_text())
        self.assertIn("#1: 1 photo(s), 1 link(s)", self.api.sent[-1])

    def test_window_expiry_starts_new_listing(self):
        self.bot.handle_update(msg(photo="a"))
        self.clock.t += 500
        self.bot.handle_update(msg(photo="b"))
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001", "002"])

    def test_new_command_forces_new_listing(self):
        self.bot.handle_update(msg(photo="a"))
        self.bot.handle_update(msg(text="/new"))
        self.bot.handle_update(msg(photo="b"))
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001", "002"])

    def test_album_is_one_listing_and_acknowledged_once(self):
        for pid in ("a", "b", "c"):
            self.bot.handle_update(msg(photo=pid, group="g1"))
        self.assertEqual(len(list((self.root / "inbox/001").glob("photo_*"))), 3)
        self.assertEqual(len(self.api.sent), 1)

    def test_duplicate_links_saved_once(self):
        self.bot.handle_update(msg(text="https://x.pl/a"))
        self.bot.handle_update(msg(text="again https://x.pl/a"))
        self.assertEqual((self.root / "inbox/001/links.txt").read_text().split(), ["https://x.pl/a"])

    def test_list_command(self):
        self.bot.handle_update(msg(photo="a", caption="https://x.pl/1"))
        self.bot.handle_update(msg(text="/list"))
        self.assertIn("#1  1 photo(s)  https://x.pl/1", self.api.sent[-1])

    def test_state_survives_restart(self):
        self.bot.handle_update(msg(photo="a"))
        self.bot.save_state()
        again = botmod.Bot(self.api, self.root, 1, window_seconds=180, clock=self.clock)
        again.handle_update(msg(photo="b"))
        self.assertEqual(sorted(p.name for p in (self.root / "inbox").iterdir()), ["001"])

    def test_env_file_does_not_override(self):
        env = self.root / ".env"
        env.write_text("# c\nFOO_TEST_KEY=1\nBAR_TEST_KEY='x y'\n")
        import os
        os.environ["FOO_TEST_KEY"] = "keep"
        botmod.load_env(env)
        self.assertEqual(os.environ["FOO_TEST_KEY"], "keep")
        self.assertEqual(os.environ["BAR_TEST_KEY"], "x y")


if __name__ == "__main__":
    unittest.main()
