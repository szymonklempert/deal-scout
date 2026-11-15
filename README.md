# deal-scout

A private Telegram assistant that reviews second-hand listings before you buy. You forward screenshots of a listing
from your phone. A few minutes later you get back a scored review, the questions the listing leaves open, and a
ready-to-send message for the seller. It runs on your Claude subscription, through Claude Code.

What it reviews comes from a **domain**, a folder of plain files. The first domain, `domains/campervan/`, is for
buying a used campervan in Poland. A convertible or a TV would be another folder, with no code changes.

## What you get for each listing

```
#12 Mercedes Sprinter 313 CDI L2H2 2012
84 000 PLN · Kraków · Kampery na sprzedaż
Score 7.4/10 · specs 6.9/10 · ask first
Fits the budget and the must-haves, but there is no word on chain history or the registered seats.

🟢 Price vs market: 84 000 PLN · market ~86k (n=11) · 7/10
🟢 Engine & gearbox: OM651 129 KM, manual · 6/10
⚪ Registered seats: not shown
🟡 Solar & batteries: 200 W, AGM 2x95 Ah · 5/10
…one line per spec…

🚩 OM651 with no chain history · "4 miejsca" may mean sleeping places only
[💬 WhatsApp message]  [✉️ SMS]  [📊 Open sheet]
```

- **Scores you can trust.** The model checks each of your criteria as met, missed or unknown, and scores each spec
  0–10 against a rubric you control. The final score is computed in code from those checks, so the same evidence
  always gives the same score. A missed deal breaker sets it to 0. Specs the listing doesn't show get no score, so
  they can't count against or for a listing.
- **What's missing, and the questions to close it.** Tap a button to get a polite first message in the seller's
  language, asking only what the listing leaves open. On WhatsApp it opens the chat with the text already typed; for
  SMS you get a version that copies with one tap.
- **A listing card** (`listings/NNN.md`): facts with a certainty level, red flags, questions for the seller, and what
  to check on site.
- **A spreadsheet:** a CSV, plus an optional Google Sheet that every analysis updates.
- **Market prices from your own data:** `listings/market.md` gives the median asking prices of similar items you've
  collected, so "fair price" isn't a guess for long. Reposts count once, and price drops are tracked.

## How it works

```
phone ──screenshots──▶ Telegram bot ──▶ inbox/NNN/
                                          │  /done (or 3 min after the last photo)
                                          ▼
                     claude -p  (Claude Code, headless, no web, writes only to listings/)
                       reads: domains/<name>/  criteria · checklist · rubric files · card template
                              + the screenshots + listings/market.md
                       returns: JSON (specs, criteria checks, drafts) + writes the card
                                          ▼
               bot/process.py: score in code ─▶ CSV · INDEX · market.md · Google Sheet
                                          ▼
                         Telegram: summary + buttons
```

- **Standard-library Python only.** The bot makes outgoing calls only (Telegram long polling), so no ports are
  open.
- **Claude Code runs as a sandboxed step.** It has only the Read, Write, Edit and Glob tools, writes only to
  `listings/`, and has no shell or web access. A listing is untrusted text from a stranger, so even a listing
  written to manipulate the model can't reach the network or anything outside the project.
- **Privacy:**
  - Seller phone numbers are never written to disk; they exist only inside a button.
  - Message contents are never logged.
  - `inbox/`, `listings/`, `logs/` and your `criteria.md` are git-ignored.
- **It never contacts anyone.** It prepares messages, and you send them.
- **Usage:**
  - every run's tokens and list-price cost go to `logs/usage.jsonl` (`/usage` shows the totals);
  - the analysis never receives an API key, so it always runs on the Claude subscription.

## Bot commands

| | |
|---|---|
| (send screenshots/links/notes) | saved as one listing; confirmed once with the count |
| `/done`, `/done 7` | analyse the open listing, or re-analyse #7 |
| `/phone 7 600 123 456` | add the seller's number to #7's WhatsApp message |
| `/list`, `/market`, `/sheet` | recent listings, market prices, open the Google Sheet |
| `/status`, `/usage` | is the bot up to date and busy; how much Claude it used |

## Setup

1. `cp .env.example .env && chmod 600 .env`, then add a Telegram bot token from @BotFather.
2. Copy `domains/campervan/criteria.example.md` to `criteria.md` in the same folder and fill it in.
3. `python3 bot/bot.py`, message the bot, and put the user ID it replies with into `.env`. From then on it only
   answers you.
4. Send a listing, then `/done`.

Optional extras:
- **Google Sheet:** `deploy/google-sheet.md`
- **Running on an always-on Linux box:** `deploy/README.md`
- **Adding a domain:** `domains/README.md`

Requirements: Python 3.10+, and [Claude Code](https://code.claude.com) logged in with a Claude subscription.

Tests (no network, no Claude calls): `python3 -m unittest discover -s tests`
