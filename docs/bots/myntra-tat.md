# Myntra courier scorecard — `python run.py myntra-tat`

**When**: 6:30 PM IST daily (`myntra-tat.yml`, GitHub schedule). By hand: run the workflow.

**Result**: one Telegram message, nothing written.

* Pickup: the partner portal's Seller_Orders_Report of MAIN, DBCC and FMCC (`MYNTRA_TAT_ACCOUNTS`),
  column "fmpu date" (first-mile pickup); reports already on the portal are reused (the Myntra SPF
  run asks for 60 days every morning), a missing day gets a new one (waited for 15 minutes).
* Deadline: OMSGuru's SLA Date of the same order, matched on the report's "order tracking number"
  (OMSGuru's AWB for Myntra).
* Orders created in the last `MYNTRA_TAT_DAYS` (7) complete days (up to the day before yesterday),
  cancelled left out. Late = picked after the SLA or still not picked past it; our side = packed
  after the SLA. Per account and courier code: orders, late, on-time %.
* The portal answers 500 now and then: the session check is tried twice; an account that cannot
  be read is noted and the others still count.
* Flipkart has no pickup time source yet: Ekart's tracking gives only the current status, and
  OMSGuru's "In Transit" time is its sync time.
* 20 Oct 2026 (12-18 Oct): DBCC + FMCC 1,012 orders, 90% on time, the 92 late all on the courier side.
