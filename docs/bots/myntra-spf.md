# Myntra SPF master — `python run.py myntra-spf`

**When**: 7:00 AM `--request` (returns 12 + delivery 84 per account), 9:00 AM the master.
On demand: `/myntraspf`.

**Result**: `SPF/MYNTRA/MYNTRA SPF MASTER.xlsx` (`MYNTRA_SPF_FOLDER_ID`), sheets MAIN / DBCC / FMCC
+ SUMMARY / DATE SUMMARY / INPUT LOG; claim aging per SPF POLICY v5 (formulas).

* Rules: the hand-run tool `SPF/MYNTRA/main.py` as `engine.py` (only its folder `MYNTRA_SPF_DIR`
  and an `OMS_RESULTS` hook added). The previous master is the base: rows update, never duplicate.
* Inputs: Myntra returns / delivery (last 30 days) and orders (last 60 days, 30-day jobs) per
  account; ERP GRN + dispatch; from Drive `SPF/MYNTRA/input`: Ticket1 + payment exports per account
  (still dropped by hand), MANUAL RETURN, LEGACY MASTER.
* **Return received**: ERP GRN, then **OMSGuru** (only rows whose FINAL RETURN STATUS is blank,
  created in the last 180 days, matched on AWBs / store order id: "Return Received OMSGuru" or the
  current status), then the manual sheet. The engine runs twice (before and after OMSGuru).
* 18 Oct: 47,502 to 52,772 rows; GRN 2,011 received; OMSGuru 5,969 checked, 447 received.

**Later**: raising SPF claims on Myntra (needs captures of the claim form, the ticket export and the
payment download); only no-video claim types, existing-ticket check, caps, dry run first.
