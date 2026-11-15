"""Turn the model's criteria checks and spec scores into numbers; pure, no I/O."""
from typing import Any

from prompts import Listing


def ordered_specs(data: Listing, specs: list[str]) -> list[tuple[str, str, int | None]]:
    """[(name, value, score)] for every spec of the domain, in order; anything Claude left out counts as not shown."""
    got = {s.get("name"): s for s in data.get("specs") or []}
    out = []
    for name in specs:
        s = got.get(name) or {}
        value = (s.get("value") or "").strip() or "not shown"
        score = s.get("score") if value.lower() != "not shown" else None
        if score is not None:
            score = max(0, min(10, int(score))) if isinstance(score, (int, float)) else None
        out.append((name, value, score))
    return out


def specs_avg(data: Listing, specs: list[str]) -> float | None:
    scores = [s for _, _, s in ordered_specs(data, specs) if isinstance(s, (int, float))]
    return round(sum(scores) / len(scores), 1) if scores else None


WEIGHT: dict[Any, float] = {"required": 1.0, "big plus": 1.0, "nice to have": 0.5}


def score(checks: list[dict[str, Any]]) -> float | None:
    """0-10 from the criteria checks. Met = 1, unknown = 0.5, missed = 0, each times its weight (nice-to-haves count
    half); a missed deal breaker makes it 0. Returns None when there are no criteria to score against."""
    if not checks:
        return None
    if any(c["deal_breaker"] and c["result"] == "missed" for c in checks):
        return 0.0
    def weight(c: dict[str, Any]) -> float:
        return WEIGHT.get(c.get("importance"), 1.0)

    points = sum(weight(c) * {"met": 1.0, "unknown": 0.5}.get(c["result"], 0.0) for c in checks)
    return round(10 * points / sum(weight(c) for c in checks), 1)


def clean(criterion: str) -> str:
    """'Deal breaker: rust' -> 'rust' (the label is added when displayed)."""
    head, sep, rest = criterion.partition(":")
    return rest.strip() if sep and head.strip().lower() in ("deal breaker", "deal breakers") else criterion
