# Campervan helper

Find a used campervan without reading endless Facebook listings. You send screenshots and links to a private
Telegram bot from your phone; the bot saves them; an AI assistant turns each listing into a card (facts, red flags,
questions for the seller, what to check, fit against your criteria) and keeps a ranked index.

Flow: phone (share screenshot or link to the bot) -> `inbox/<NNN>/` -> process with Claude (see `CLAUDE.md`) ->
`listings/<NNN>.md` and `listings/INDEX.md`.

Start here:
1. Fill in `criteria.md`.
2. Create the bot and run it: `deploy/README.md`.
3. Send a first listing, then ask Claude in this folder to "process the inbox".

Files: `bot/bot.py` (the bot), `criteria.md`, `checklist.md`, `templates/listing-card.md`, `CLAUDE.md` (instructions
for the assistant), `deploy/` (service file for the Linux PC). Tests: `python3 -m unittest discover -s tests`.

Nothing here reads Facebook or any portal automatically: it only handles what you send.
