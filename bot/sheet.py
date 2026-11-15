#!/usr/bin/env python3
"""Copy listing rows to a Google Sheet through a small Apps Script web app (setup: deploy/google-sheet.md).

The bot calls push() after each analysis. To copy everything that is already in listings/listings.csv:
    python3 bot/sheet.py
Configuration: GOOGLE_SHEET_URL (the web app URL) and GOOGLE_SHEET_SECRET in .env. Standard library only.
"""
import json
import sys
import urllib.request
from pathlib import Path

from config import Settings, load_env


def enabled(settings: Settings) -> bool:
    """Whether GOOGLE_SHEET_URL is set."""
    return bool(settings.sheet_url)


def link(settings: Settings) -> str | None:
    """The spreadsheet's own address (GOOGLE_SHEET_LINK), for the "Open sheet" button; None if unset or not a sheet."""
    url = settings.sheet_link
    return url if url.startswith("https://docs.google.com/spreadsheets/") else None


def as_value(value):
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


def push(row, fields, settings: Settings, opener=urllib.request.urlopen):
    """Add or update one listing (matched by number). The script keeps a status you changed in the sheet."""
    body = {
        "secret": settings.sheet_secret,
        "fields": list(fields),
        "row": {k: as_value(row.get(k, "")) for k in fields},
    }
    req = urllib.request.Request(settings.sheet_url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    # Apps Script answers with a redirect to the result; urllib follows it as a GET, which is what Google expects.
    with opener(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        out = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("the sheet script did not answer with JSON (check the web app URL and access "
                           "setting)") from exc
    if not out.get("ok"):
        raise RuntimeError(out.get("error") or "the sheet script reported an error")


def push_all(csv_path, settings: Settings, opener=urllib.request.urlopen):
    """Copy every row of listings.csv to the sheet, in number order; returns how many were copied."""
    import process
    rows = process.read_rows(csv_path)
    for n in sorted(rows):
        push(rows[n], process.csv_fields(), settings, opener=opener)
        print("#%d copied" % n)
    return len(rows)


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import process
    load_env(process.ROOT / ".env")
    settings = Settings.from_env()
    if not enabled(settings):
        sys.exit("GOOGLE_SHEET_URL is not set in .env (see deploy/google-sheet.md)")
    count = push_all(process.ROOT / "listings" / "listings.csv", settings)
    print("Done: %d listing(s)." % count)


if __name__ == "__main__":
    main()
