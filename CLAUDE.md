# Campervan helper: how to work in this project

The owner is looking for a used campervan in Poland and collects listings from Facebook Marketplace, Facebook
groups and portals by sending screenshots and links to a private Telegram bot. The bot saves them into
`inbox/<NNN>/` (photo_NN.jpg, notes.txt, links.txt). You turn them into decision-ready cards.

## When asked to process the inbox
1. Read `criteria.md` and `checklist.md`. If `criteria.md` is mostly empty, say so and ask the owner to fill it in
   before scoring; you can still extract facts.
2. For every `inbox/<NNN>/` without a matching `listings/<NNN>.md`: look at every photo (read the image files),
   then notes.txt and links.txt, and write `listings/<NNN>.md` from `templates/listing-card.md`.
3. Numbers read from images (price, mileage, year, weight) can be wrong. Put them in the Facts table with a certainty
   column, and list anything you could not read as a question. Never invent a value; write "not shown".
4. Fill the red flags, seller questions and on-site checks from `checklist.md` and from what the listing actually
   shows. Mark legal or technical claims you are not sure about as "verify".
5. Update `listings/INDEX.md`: one table row per listing (number, title, price, base vehicle, year, mileage, fit score,
   status, verdict), sorted by fit score. Keep it short.
6. Reply with a few lines: the new cards, the best one and why, and anything you need from the owner.

## Rules
- Use only what the owner sent. Do not fetch or scrape listing URLs from Facebook or the portals, and do not log in to
  any account. If a link would help, ask the owner to send a screenshot or the text.
- Do not contact sellers. Message drafts are text for the owner to copy into Messenger.
- Seller names and phone numbers are personal data: do not copy them into cards or the index. Refer to "the seller".
- `inbox/` and `listings/` are private and git-ignored. Never commit them, and never put tokens in files.
- The tone is practical and cautious: a clear "skip" is a useful answer. Say what you could not verify.

## The bot
`bot/bot.py` is a standard-library Python long-polling bot (outgoing connections only). Config is in `.env`
(see `.env.example`). Tests: `python3 -m unittest discover -s tests`. Deployment on the Linux PC: `deploy/README.md`.
Changing the bot: keep it standard library only, keep the allowlist behaviour (strangers get no answer), and add a test.
