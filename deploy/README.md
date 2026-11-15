# Running the bot on the Linux PC

The bot only makes outgoing connections to Telegram, so no router ports need opening.

1. Create the bot: in Telegram open @BotFather, send `/newbot`, choose a name, and copy the token.
2. On the PC (use a normal user account, not root):
   ```
   git clone <this repo> ~/deal-scout        # or copy the folder
   cd ~/deal-scout
   cp .env.example .env && chmod 600 .env   # paste the token; leave TELEGRAM_ALLOWED_USER_ID empty for now
   python3 -m deal_scout
   ```
3. Message your bot. It answers "Setup: your Telegram user ID is ...". Put that number into `.env`
   as `TELEGRAM_ALLOWED_USER_ID`, stop the bot (Ctrl+C) and start it again. Now only you are served.
4. Send a screenshot and a link. You should get "#1: 1 photo(s), 1 link(s) saved." and see `inbox/001/`.
5. Make it permanent:
   ```
   mkdir -p ~/.config/systemd/user
   cp deploy/deal-scout.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now deal-scout
   loginctl enable-linger $USER             # keep it running after you log out
   journalctl --user -u deal-scout -f    # logs
   ```
   The unit assumes the folder is `~/deal-scout`. Adjust `WorkingDirectory` and `ExecStart` if it is elsewhere.
6. Keep the PC from sleeping, and in the BIOS enable "restore on AC power loss".

## AI analysis on the PC
The bot runs Claude Code (`claude -p`) for each listing, using your Claude subscription.
1. Install Claude Code on the PC (see the Claude Code docs), then run `claude setup-token` and copy the token.
2. In `.env`: `CLAUDE_CODE_OAUTH_TOKEN=<token>`, `ANALYSIS=auto`, and `CLAUDE_BIN=<output of which claude>`
   (systemd does not see your shell's PATH).
3. Test it once by hand: `python3 -m deal_scout.process 1` (from the repo folder).

## Security
- The token lives only in `.env` (mode 600), which is git-ignored. If it leaks, revoke it with @BotFather (`/revoke`).
- Strangers get no reply and nothing is saved. Telegram chats with a bot are not end-to-end encrypted: do not send
  private chats with sellers, or crop out phone numbers.

## Getting the results to the Mac
The analysis runs on the PC. To open the cards and `listings/listings.csv` on the Mac, sync `listings/` (for example
with rsync over SSH or Syncthing).
