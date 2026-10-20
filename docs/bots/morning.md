# Morning report — `python run.py morning`

**When**: 9:30 AM IST daily (`morning.yml`, GitHub schedule; the Cloudflare cron's 5 free triggers
are all used). By hand: run the workflow, optionally with a `day`.

**Result**: one Telegram message, nothing written anywhere: yesterday's sale in units per channel
against the same weekday a week before, with the change in %, the total and yesterday's cancels.

* OMSGuru: every channel on its own line, the Myntra accounts apart (MAIN = channel MYNTRA, PPMP,
  DBCC = DressBerry CC, FMCC = Fashfun Murcia CC); units by order date, each day read on its own
  (`units_by_channel`, shared with the sale report; a window near the list's ~3,000-line limit is
  read in two halves). Cancelled = item status Cancelled / Cancelled Before Shipping / Removed
  Before Shipping.
* Flipkart FBF from Seller Hub and Myntra SJIT from the partner portal, as in the sale report; the
  SJIT report asked for at 7 AM is waited for 5 minutes at most (`SJIT_WAIT_MINUTES`), else noted.
* 2-3 lines on the numbers in English from the agent's free model (Workers AI, `bindal/agent/llm.py`);
  left out when the model is not available.
* **Sudden drops** (plan E5): a channel that sold `DROP_MIN_UNITS` (20)+ units on last week's
  same day and sold nothing yesterday (🔴, check the listing / account) or fell `DROP_PCT` (40) %+ (⚠️).
* Returns are not in it: the sale report's RETURN is typed by hand.
