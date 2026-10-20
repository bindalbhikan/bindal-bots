# SLA warning — `python run.py sla`

**When**: 12 PM and 3 PM IST (`sla.yml`, GitHub schedule; the Cloudflare cron's 5 free triggers
are all used). By hand: run the workflow.

**Result**: a Telegram message only when there is something to do; nothing is written.

* OMSGuru order list, every channel and warehouse, orders of the last `SLA_ORDER_DAYS` (8) order
  days (`order_lines`, shared with the sale and morning reports; each day its own window, a window
  near the list's ~3,000-line limit read in two halves).
* At risk = status New / Pending / Processing / Packed (not ready to ship yet) and the SLA Date
  column today or earlier. Over = SLA already passed; Today = due later today.
* The message: counts per channel (Over / Today), then the orders by channel (order id, status,
  OVER or the SLA time), at most 40 listed.
* 20 Oct 2026, 4 PM: 7,402 order lines read, 1 at risk.
