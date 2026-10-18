# AJIO dispatch TAT — `python run.py ajio-tat`

**When**: 5:30 PM daily (OMSGuru window = yesterday + today, `TAT_LOOKBACK_DAYS`). On demand:
`/ajiotat [days]`.

**Result**: one master `AJIO_TAT_MASTER.xlsx` in Drive `AJIO/Ajio Daily Order Tat Check Working`
(`TAT_DRIVE_FOLDER_ID`, found by name; a missing one is seeded from the old file
`TAT_DRIVE_FILE_ID` and created through the relay). Orders from 2026-08-01.

* **10:10 rule**: orders from yesterday 10:00 to today 10:00 must be processed and picked up today
  (before 10:00: due the order date; from 10:00: the next day).
* OMSGuru order pages (only orders new or still open) + courier tracking for AWBs whose pickup is
  not confirmed: Process / Pickup status, TAT Result (CORRECT / NOT CORRECT / CHECK / PENDING /
  CANCELLED).
* **ERP label print**: `Label Printed` / `ERP Label Time` / `ERP Label By` from ERP report 62
  (portal AJIO; its "Order ID" is the AWB), read from the window start (a week at least); the first
  print counts. 18 Oct: 3,104 of 3,286 orders matched.
* Telegram: TAT table + pickups pending.
