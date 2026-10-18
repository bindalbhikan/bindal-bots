# AJIO SPF — `python run.py ajio-spf`

**When**: 9:00 AM daily. Wed + Sat the full run (tickets + replies); the other days
`--master-only` (master updated, nothing posted). On demand: `/ajiospf` (live) or `/ajiospf dry`.

**Result**: `SPF/AJIO/AJIO MASTER.xlsx` (`SPF_MASTER_DRIVE_FILE_ID`).

1. **Fetch**: AJIO RTV report + SPF tickets, ERP GRN and dispatch, SRM ledger, the manual register
   (Drive).
2. **Build**: the hand-run master script (`master_engine.py`, unchanged). Return received:
   **ERP GRN first** ("Return Received ERP"), **then OMSGuru** for the returns still open (live
   order lookups give a return sheet the script reads), **then the manual register**.
3. **Raise** SPF tickets for returns past a claim threshold (`claims.py`: dispute 5 to 365 days,
   not delivered 60 to 90 days); **follow up** where AJIO answered (`followup.py`, Gemini drafts).
4. **Publish**: master to Drive, actions file + summary to Telegram.

`SPF_DRY_RUN=1` (default) lists instead of posting; only an explicit 0 posts. Any failed source turns
the run into a dry run; per-run caps (`MAX_RAISE_PER_RUN`, `MAX_REPLIES_PER_RUN`); a failed "ticket
already exists?" check skips that ticket.
