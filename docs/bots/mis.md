# Monthly MIS — `python run.py mis`

**When**: the 2nd of every month, 11 AM IST (`mis.yml`, GitHub schedule). By hand: run the
workflow (optionally with a `day` in the month after the one wanted).

**Result**: one Telegram message, nothing written. Last month against the month before.

* Per channel (OMSGuru order lines by order date, every channel line apart): order value (the
  lines' "Total", Rs, cancelled included), its change, units, cancel %; the sale after cancels.
* With the `MIS_RATES` repo variable (JSON, kept out of the public repo), commission, shipping and
  net per channel: `{"Flipkart": {"commission": 22, "shipping": 70}, "Myntra": {"commission": 25,
  "shipping": 0}, ...}` - commission % of the sale after cancels, shipping Rs per unit kept; a key
  covers every channel line starting with it ("Myntra" = MAIN, PPMP, DBCC, FMCC). Channels with
  no rate are named.
* 🧭 a plan for this month from the agent's free model (plan M5), told to use only the numbers
  given and never to invent budgets, targets or causes.
* 20 Oct 2026 (September): Rs 103.71 lakh (+0% on August), 92.99 lakh after cancels, 17,182 units.
