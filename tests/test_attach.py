import tempfile
import unittest
from pathlib import Path

from deal_scout.attach import Attach
from deal_scout.inbox import Inbox
from deal_scout.render import Message


class AttachTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        (self.root / "inbox" / "001").mkdir(parents=True)
        self.t = 1000.0
        self.said, self.sent, self.analysed, self.queued = [], [], [], []

        def fetch(file_id):
            if file_id == "lost":
                raise TimeoutError("timed out")
            return b"IMG"

        self.attach = Attach(
            Inbox(self.root),
            lambda: self.t,
            fetch,
            self.said.append,
            self.sent.append,
            self.analysed.append,
            self.queued.append,
            auto_analyse=True,
        )

    def msg(self, date=1000):
        return {"date": date}

    def test_viewing_messages_go_to_viewing_files_and_one_confirmation(self):
        self.attach.viewing_tap("start", 1)
        self.attach.save(self.msg(), "rust on the sills https://x.pl/a", ["a", "lost"])
        self.attach.save(self.msg(), "", ["b"])
        folder = self.root / "inbox" / "001"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["viewing.txt", "viewing_01.jpg", "viewing_02.jpg"])
        self.assertFalse((folder / "links.txt").exists())
        self.assertEqual(len(self.said), 1)  # the lost photo is reported
        self.assertIn("1 photo(s) couldn't be downloaded", self.said[0])
        self.attach.flush_ack()
        self.assertEqual(self.said[1:], [])  # not yet: the photos may still be arriving
        self.t += 3
        self.attach.flush_ack()
        self.assertEqual(self.said[1:], ["🔍 #1: 2 photo(s), 1 note(s) saved from the viewing."])

    def test_each_message_extends_viewing_mode_by_two_hours(self):
        self.attach.viewing_tap("start", 1)
        self.t += 7000
        self.attach.save(self.msg(), "note", [])
        self.t += 7000
        self.attach.expire()
        self.assertIsNotNone(self.attach.pending)

    def test_expiry_ends_the_mode_and_offers_finish_without_auto_analysis(self):
        self.attach.viewing_tap("start", 1)
        self.attach.save(self.msg(), "note", [])
        self.t += 7201
        self.attach.expire()
        self.assertIsNone(self.attach.pending)
        self.assertEqual(self.queued, [])
        message = self.sent[-1]
        self.assertIsInstance(message, Message)
        self.assertEqual(message.text, "🔍 #1: viewing mode ended after 2 hours of quiet. Finish when you're ready:")
        self.assertEqual(message.buttons, (("✅ Finish viewing", "vf:1"),))

    def test_toggling_keeps_the_mode_and_finish_or_cancel_end_it(self):
        self.attach.viewing_tap("start", 1)
        self.attach.viewing_tap("toggle", 1)
        self.assertEqual(self.attach.pending.kind, "viewing")
        self.attach.viewing_tap("finish", 2)  # another listing's tap
        self.assertIsNotNone(self.attach.pending)
        self.attach.viewing_tap("cancel", 1)
        self.assertIsNone(self.attach.pending)

    def test_other_commands_do_not_end_a_viewing_but_end_reply_mode(self):
        self.attach.viewing_tap("start", 1)
        self.assertFalse(self.attach.leave_for_command("/list", []))
        self.assertIsNotNone(self.attach.pending)
        self.attach.tap("sr", 1)
        self.assertEqual(self.attach.pending.kind, "reply")  # reply mode replaces the viewing
        self.assertFalse(self.attach.leave_for_command("/list", []))
        self.assertIsNone(self.attach.pending)
        self.assertTrue(self.said[-1].startswith("📨 #1: reply mode ended"))

    def test_tap_on_an_unknown_listing(self):
        self.attach.tap("sr", 9)
        self.assertEqual(self.said, ["There is no #9."])
