# Stock-out alert — `python run.py stock`

**When**: 10 AM IST daily (`stock.yml`, GitHub schedule; the Cloudflare cron's 5 free triggers are
all used). By hand: run the workflow.

**Result**: a Telegram message when SKUs are at risk; nothing written.

* Stock: OMSGuru Products > Inventory > **Export Inventory** of every warehouse
  (`OmsGuruClient.export_inventory`: queued, the "download the inventory file" link appears in the
  notifications in 1-10 minutes; a zip with a CSV of Item SkuCode / Qty). 20 Oct 2026: 12,850 SKUs.
* Sold: units on OMSGuru order lines (every channel, cancelled lines left out) of the last
  `STOCK_SALE_DAYS` (14) complete order days (`order_lines`, which gives each line's SKUs).
* At risk: sold `STOCK_MIN_SOLD` (7)+ units and `STOCK_DAYS` (3) days of stock or less at that pace,
  0 in stock included. SKUs not in the inventory (combos) are left out.
* The message: how many SKUs are out of stock (and the units a day they sold) and running out,
  then up to 30 styles (SKU without its size), biggest sellers first, with each size's stock and
  days left or OUT. 20 Oct: 202 SKUs at risk, 161 out of stock (~194 units a day).
