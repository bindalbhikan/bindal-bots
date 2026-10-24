# AJIO dispatch TAT — `python run.py ajio-tat`

**When**: 5:30 PM daily (OMSGuru window = yesterday + today, `TAT_LOOKBACK_DAYS`). On demand:
`/ajiotat [days]`.

**Result**: one master `AJIO_TAT_MASTER.xlsx` in Drive `AJIO/Ajio Daily Order Tat Check Working`
(`TAT_DRIVE_FOLDER_ID`, found by name; a missing one is seeded from the old file
`TAT_DRIVE_FILE_ID` and created through the relay). Orders from 2026-06-01 (rebuilt 20 Oct
2026 with `days=141`), plus 26 hand-added late-May orders (`--orders`).

* **10:10 rule**: orders from yesterday 10:00 to today 10:00 must be processed and picked up today
  (before 10:00: due the order date; from 10:00: the next day).
* OMSGuru order pages (only orders new or still open) + courier tracking for AWBs whose pickup is
  not confirmed: Process / Pickup status, TAT Result (CORRECT / NOT CORRECT / CHECK / PENDING /
  CANCELLED).
* **ERP label print**: `Label Printed` / `ERP Label Time` / `ERP Label By` from ERP report 62
  (portal AJIO; its "Order ID" is the AWB), read from the window start (a week at least); the first
  print counts. 18 Oct: 3,104 of 3,286 orders matched.
* **OMSGuru list limit**: the order list stops at ~3,000 orders (10 pages) whatever the filter, so
  windows are listed 7 days at a time (`LIST_DAYS`), a piece near the limit split in two.
* **AJIO order report** (Seller Central Dropship report, `AJIO_USERNAME`): for orders still missing
  them, `Cust Order No` (AJIO's FN... / EX... number, matched on Order ID = seller order) and
  `Pickup Time` from its "Pickup Timestamp" where courier tracking gave none. Couriers keep no
  history after a few months and OMSGuru logs no pickup; on 1,512 Sep-Oct orders the AJIO time
  equals the courier pickup scan. An order OMSGuru shows In Transit / Delivered / returned with no
  pickup time at all is Picked (Time N/A) -> CHECK.
* **`--orders`** (workflow input `orders`): add hand-picked orders whatever their date - FN / EX
  numbers (via the AJIO report, last 180 days), seller order numbers, invoices or AWBs.
* The saved master is tested as a zip before upload (a local run from G: once uploaded a damaged
  file).
* Telegram: TAT table, then the **courier scorecard**, then pickups pending. The scorecard: per
  courier, orders of the last 7 complete order days (every deadline passed, i.e. up to the day
  before yesterday at the 5:30 PM run), late pickups (after the deadline or still waiting past it),
  on-time % and the last day's %; that day's NOT CORRECT split into our side (processing late) and
  the courier's (pickup late); an alert for a courier under `COURIER_ALERT_PCT` (70) % on time
  `COURIER_ALERT_DAYS` (3) days in a row (days with 5+ orders); 2 lines from the agent's free
  model (left out when it is not available). Pickups pending: a ready-to-copy mail with the AWB
  table when it fits in one message; with more AWBs the mail comes alone and every AWB (courier,
  Over / Due, order time, RTS, deadline) is in the attached `AJIO_pickup_pending_*.xlsx`.
* **Pickup tickets** (on by default, `AJIO_PICKUP_TICKETS`): one AJIO "pickup not done" ticket per
  order of the pickup pending list, Over and Due alike (since 24 Oct 2026; `TICKET_ONLY_OVER=1` goes
  back to orders `TICKET_GRACE_HOURS` past the deadline), when the AJIO report and the courier's site
  show no pickup; at most `TICKET_CAP` (200) per run, never twice per order (30 days), a follow-up
  comment every 24 h while still not picked up. `TICKET_DELAY_MS` (30) between two ticket calls. The ticket id goes into the master's Pickup Ticket.
