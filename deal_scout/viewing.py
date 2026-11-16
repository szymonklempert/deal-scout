"""Viewing mode, stored and orchestrated: start, tick, finish or pause the on-site checklist of one listing.

The checklist is frozen into listings/NNN.json under "viewing" when the owner starts, so ticks survive a restart and a
re-analysis keeps it (process.py). viewing_menu.py holds the pure screens; the bot shows the FixReply values. Standard
library only.
"""

import json
import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from deal_scout import storage
from deal_scout.errors import ProcessError
from deal_scout.feedback_menu import FixReply
from deal_scout.process import load_listing
from deal_scout.prompts import Listing
from deal_scout.render import Message
from deal_scout.viewing_menu import Viewing, checklist, counts, final_message, results_text, toggled, viewing_items

log = logging.getLogger("dealscout")

STALE = "That button is out of date. Tap 🔍 Viewing on the summary again."


def stored(data: Listing) -> Viewing:
    """Return the viewing kept in a listing's data, or say there is none."""
    viewing = data.get("viewing")
    if not isinstance(viewing, dict) or not viewing.get("items"):
        raise ProcessError(STALE)
    return cast(Viewing, viewing)


def save(root: Path, n: int, data: Listing) -> None:
    """Write listing n's data back; the caller holds storage.rows_lock."""
    path = root / "listings" / f"{n:03d}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def start(root: Path, n: int, clock: Callable[[], float]) -> FixReply:
    """Freeze the checklist (or reopen the one already there, marks kept) and show it as a new message."""
    with storage.rows_lock(root / "listings"):
        data = load_listing(root, n)
        if not isinstance(data.get("viewing"), dict):
            items = viewing_items(data)
            if not items:
                raise ProcessError("There is nothing to check on site yet. Re-analyse it with /done " + str(n))
            started = time.strftime("%Y-%m-%d %H:%M", time.localtime(clock()))
            data["viewing"] = {"items": items, "marks": {}, "started": started}
            save(root, n, data)
        viewing = stored(data)
    log.info("#%d: viewing started, %d item(s)", n, len(viewing["items"]))
    return FixReply(checklist(n, data.get("title") or "listing", viewing))


def toggle(root: Path, n: int, args: Sequence[str]) -> FixReply:
    """Cycle one item's mark and edit the checklist in place."""
    with storage.rows_lock(root / "listings"):
        data = load_listing(root, n)
        viewing = stored(data)
        try:
            i = int(args[0])
        except (IndexError, ValueError):
            i = -1
        if not 0 <= i < len(viewing["items"]):
            raise ProcessError(STALE)
        viewing["marks"] = toggled(viewing["marks"], i)
        save(root, n, data)
    return FixReply(checklist(n, data.get("title") or "listing", viewing), edit=True)


def finish(root: Path, n: int) -> FixReply:
    """Write the results for the model to read, and show the tally; the bot queues the re-analysis."""
    data = load_listing(root, n)
    viewing = stored(data)
    folder = root / "inbox" / f"{n:03d}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "viewing_checks.txt").write_text(results_text(viewing), encoding="utf-8")
    log.info("#%d: viewing finished: %d fine, %d problem(s), %d not checked", n, *counts(viewing))
    return FixReply(final_message(n, viewing), edit=True)


def handle(
    root: Path | str, n: int, action: str, args: Sequence[str], *, clock: Callable[[], float] = time.time
) -> FixReply:
    """Handle one tap of the viewing checklist of listing n (start, toggle, finish, cancel) and say what to show."""
    root = Path(root)
    if action == "start":
        return start(root, n, clock)
    if action == "toggle":
        return toggle(root, n, args)
    if action == "finish":
        return finish(root, n)
    if action == "cancel":
        return FixReply(extra=(Message(f"🔍 #{n}: viewing paused; tap 🔍 Viewing to continue."),))
    raise ProcessError(f"unknown action {action}")
