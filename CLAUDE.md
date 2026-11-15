# deal-scout: how to work in this project

The owner collects second-hand listings (screenshots, links, notes) by sending them to a private Telegram bot. The
bot saves them into `inbox/<NNN>/` (photo_NN.jpg, notes.txt, links.txt). You turn them into decision-ready cards.
What is being bought is the active domain: `DOMAIN` in `.env` (default `campervan`), with its files in
`domains/<name>/` (see `domains/README.md`).

## When asked to process the inbox
1. Read the domain's `criteria.md`, `checklist.md`, `instructions.md` and the reference files listed in its
   `domain.json`. If `criteria.md` is missing or mostly empty, say so and ask the owner to fill it in (from
   `criteria.example.md`) before scoring; you can still extract facts.
2. For each new listing, run `python3 bot/process.py <N>`. It runs this workflow through headless Claude Code with
   the right restrictions, scores in code, and updates `listings/` (card, CSV, INDEX, market.md, Google Sheet).
   If you write a card by hand instead: look at every photo, then notes.txt and links.txt, and use the domain's
   `card-template.md`.
3. Numbers read from images can be wrong. Put them in the facts table with a certainty level, and list anything you
   could not read as a question. Never invent a value; write "not shown".
4. Mark legal or technical claims you are not sure about as "verify".
5. Never give a numeric score by hand or edit `listings/INDEX.md`, `listings/listings.csv` or `listings/market.md`:
   the scripts generate them.
6. Reply with a few lines: the new cards, the best one and why, and anything you need from the owner.

## When asked to review the market
`listings/market.md` is generated from the owner's listings (asking prices grouped as the domain's `domain.json`
says). Compare it with the fair prices in the domain's reference files. Propose concrete edits only where a group has
at least 8 items and a ⚠️ shows a gap, or where the reference files have no prices for a group that now has data.
Asking prices run above sale prices; price drops hint at the real level. Show the proposed lines and wait for the
owner's approval before editing a reference file.

## Rules
- Use only what the owner sent. Do not fetch or scrape listing URLs and do not log in to any account. If a link would
  help, ask the owner for a screenshot or the text.
- Never contact sellers. Prepare the messages; the owner sends them.
- Save the seller's name as the listing shows it, in the card's seller line only (to check it against the documents
  and to spot one seller behind several listings). Never write phone numbers into any file (the bot uses them only
  inside the WhatsApp link). Elsewhere say "the seller".
- `inbox/`, `listings/`, `logs/`, `.env` and `domains/*/criteria.md` are private and git-ignored. Never commit them,
  and never put tokens in files.
- The tone is practical and cautious: a clear "skip" is a useful answer. Say what you could not verify.

## The code
- **Standards:** `docs/CODING_STANDARDS.md` is binding for every code change. Run ruff, mypy and the tests before
  committing (README, "Development").
- **The bot:** `bot/bot.py` is a standard-library Python long-polling bot (outgoing connections only). Config is in
  `.env` (see `.env.example`).
- **Tests:** `python3 -m unittest discover -s tests`. Deployment: `deploy/README.md`.
- **Changing the bot:** keep it standard library only, keep the allowlist behaviour (strangers get no answer), and
  add a test.
- **Analysis:** `bot/analysis.py` runs it with `PROVIDER` from `.env` (`bot/providers.py`).
  - `claude-code` (default) runs `claude -p` with no shell or web tools, write access only to `listings/`, and no
    API key in its environment.
  - `anthropic`, `openai` and `openrouter` make one structured-output request with no tools; their SDKs are imported
    only when chosen.
  - Keep these restrictions, and keep every provider returning the same schema.
- **Domains:** nothing in `bot/` may be specific to one domain. Domain knowledge goes in `domains/<name>/`.
- **Modules** (one job each, no import cycles):
  - `bot/process.py` ties one listing's analysis together (CLI: `python3 bot/process.py N`);
  - `bot/analysis.py` runs Claude Code or the API provider;
  - `bot/prompts.py` the prompt and the JSON schema;
  - `bot/scoring.py` the score from the criteria checks (pure);
  - `bot/storage.py` the CSV and `INDEX.md`;
  - `bot/render.py` Telegram HTML;
  - `bot/contact.py` phone numbers and seller drafts;
  - `bot/providers.py` the API providers;
  - `bot/config.py` reads `.env` and the environment once into `Settings` (nothing else reads `os.environ`) and defines the project root;
  - `bot/domain.py` loads the active domain;
  - `bot/market.py` builds `listings/market.md`;
  - `bot/sheet.py` copies rows to a Google Sheet through `deploy/google-sheet.gs`;
  - `bot/usage.py` records Claude usage per run.
