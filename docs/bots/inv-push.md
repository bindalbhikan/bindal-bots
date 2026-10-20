# OMSGuru inventory push — `python run.py inv-push`

**When**: every 3 hours, 12 / 3 / 6 / 9 AM and PM IST (GitHub schedule in `inv-push.yml`; the
Cloudflare cron's 5 free triggers are all used). On demand: run the workflow by hand
(`dry_run` = 1 only lists the channels).

* Channel > List Channels in OMSGuru: every channel with **Inventory Sync Enabled = Yes** gets
  Related Actions > **Push Inventory** (all its listings), one after the other. 20 Oct 2026: 12
  channels (COCOBLU, Nykaa Fashion, Ajio Api, Myntra x4, Snapdeal, Amazon x2, Meesho, Flipkart).
* `INV_PUSH_SKIP` (repo variable): channel ids never pushed, comma separated.
* Telegram only when a channel's push fails (the others are still pushed) or the run fails.
* GitHub turns off scheduled workflows of a repo with no commits for 60 days; it says so by email.
