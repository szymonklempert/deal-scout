#!/usr/bin/env python3
"""How much the listing analysis uses Claude: one line per run in logs/usage.jsonl, totals for /usage.

    python3 bot/usage.py         # print the totals

Claude Code reports tokens and a cost at API list prices for each run. On a Claude subscription nothing is billed
per run; the list-price figure is still the best single measure of how heavy the usage is, because it weights input,
cached input and output tokens the way they are priced. Your plan's weekly limit is not readable from here: see
/usage in Claude Code or claude.ai > Settings > Usage. Standard library only.
"""
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict

import config

DAY = 86400


class RunStats(TypedDict, total=False):
    """Token counts and timing of one API-provider run (providers.call); cost_usd may be missing or None."""

    model: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    duration_s: float
    turns: int
    cost_usd: float | None


def record(root: Path | str, listing: int, out: dict[str, Any], ok: bool) -> dict[str, Any]:
    """Append one run. `out` is Claude Code's JSON result (may be {} when the run failed before answering)."""
    models = out.get("modelUsage") or {}

    def total(key: str) -> int:
        return sum(int(m.get(key) or 0) for m in models.values())

    rec = {
        "ts": round(time.time()),
        "listing": listing,
        "ok": bool(ok),
        "models": sorted(models),
        "turns": out.get("num_turns"),
        "duration_s": round((out.get("duration_ms") or 0) / 1000, 1),
        "input_tokens": total("inputTokens") + total("cacheCreationInputTokens") + total("cacheReadInputTokens"),
        "cached_tokens": total("cacheReadInputTokens"),
        "output_tokens": total("outputTokens"),
        "cost_usd": round(sum(float(m.get("costUSD") or 0) for m in models.values())
                          or float(out.get("total_cost_usd") or 0), 4),
    }
    path = Path(root) / "logs" / "usage.jsonl"
    path.parent.mkdir(exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def record_api(root: Path | str, listing: int, ok: bool, provider: str, model: str,
               stats: RunStats) -> dict[str, Any]:
    """Append one API-provider run (stats from providers.call; cost_usd may be None when unknown)."""
    rec = {"ts": round(time.time()), "listing": listing, "ok": bool(ok), "provider": provider, "models": [model],
           "turns": stats.get("turns"), "duration_s": stats.get("duration_s", 0),
           "input_tokens": stats.get("input_tokens", 0), "cached_tokens": stats.get("cached_tokens", 0),
           "output_tokens": stats.get("output_tokens", 0), "cost_usd": stats.get("cost_usd") or 0}
    path = Path(root) / "logs" / "usage.jsonl"
    path.parent.mkdir(exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def load(path: Path | str) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    out: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def tokens(n: float) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{round(n / 1000)}k" if n >= 1000 else str(n)


def line(label: str, recs: Sequence[dict[str, Any]]) -> str:
    if not recs:
        return f"{label}: nothing"
    ok = [r for r in recs if r.get("ok")]
    tin = sum(r.get("input_tokens", 0) for r in recs)
    cached = sum(r.get("cached_tokens", 0) for r in recs)
    cost = sum(r.get("cost_usd", 0) for r in recs)
    text = f"{label}: {len(recs)} analys{'is' if len(recs) == 1 else 'es'}"
    if len(ok) < len(recs):
        text += f" ({len(recs) - len(ok)} failed)"
    text += (f" · {tokens(tin)} tokens in ({round(100 * cached / tin) if tin else 0}% cached) · "
             f"{tokens(sum(r.get('output_tokens', 0) for r in recs))} out · ~${cost:.2f}")
    if ok:
        text += (f" · per listing ~${sum(r.get('cost_usd', 0) for r in ok) / len(ok):.2f}, "
                 f"{round(sum(r.get('duration_s', 0) for r in ok) / len(ok))} s")
    return text


def summary(path: Path | str, now: float | None = None) -> str:
    now = now or time.time()
    recs = load(path)
    return "\n".join([
        "Claude usage of the listing analysis (cost at API list prices; a subscription isn't billed per run)",
        line("Last 7 days", [r for r in recs if r.get("ts", 0) >= now - 7 * DAY]),
        line("Last 30 days", [r for r in recs if r.get("ts", 0) >= now - 30 * DAY]),
        line("All time", recs),
        "Your plan's weekly limit: /usage in Claude Code, or claude.ai > Settings > Usage.",
    ])


def main() -> None:
    print(summary(config.ROOT / "logs" / "usage.jsonl"))


if __name__ == "__main__":
    main()
