"""Copy listing rows to a Google Sheet through a small Apps Script web app (setup: deploy/google-sheet.md).

The bot calls push() after each analysis. To copy everything that is already in listings/listings.csv:
    python3 -m deal_scout.sheet
Configuration: GOOGLE_SHEET_URL (the web app URL) and GOOGLE_SHEET_SECRET in .env. Standard library only.
"""

import json
import sys
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from deal_scout import domain
from deal_scout.config import ROOT, Settings, load_env
from deal_scout.storage import Row, csv_fields, read_rows


def enabled(settings: Settings) -> bool:
    """Whether GOOGLE_SHEET_URL is set."""
    return bool(settings.sheet_url)


def link(settings: Settings) -> str | None:
    """Return the spreadsheet's own address (GOOGLE_SHEET_LINK) for the "Open sheet" button, or None."""
    url = settings.sheet_link
    return url if url.startswith("https://docs.google.com/spreadsheets/") else None


def as_value(value: Any) -> int | float | str:
    """CSV text -> number where it is one, so the sheet can sort and sum."""
    if isinstance(value, (int, float)) or value is None:
        return "" if value is None else value
    text = str(value)
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def push(
    row: Row,
    fields: Iterable[str],
    settings: Settings,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Add or update one listing (matched by number). The script keeps a status you changed in the sheet."""
    body = {
        "secret": settings.sheet_secret,
        "fields": list(fields),
        "row": {k: as_value(row.get(k, "")) for k in fields},
    }
    req = urllib.request.Request(
        settings.sheet_url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    # Apps Script answers with a redirect to the result; urllib follows it as a GET, which is what Google expects.
    with opener(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        out = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(
            "the sheet script did not answer with JSON (check the web app URL and access setting)"
        ) from exc
    if not out.get("ok"):
        raise RuntimeError(out.get("error") or "the sheet script reported an error")


def push_all(
    csv_path: Path, fields: list[str], settings: Settings, opener: Callable[..., Any] = urllib.request.urlopen
) -> int:
    """Copy every row of listings.csv to the sheet, in number order; returns how many were copied."""
    rows = read_rows(csv_path)
    for n in sorted(rows):
        push(rows[n], fields, settings, opener=opener)
        print(f"#{n} copied")
    return len(rows)


def main() -> None:
    """Copy every row of listings.csv to the Google Sheet."""
    load_env(ROOT / ".env")
    settings = Settings.from_env()
    if not enabled(settings):
        sys.exit("GOOGLE_SHEET_URL is not set in .env (see deploy/google-sheet.md)")
    try:
        d = domain.load(settings.domain)
    except domain.DomainError as exc:
        sys.exit(str(exc))
    count = push_all(ROOT / "listings" / "listings.csv", csv_fields(d), settings)
    print(f"Done: {count} listing(s).")


if __name__ == "__main__":
    main()
