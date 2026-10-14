# Bindal Myntra Session (Chrome extension)

Sends the Myntra partner portal login to the sale bot, so the bot can fetch the SJIT
`Seller_Orders_Report` without logging in (the portal login has a reCAPTCHA).

The login cookies are httpOnly: page JavaScript, console snippets and page-level network
captures cannot read them. The extension reads them with `chrome.cookies`.

## Install (once)

1. Chrome > `chrome://extensions` > turn on **Developer mode** (top right).
2. **Load unpacked** > pick this `myntra-extension` folder.
3. Click the extension icon (pin it from the puzzle-piece menu), paste the token
   (`DASHBOARD_TOKEN` from the project `.env`), **Save**.

## Use

Log in on https://partners.myntrainfo.com in the same Chrome. About 30 seconds later the
extension sends the login to the Telegram worker (`/api/myntra-session`), and the bot posts
"Myntra session saved" on Telegram. Badge: **OK** sent, **OFF** not logged in on the portal,
**SET** token missing, **ERR** sending failed (the popup says why).

It sends again only when the login cookies change (a new login), and checks every 6 hours.
The sale run reads the latest session from the worker and alerts on Telegram when it has
expired or expires within 3 days: then just log in on the portal again.
