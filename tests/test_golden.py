"""Golden outputs: everything the owner, the sheet or the model sees, frozen byte for byte.

A made-up domain (tests/fixtures/domains/gadget) keeps these independent of the real domain files. If a change to
one of these outputs is intended, regenerate them with
    GOLDEN_UPDATE=1 python3 -m unittest tests.test_golden
and review the diff of tests/golden/ before committing.
"""
import dataclasses
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bot"))
sys.path.insert(0, str(HERE))
from fakes import FakeAnthropic, FakeOpenAI, FakeRunner  # noqa: E402

import analysis  # noqa: E402
import bot as botmod  # noqa: E402
import config  # noqa: E402
import contact  # noqa: E402
import domain  # noqa: E402
import process  # noqa: E402
from render import Message  # noqa: E402

GOLDEN = HERE / "golden"
FIXTURES = HERE / "fixtures"
SHEET_LINK = "https://docs.google.com/spreadsheets/d/golden/edit"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?")


def result(**over):
    data = {
        "title": "Acme Widget 3 128 GB", "seller_name": "Alex Example", "source": "Gadgets for sale",
        "location": "Kraków", "kind": "phone", "grade": "used", "price_net": False, "fair_price_low": 1000,
        "fair_price_high": 1200, "price_pln": 1350, "model": "Acme Widget 3", "year": 2022, "mileage_km": None,
        "specs": [{"name": "Price vs market", "value": "1 350 PLN · fair 1.0-1.2k", "score": 5},
                  {"name": "Condition", "value": "light scratches <back>", "score": 7},
                  {"name": "Battery", "value": "not shown", "score": 4}],
        "facts": [{"field": "Storage", "value": "128 GB", "certainty": "read"}],
        "criteria_checks": [
            {"criterion": "Budget: up to 1500 PLN", "result": "met", "deal_breaker": True, "importance": "required"},
            {"criterion": "Battery health shown", "result": "unknown", "deal_breaker": False,
             "importance": "required"},
            {"criterion": "Original box", "result": "missed", "deal_breaker": False, "importance": "nice to have"},
            {"criterion": "Deal breaker: iCloud lock", "result": "unknown", "deal_breaker": True,
             "importance": "required"}],
        "missing": ["battery health", "serial number"], "red_flags": ["price above fair range", "no receipt"],
        "verdict": "ask first", "summary": "Decent phone & fair condition, but the battery is unknown.",
        "seller_message": "Hello, I'm interested in the Acme Widget 3.\n1. What is the battery health?\nThanks.",
        "seller_sms": "Hello, is the Acme Widget 3 still for sale? Battery health?",
        "seller_phone": "+48 600 111 222", "contact_preference": "whatsapp",
    }
    data.update(over)
    return data


class Recorder:
    """A Telegram API that records every message the bot sends."""

    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text, buttons=None, html=False):
        self.sent.append(Message(text=text, html=html, buttons=tuple((label, value) for label, value in buttons or [])))

    def answer_callback(self, callback_id):
        pass

    def get_file_bytes(self, file_id):
        return b"IMG"


def mask(text):
    return DATE.sub("<date>", text)


def message_text(items):
    """Messages as the owner sees them: one block per message, its buttons underneath."""
    out = []
    for m in items:
        if isinstance(m, str):
            out.append(m)
            continue
        block = ("[html]\n" if m.html else "") + m.text
        for label, value in m.buttons:
            block += "\n[%s] %s" % (label, value)
        out.append(block)
    return "\n\n=====\n\n".join(out) + "\n"


class GoldenTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.settings = config.Settings(sheet_link=SHEET_LINK)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.domain = domain.load("gadget", roots=[FIXTURES])

    def check(self, name, text):
        path = GOLDEN / name
        if os.environ.get("GOLDEN_UPDATE"):
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.assertEqual(text, path.read_text(encoding="utf-8"), name)

    def listing(self, n, links=None, notes=None):
        folder = self.root / "inbox" / ("%03d" % n)
        folder.mkdir(parents=True)
        (folder / "photo_01.jpg").write_bytes(b"\xff\xd8golden")
        if links:
            (folder / "links.txt").write_text(links + "\n", encoding="utf-8")
        if notes:
            (folder / "notes.txt").write_text(notes + "\n", encoding="utf-8")

    def test_claude_code_command(self):
        cmd = analysis.claude_command("007", "opus", self.domain)
        self.check("claude_command.txt", "\n".join(cmd[:2] + cmd[3:-1]) + "\n")
        self.check("prompt_claude_code.txt", cmd[2] + "\n")
        self.check("schema.json", json.dumps(json.loads(cmd[-1]), indent=2, ensure_ascii=False) + "\n")

    def test_api_request_and_card(self):
        self.listing(1, links="https://example.com/item/1", notes="[2026-01-01 10:00] seen in a group")
        (self.root / "listings").mkdir()
        (self.root / "listings" / "market.md").write_text("# Market\n- phone: median 1.2k\n", encoding="utf-8")
        settings = dataclasses.replace(self.settings, provider="anthropic")
        client = FakeAnthropic(text=json.dumps(dict(result(), card_markdown="# #001 Widget\n- tel. 600 111 222\n")))
        out = process.process_listing(self.root, 1, settings=settings, domain=self.domain, client=client)
        kw = client.calls[0][1]
        content = kw["messages"][0]["content"]
        self.check("api_system.txt", kw["system"][0]["text"] + "\n")
        self.check("api_user.txt", "\n".join("<image %s>" % b["source"]["media_type"] if b["type"] == "image"
                                             else b["text"] for b in content) + "\n")
        self.check("schema_card.json", json.dumps(kw["output_config"]["format"]["schema"], indent=2,
                                                  ensure_ascii=False) + "\n")
        self.check("card_api.md", (self.root / "listings" / "001.md").read_text(encoding="utf-8"))
        self.check("summary_api.txt", message_text(out))
        settings = dataclasses.replace(self.settings, provider="openai")
        client = FakeOpenAI(content=json.dumps(dict(result(), card_markdown="# card\n")))
        process.process_listing(self.root, 1, settings=settings, domain=self.domain, model="gpt-x", client=client)
        kw = client.calls[0]
        self.assertEqual(kw["messages"][0]["content"], self.read("api_system.txt")[:-1])

    def read(self, name):
        return (GOLDEN / name).read_text(encoding="utf-8")

    def test_full_run_files_and_messages(self):
        prices = [1450, 1100, 1350, 1500, 1800, 1300, 1400, 1250, 1600, 1380]
        for n, price in enumerate(prices, start=1):
            self.listing(n, links="https://example.com/item/%d" % n)
            over = {"price_pln": price, "model": "Acme Widget %d" % n, "year": 2020 + n % 4,
                    "grade": "used" if n % 2 else "new"}
            if n == 10:  # #1 reposted cheaper
                over.update(model="Acme Widget 1", year=2021)
            if n == 2:
                over.update(seller_phone=None, seller_sms="", red_flags=[], seller_name="alex  EXAMPLE")
            if n == 3:
                over["criteria_checks"] = [dict(result()["criteria_checks"][0], result="missed")]
            if n == 4:
                over.update(criteria_checks=[], seller_message="", seller_sms="", seller_name=None,
                            price_pln=None, specs=[])
            out = process.process_listing(self.root, n, settings=self.settings, domain=self.domain, model="opus",
                                          runner=FakeRunner(result(**over)))
            self.check("summary_%d.txt" % n, message_text(out))
        listings = self.root / "listings"
        self.check("listings.csv", mask(listings.joinpath("listings.csv").read_text(encoding="utf-8-sig")))
        self.check("INDEX.md", listings.joinpath("INDEX.md").read_text(encoding="utf-8"))
        self.check("market.md", mask(listings.joinpath("market.md").read_text(encoding="utf-8")))
        self.check("001.json", listings.joinpath("001.json").read_text(encoding="utf-8"))
        drafts = (contact.seller_text(self.root, 1, "msg", "48600111222")
                  + contact.seller_text(self.root, 1, "sms", "48600111222")
                  + contact.seller_text(self.root, 1, "sms", "")
                  + contact.contact_for(self.root, 2, "+49 151 2345 6789"))
        self.check("drafts.txt", message_text(drafts))

    def test_bot_transcript(self):
        api = Recorder()
        def processor(n):
            return process.process_listing(self.root, n, settings=self.settings, domain=self.domain, model="opus",
                                           runner=FakeRunner(result()))
        bot = botmod.Bot(api, self.root, 1, window_seconds=180, clock=lambda: 1000.0, processor=processor,
                         contact=lambda n, phone: contact.contact_for(self.root, n, phone), sheet_link=SHEET_LINK,
                         seller_text=lambda n, kind, phone: contact.seller_text(self.root, n, kind, phone))

        def say(text=None, user=1, photo=None):
            m = {"from": {"id": user}, "chat": {"id": user}, "date": 1000}
            if text is not None:
                m["text"] = text
            if photo:
                m["photo"] = [{"file_id": photo}]
            bot.handle_update({"update_id": 1, "message": m})

        def tap(data, user=1):
            bot.handle_update({"update_id": 2, "callback_query": {"id": "cb", "from": {"id": user}, "data": data}})

        say("/help")
        say("/list")
        say("/market")
        say("/done")
        say(photo="a")
        say("https://example.com/item/1 nice one")
        say(photo="b")
        say("/list")
        say("/done")
        bot.run_job(bot.jobs.get_nowait())
        tap("msg:1:48600111222")
        tap("sms:1:")
        tap("sms:1:", user=99)
        tap("bogus")
        say("/phone 1 600 111 222")
        say("/phone 1 12")
        say("/phone x")
        say("/done 9")
        say("/sheet")
        say("/usage")
        say("hello", user=99)
        say("/bogus")
        say("")
        bot.flush_ack(force=True)
        self.check("bot_transcript.txt", message_text(api.sent))


if __name__ == "__main__":
    unittest.main()
