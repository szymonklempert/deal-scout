"""Shared test helpers: a canned model result, fake Claude Code / Anthropic / OpenAI backends. No tests here."""

import json
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace as Ns

from deal_scout import config, domain
from deal_scout.reports import Entry

SETTINGS = config.Settings()
CAMPERVAN = domain.load("campervan")


def check(criterion, result, deal_breaker=False):
    return {"criterion": criterion, "result": result, "deal_breaker": deal_breaker}


RESULT = {
    "title": "Fiat Ducato 2.3 Hymer 2014",
    "seller_name": "Jan Kowalski",
    "location": "Łódź",
    "model_family": "Ducato/Jumper/Boxer X250 (2006-2014)",
    "body": "coachbuilt",
    "build": "OEM",
    "registration": "M1",
    "price_net": False,
    "fair_price_low": 105000,
    "fair_price_high": 115000,
    "specs": [
        {"name": "Year", "value": "2014", "score": 6},
        {"name": "Solar & batteries", "value": "Victron 400 W", "score": 8},
        {"name": "Mileage", "value": "not shown", "score": 3},
    ],
    "source": "Kampery i campervany na sprzedaż",
    "price_pln": 118500,
    "model": "Fiat Ducato 2.3",
    "year": 2014,
    "mileage_km": 196000,
    "facts": [{"field": "Weight (DMC)", "value": "not shown", "certainty": "not shown"}],
    "criteria_checks": [
        check("Budget", "met", True),
        check("Fixed bed", "met"),
        check("Toilet", "unknown"),
        check("Solar", "missed"),
    ],
    "missing": ["VIN", "DMC"],
    "red_flags": ["Urgent sale"],
    "verdict": "ask first",
    "summary": "Fits most criteria.",
    "seller_message": "Dzień dobry, interesuje mnie kamper Hymer.\n1. Czy mogę prosić o VIN?\nDziękuję.",
    "seller_sms": "Dzień dobry, czy mogę prosić o VIN Hymera?",
    "seller_phone": "+48 600-123-456",
    "contact_preference": "whatsapp",
}


class FakeRunner:
    """Stands in for subprocess.run(claude ...): records the call and returns a canned Claude Code JSON result."""

    def __init__(self, structured=RESULT, stdout=None, returncode=0):
        self.calls = []
        self.stdout = (
            stdout
            if stdout is not None
            else json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "structured_output": structured}
            )
        )
        self.returncode = returncode

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "boom")


CARD = "# #001 Fiat Ducato\n- Seller: Jan Kowalski, tel. +48 600-123-456\n"


def answer(**extra):
    return json.dumps(dict(RESULT, card_markdown=CARD, **extra))


class FakeAnthropic:
    def __init__(self, text=None, stop_reason="end_turn"):
        self.calls = []
        msg = Ns(
            stop_reason=stop_reason,
            model="claude-opus-5-5",
            stop_details=Ns(category="cyber"),
            content=[Ns(type="thinking", thinking=""), Ns(type="text", text=text or answer())],
            usage=Ns(
                input_tokens=1200, cache_read_input_tokens=8000, cache_creation_input_tokens=0, output_tokens=5000
            ),
        )

        def stream(kind):
            @contextmanager
            def run(**kw):
                self.calls.append((kind, kw))
                yield Ns(get_final_message=lambda: msg)

            return run

        self.messages = Ns(stream=stream("plain"))
        self.beta = Ns(messages=Ns(stream=stream("beta")))


class FakeOpenAI:
    def __init__(self, content=None, refusal=None, finish_reason="stop", cost=None):
        self.calls = []
        usage_ = Ns(prompt_tokens=9000, completion_tokens=4000, prompt_tokens_details=Ns(cached_tokens=6000))
        if cost is not None:
            usage_.cost = cost
        resp = Ns(
            model="some-model",
            usage=usage_,
            choices=[Ns(finish_reason=finish_reason, message=Ns(content=content or answer(), refusal=refusal))],
        )

        def create(**kw):
            self.calls.append(kw)
            return resp

        self.chat = Ns(completions=Ns(create=create))


def entry(n, score=None, **kw):
    base = {
        "title": f"Van {n}",
        "verdict": "ask first",
        "status": "new",
        "score": score,
        "specs_avg": None,
        "price": None,
        "year": None,
        "km": None,
    }
    base.update(kw)
    return Entry(n=n, **base)
