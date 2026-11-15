# Copying listings to a Google Sheet

After each analysis the bot sends the listing's row (the same columns as `listings/listings.csv`) to a small script
inside your spreadsheet, which adds the row or updates it if the listing is already there. No Google Cloud project or
key files: just a script you paste and a shared secret.

1. Make a secret on the computer that runs the bot:
   ```
   python3 -c "import secrets; print(secrets.token_urlsafe(24))"
   ```
   Put it in `.env` as `GOOGLE_SHEET_SECRET=...`.
2. Create a new Google Sheet (sheets.new). Keep it private; there's no need to share it.
3. In the sheet: **Extensions > Apps Script**. Delete what's there, paste the contents of `deploy/google-sheet.gs`,
   and replace `PASTE-THE-SAME-SECRET-AS-GOOGLE_SHEET_SECRET-IN-.env` with the secret from step 1. Save.
4. **Deploy > New deployment**. Gear icon > **Web app**. Execute as: **Me**. Who has access: **Anyone**.
   Deploy, allow the permissions it asks for (Google warns that the app is unverified, because it's your own
   script: Advanced > Go to ... > Allow), and copy the **Web app URL** (it ends in `/exec`).
5. Put it in `.env` as `GOOGLE_SHEET_URL=https://script.google.com/macros/s/.../exec` and restart the bot.
6. Copy the listings you already have: `python3 -m deal_scout.sheet`. A "Listings" tab appears with a header row.
7. Optional: put the spreadsheet's own address (from the browser, `https://docs.google.com/spreadsheets/d/...`) in
   `.env` as `GOOGLE_SHEET_LINK=...`. Every summary then gets an "Open sheet" button, and `/sheet` sends it anytime.

From then on every analysis updates the sheet. If the sheet can't be reached, the Telegram summary says so; the CSV is
saved anyway, and `python3 -m deal_scout.sheet` copies everything again (it updates rows, it doesn't duplicate them).

Notes:
- "Anyone" means anyone with the URL can *send* to the script, which rejects anything without the secret. Nobody can
  read the sheet through it. Keep the URL and the secret in `.env` only.
- You can sort, filter, add your own columns and change `status` in the sheet. Re-analysing a listing updates the
  bot's columns but keeps your status and your own columns.
- If you change the script later, use **Deploy > Manage deployments > Edit > New version**, so the URL stays the same.
