# Portals — the outside systems the bots use

One client per system in `bindal/portals/`. Logins are GitHub secrets / the local `.env`; nothing
here is a secret. Each section ends with the lessons that cost a run to learn.

## Myntra partner portal — `portals/myntra.py`

* **Login**: has a reCAPTCHA, never bypassed. A login made by hand in Chrome is reused: the
  `cookie:` request header copied from DevTools (Network → a `partnersapi.myntrainfo.com`
  request), saved with `python scripts/myntra_session.py [MAIN|DBCC|FMCC]` (no argument = the
  files `scripts/main.txt`, `dbcc.txt`, `fmcc.txt`) to the worker KV (`myntra_session`,
  `myntra_session_dbcc`, `myntra_session_fmcc`; MAIN also the secret `MYNTRA_SESSION_JSON`).
  Login cookies `erp.at` (3 h), `erp.rt` (24 h), `session` (30 d) are httpOnly and host-only on
  `partnersapi`: page JavaScript, captures (netcap) and a Chrome extension cannot read them.
* **Keeping it alive**: every run that uses a session saves the cookies the portal renewed back
  to the worker (`save_to_worker`); the 7 AM and 3:30 PM runs touch all three accounts. A session
  used twice a day has lasted for days. **Do not log out of the portal** — that ends the bot's
  session too. Logged out = HTTP 401 → Telegram alert.
* **Accounts**: MAIN 19170 (also SJIT), DBCC 32207, FMCC 35426 — separate logins.
* **Reports** (`storeId 2297`, `partnerType PPMP`, one job ≤ 1 month):
  `Seller_Orders_Report` 4, `Seller_Returns_Report` 12, `Return_Delivery_Report` 84.
  `POST /api/partners/report/jobs/schedule` → poll `GET /api/partners/report/jobs/search` until
  `COMPLETED` → download via `/api/file-manager/getDownloadUrl?url=<successFile>` (redirect to a
  signed link; the blob URL itself is private). A job takes ~5–10 min.
* **Cooldown**: the same report again within 15 minutes is refused with *"Internal server error
  while scheduling…"* (HTTP 200). Different reports can be requested together. After any request
  or refusal, the next request of that report waits ~16 min (`schedule()` waits out one refusal).
* **Reuse**: a job for exactly the same dates (`exact_job`) or covering them (`plan`, SJIT) is
  reused. Statuses like `INTERRUPTED`, `FAILED`, `EXPIRED` are dead — never reused or waited for.
  A job that will not download is asked for again (`collect_or_regenerate`).
* Backfilled months: the order report carries the return status as of the day it is generated.

## AJIO Seller Central — `portals/ajio.py`

* Login: Reliance seller OAuth form, username + password, no OTP (`AJIO_USERNAME/PASSWORD`).
* Dropship **order report**: `POST /malekith/vision/v1/dropship/order/report/download?from=&to=`,
  **RTV report**: `.../dropship/rtv/report/download`; form `pobIds=DV00348890,DV00316239`,
  headers `X-Tenant: AJIO`, `x-pob-id`, `businessModel: B2C`. Max **31 days** per download.
  Direct downloads — nothing to reuse or wait for.
* SPF tickets (list, detail, comment, create) — used by the AJIO SPF bot.

## Flipkart Seller Hub — `portals/flipkart.py`

* Login: JSON POST `/login` (`FLIPKART_EMAIL/PASSWORD`); CSRF from `/getFeaturesForSeller`.
* FBF units: `/napi/metrics/search` filtered on Flipkart's own warehouses (sale bot).
* **Report centre**: `POST /napi/metrics/bizReport/submitReport?reportName=<name>` with
  `{"from_date","to_date","file_format":"EXCEL","type":"ONETIME","filters":[{"values":[from,to],
  "operator":"RANGE","name":"date"}]}` → poll `report/getReportsV2` (paged, 50 per page) until
  `FILE_STORED` → `GET downloadReport/<request_id>_<created_at>.xlsx?token=<id>&sellerId=<sid>`.
  Return analysis uses Tax Reports → **`Sales Report`** and Fulfilment Reports → **`Returns`**
  (the Fulfilment "Orders" report is a different one).
* **Returns retention: about four months.** Returns reports for older months are empty (~10 KB)
  or start mid-month — also when requested by hand. Such a month is left out (never shown as
  0 % returns). The Sales Report carries Return events but cannot split RTO from customer returns
  and has no reasons, so it is not used for returns.
* The local `.env` `FLIPKART_PASSWORD` was stale on 2026-10-18 (GitHub's secret works).

## Reliance SRM — `portals/srm.py`

* Statement of accounts (the AJIO vendor ledger: G4 payments, IC reversals, TOT charges):
  `POST https://srm-rrscm.ril.com/srm/rdl/api/v1/report/statement-off-acc/fetch`, company 9008.
  `payment_rows()` gives the columns of the hand-downloaded `StatementOfAccount*.xlsx`
  (DT, VENDORCODE, DOCDATE `dd.mm.yyyy`, AMOUNT, TEXT, …). Used by AJIO SPF and AJIO ads gap.

## DigiBizz ERP — `portals/erp.py`

* Report preview API (`ERP_USERNAME`, `ERP_PASSWORD`, `ERP_USER_TOKEN`; password AES-encrypted
  like the web app). Report **58** GR GRN (returns booked into the warehouse), report **62**
  Shipping Label Print / packing (`packed(portal, since)`).
* In report 62 the "Order ID" is the **AWB** for AJIO and the **Myntra tracking number** for Myntra.

## OMSGuru — `portals/omsguru.py`, `portals/omsguru_returns.py`

* One session (`OMSGURU_EMAIL/PASSWORD/OTP`), warehouse by warehouse; list pages, order pages,
  search box (AWB / order id). `omsguru_returns.ReturnLookup`: an item counts as received only
  when its order page shows "Return Log RT… (date)". Myntra masters have no SKU, so Myntra
  lookups match the order on its AWBs / store order id.

## VMS — `bindal/vms/vms.py`

* `fkvms.synlabs.io`: login and the AWBs that have a packing-video scan (VMS bot).

## Couriers — `bindal/awb_tracker/`

* BlueDart, Delhivery, Ekart, Shadowfax, Valmo, XpressBees. A courier with no record of an AWB
  is "not found", not an error (Delhivery forgets after ~2–3 months).

## Google Drive — `core/drive.py`

* Service account `ajio-tat-bot@storied-port-441507-k7.iam.gserviceaccount.com` needs Editor on
  each folder / workbook. It can update and create folders but **not create files** ("Service
  Accounts do not have storage quota"): new files go through the relay
  `scripts/drive_relay.gs` (Apps Script web app "Bindal Bots Drive Relay" in
  bhikan.serein@gmail.com, Execute as Me, access Anyone, token-checked).
* `DriveFile.upload()` refuses when the file changed on Drive since it was downloaded.
* The bot cannot delete files you own: to drop data it overwrites the file (e.g. header-only CSV).
