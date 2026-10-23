# Myntra SPF master — `python run.py myntra-spf`

**When**: 7:00 AM `--request` (returns 12 + delivery 84 per account), 9:00 AM the master.
On demand: `/myntraspf`.

**Result**: `SPF/MYNTRA/MYNTRA SPF MASTER.xlsx` (`MYNTRA_SPF_FOLDER_ID`), sheets MAIN / DBCC / FMCC
+ SUMMARY / DATE SUMMARY / INPUT LOG; claim aging per SPF POLICY v5 (formulas).

* Rules: the hand-run tool `SPF/MYNTRA/main.py` as `engine.py` (only its folder `MYNTRA_SPF_DIR`
  and an `OMS_RESULTS` hook added). The previous master is the base: rows update, never duplicate.
* Inputs: Myntra returns / delivery (last 30 days) and orders (last 60 days, 30-day jobs) per
  account; ERP GRN + dispatch; from Drive `SPF/MYNTRA/input`: Ticket1 + payment exports per account
  (still dropped by hand), MANUAL RETURN, LEGACY MASTER, and the courier POD sheet at the input
  root (`MYNTRA RETURN PICKUP POD.xlsx` -> PICKUP POD / RETURN POD / RETURN POD DATE, the last
  columns; refreshed every run, kept as they were when the file is missing).
* **Young in-transit returns stay off the sheets**: a return still in transit, not received or
  settled, and created fewer than 60 days ago (`YOUNG_TRANSIT_DAYS`) is not on MAIN / DBCC /
  FMCC. It waits on the hidden sheet `YOUNG IN TRANSIT` of the master (the portal reports only
  cover 30 days, so it would otherwise be lost) and moves to its account sheet on the first run
  after it turns 60 days old, is delivered or is received. OMSGuru checks the parked rows too.
* **SPF tickets from the portal**: every run reads each account's SPF tickets (all statuses,
  `api/spf/v2/getTickets`) as one more ticket report, so TICKET ID / STATUS follow the portal
  without a hand-dropped Ticket1 (a rejection reason from Ticket1 is kept).
* **Auto claims** (`tickets.py`), after the first engine run, on the SPF POLICY v5 timelines:
  * *Return Not Received* (`RTS_ORDER_WAS_NEVER_DELIVERED`): delivered to us per Myntra, not
    received; raised 4 to 7 days after the delivery date (`MYNTRA_SPF_RNR_AFTER_DAYS` /
    `_MAX_DAYS`; policy: within 7 days).
  * *Delayed Returns* (`DELAYED_DELIVERED_RETURN`): in transit or no return tracking; raised 61
    to 120 days after the return / RTO creation date (`MYNTRA_SPF_DELAY_AFTER_DAYS` / `_MAX_DAYS`;
    policy: after 60, up to 120).
  * Only rows with no ticket (master TICKET ID or any portal ticket for the order line - the
    policy allows one claim per order and treats repeats as fraud), a blank FINAL RETURN STATUS
    (OMSGuru received results of this run included), PPMP (not SJIT / FBM) and no courier RETURN
    POD. An account whose portal tickets could not be read gets no claims.
  * Raised like the portal's bulk upload: an xlsx with one `OrderId` column (order line ids, max
    500) put on the claims blob store (`file-manager/getUploadToken/spfJob`), then
    `api/spf/createRnrBulkTickets`. The rows show TICKET ID `BOT JOB <job id>` / STATUS
    `RAISED BY BOT` until the real ticket comes in on the next run.
  * Live only with the repo variable `MYNTRA_SPF_TICKETS=1`; otherwise the report only counts
    the claims due. At most `MYNTRA_SPF_TICKET_MAX` (500) per account and category per run.
* A formula linking another workbook (e.g. `=VLOOKUP(G5,[1]Orders!$E:$F,2,)` pasted into a
  yellow column) reads as empty: written back it made Excel repair the file on every open.
* **Return received**: ERP GRN, then **OMSGuru** (only rows whose FINAL RETURN STATUS is blank,
  created in the last 180 days, matched on AWBs / store order id: "Return Received OMSGuru" or the
  current status), then the manual sheet. The engine runs twice (before and after OMSGuru).
* **Missing ORDER AMOUNT**: the daily order window is 60 days, so older returns had no order
  value (18 Oct: MAIN 4,306, DBCC 1,544, FMCC 177). Each run works out, from the master, the
  calendar months of order reports those returns need (45 days before each return up to it,
  `MYNTRA_SPF_ORDER_LOOKBACK`) and fetches them: a report already on the portal is only
  downloaded; at most `MYNTRA_SPF_OLD_ORDERS_NEW` (1) new one per account per run, asked for after
  the daily reports and waited for only `MYNTRA_SPF_OLD_WAIT_MINUTES` (10); the rest, refusals
  and reports not ready in time are left to the next runs (named in the notes), so they never
  hold up the run (19 Oct: 3 per run with retries and 45-minute waits ran past the 150-minute
  job limit). A value once filled stays in the master.
* **SJIT and FBM orders are not worked** (`NOT_WORKED_MODELS`): their rows stay on the sheets,
  but CLAIM STATUS is always NON ELIGIBLE, OMSGuru does not check them, and the summaries and the
  dashboard leave them out. A row with a blank `model` from a warehouse that is not one of ours
  (ours = the warehouses with PPMP orders: 46148, 24504, 50829, 56765) is marked `FBM` (Myntra
  warehouses 28 / 36 / 81 serve both SJIT and FBM, so the warehouse alone cannot tell).
* 18 Oct: 47,502 to 52,772 rows; GRN 2,011 received; OMSGuru 5,969 checked, 447 received.
  Without SJIT / FBM: MAIN 915 open (was 5,340), 69 eligible (was 435); OMSGuru checks 1,553.

**Later**: raising SPF claims on Myntra (needs captures of the claim form, the ticket export and the
payment download); only no-video claim types, existing-ticket check, caps, dry run first.
