# Myntra login session

The Myntra partner portal login has a reCAPTCHA, so the bots cannot log in themselves. They reuse
a login you make in Chrome. Each seller account has its own login:

| Account | Seller id | File |
|---|---|---|
| MAIN | 19170 | `scripts/myntra/main.txt` |
| DBCC | 32207 | `scripts/myntra/dbcc.txt` |
| FMCC | 35426 | `scripts/myntra/fmcc.txt` |

Every run renews the saved sessions, so this is needed only when Telegram says
**"Myntra session expired"** (or "expires in N days") for an account.

## Steps (per account that expired)

1. In Chrome, open <https://partners.myntrainfo.com> and log in with **that** account.
2. Press **F12** > **Network** tab > reload the page (F5).
3. Click any request to `partnersapi.myntrainfo.com` > **Headers** > **Request Headers**.
4. Copy the request headers (all of them, or just the `cookie:` line) into the account's file
   above (create it in this folder; Notepad is fine).
5. In the project folder run:

   ```
   python scripts/myntra/myntra_session.py
   ```

   It reads every `.txt` file here, checks each login on the portal (logged in, and really that
   account), and saves it to the Telegram worker (where the bots read it). MAIN is also saved to
   the GitHub secret `MYNTRA_SESSION_JSON`. One account only: `python scripts/myntra/myntra_session.py DBCC`.
6. **Delete the `.txt` files** afterwards: they hold a live login.

## Rules

* **Do not log out** of the portal in that Chrome window: logging out ends the bot's session too.
  Just close the tab.
* Never paste the cookies anywhere else (chat, email, git). The `.txt` files are git-ignored.
* The cookies (`erp.at`, `erp.rt`, `session`) are httpOnly: only DevTools shows them; browser
  extensions and page captures cannot read them.

Check: send `/sale` in Telegram, or wait for the next run; no "session expired" message means it
worked.
