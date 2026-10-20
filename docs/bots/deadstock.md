# Dead stock report — `python run.py deadstock`

**When**: the 1st of every month, 11 AM IST (`deadstock.yml`, GitHub schedule). By hand: run the
workflow.

**Result**: one Telegram message, nothing written.

* Stock: OMSGuru inventory export of every warehouse (as in the stock-out alert).
* Sold: units on OMSGuru order lines of the last `DEAD_SALE_DAYS` (90) complete days, every
  channel, cancelled lines left out (about 100,000 lines: the run takes 10-15 minutes).
* Dead: `DEAD_MIN_STOCK` (5)+ in stock, nothing sold. Slow: sold, but `DEAD_SLOW_DAYS` (180)+ days
  of stock at that pace. Each listed by style, most units first (25 styles), sizes with stock.
* 3 lines from the agent's free model on what to do (clearance, bundles, another channel, stop
  reorders).
* 20 Oct 2026: dead 1,177 units, slow 3,288 units (DRESS-XC-1-N16 alone 952).
