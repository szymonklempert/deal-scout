"""Owner corrections to the model's scores: apply them, describe them, log them. Pure except log_feedback.

Corrections live next to the model's own values in listings/NNN.json under "overrides":
    {"specs": {"<spec name>": <int 0-10 or None>}, "checks": {"<criterion text>": "met"|"missed"|"unknown"}}
apply_overrides() returns a copy of the listing with them applied, and records what changed under the private key
MARK, so the summary and the CSV can show "5→3" without the model's values being touched. The key is only ever set
on that copy, never on the data that is saved.
"""
import copy
import json
import re
from pathlib import Path
from typing import Any

from deal_scout.prompts import Listing
from deal_scout.scoring import clean, ordered_specs

Overrides = dict[str, dict[str, Any]]

MARK = "_overridden"  # private key on an overridden copy: {"specs": {name: {"was", "now"}}, "checks": {...}}
RESULTS = ("met", "missed", "unknown")
OWNER_VALUE = "set by you"  # shown for a spec the model found nothing about but the owner scored


def clamp_score(value: Any) -> int | None:
    """Return an owner's spec score as an int from 0 to 10, or None for "no score"."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(10, int(value)))


def apply_overrides(data: Listing, overrides: Overrides) -> Listing:
    """Return a copy of the listing with the owner's spec scores and check results in place of the model's."""
    out = dict(data)
    out.pop(MARK, None)
    marks: dict[str, dict[str, dict[str, Any]]] = {"specs": {}, "checks": {}}
    spec_over = overrides.get("specs") or {}
    if spec_over:
        was = {name: score for name, _, score in ordered_specs(data, list(spec_over))}
        out["specs"] = _override_specs(data.get("specs") or [], spec_over, was, marks["specs"])
    check_over = overrides.get("checks") or {}
    if check_over:
        out["criteria_checks"] = _override_checks(data.get("criteria_checks") or [], check_over, marks["checks"])
    if marks["specs"] or marks["checks"]:
        out[MARK] = marks
    return out


def _override_specs(specs: list[dict[str, Any]], spec_over: dict[str, Any], was: dict[str, int | None],
                    marks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy the model's specs with the owner's scores applied, noting each real change in marks."""
    out = copy.deepcopy(specs)
    by_name = {s.get("name"): s for s in out}
    for name, raw in spec_over.items():
        new = clamp_score(raw)
        spec = by_name.get(name)
        if spec is None:
            spec = {"name": name, "value": "not shown", "score": None}
            out.append(spec)
            by_name[name] = spec
        if new is not None and (spec.get("value") or "not shown").strip().lower() == "not shown":
            spec["value"] = OWNER_VALUE  # a score on "not shown" would otherwise be ignored by the scoring
        spec["score"] = new
        if new != was[name]:
            marks[name] = {"was": was[name], "now": new}
    return out


def _override_checks(checks: list[dict[str, Any]], check_over: dict[str, Any],
                     marks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy the model's criteria checks with the owner's results applied, noting each real change in marks."""
    out = copy.deepcopy(checks)
    for check in out:
        new = check_over.get(str(check.get("criterion")))
        if new in RESULTS and new != check.get("result"):
            marks[check["criterion"]] = {"was": check.get("result"), "now": new}
            check["result"] = new
    return out


def spec_marks(data: Listing) -> dict[str, dict[str, Any]]:
    """Return the specs whose score the owner changed: {name: {"was": int | None, "now": int | None}}."""
    result: dict[str, dict[str, Any]] = (data.get(MARK) or {}).get("specs") or {}
    return result


def check_marks(data: Listing) -> dict[str, dict[str, Any]]:
    """Return the checks whose result the owner changed: {criterion: {"was": str, "now": str}}."""
    result: dict[str, dict[str, Any]] = (data.get(MARK) or {}).get("checks") or {}
    return result


def score_word(score: int | None) -> str:
    """Format a spec score for display: the number, or an en dash for no score."""
    return "–" if score is None else str(score)


def corrections_text(data: Listing) -> str:
    """Describe the owner's changes in one line for the CSV, e.g. "Solar 5→3; Toilet unknown→met"; empty when none."""
    parts = [f"{name} {score_word(m['was'])}→{score_word(m['now'])}" for name, m in spec_marks(data).items()]
    parts += [f"{clean(name)} {m['was']}→{m['now']}" for name, m in check_marks(data).items()]
    return "; ".join(parts)


def override_count(overrides: Overrides) -> int:
    """How many corrections are stored."""
    return len(overrides.get("specs") or {}) + len(overrides.get("checks") or {})


def carry_overrides(old: Overrides, new_data: Listing) -> tuple[Overrides, int]:
    """Return the corrections to keep when a listing is analysed again, and how many were dropped.

    Spec corrections carry over by name. A check correction only applies while the same criterion text is still
    among the new checks; the others are dropped.
    """
    criteria = {c.get("criterion") for c in new_data.get("criteria_checks") or []}
    kept: Overrides = {}
    if old.get("specs"):
        kept["specs"] = dict(old["specs"])
    checks = {k: v for k, v in (old.get("checks") or {}).items() if k in criteria}
    if checks:
        kept["checks"] = checks
    return kept, len(old.get("checks") or {}) - len(checks)


def scrub_numbers(text: str) -> str:
    """Replace anything that looks like a phone number (7 or more digits) in the owner's free text."""
    def repl(m: re.Match[str]) -> str:
        return "[number removed]" if sum(c.isdigit() for c in m.group()) >= 7 else m.group()

    return re.sub(r"\+?\d[\d\s().-]*\d", repl, text)


def log_feedback(path: Path, record: dict[str, Any]) -> None:
    """Append one correction (or reason) as a JSON line to listings/feedback.jsonl."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
