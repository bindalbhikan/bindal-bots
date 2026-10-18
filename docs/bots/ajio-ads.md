# AJIO ads / TOT gap — `python run.py ajio-ads`

**When**: 9:00 AM on the 3rd; on the 5th `--if-missing` (only if this month's report is not saved).
On demand: `/ajioads`.

**Result**: `AJIO/Ajio Ads Gap Working/output/AJIO_Simple_Monthly_G4_Charges.xlsx`
(`AJIO_ADS_FOLDER_ID`): per month the G4 amount (statement, by document month) against the TOT
marketing charge (by the narration's Roman month), with / without an assumed 18 % GST, and %.

* Rules: the hand-run tool's `main.py` as `engine.py` (folder via `AJIO_ADS_DIR`).
* Input: the SRM statement of accounts from `STATEMENT_FROM` (2026-01-01) to yesterday. The order
  report and TOT.xlsx in that folder are reference only.
* 18 Oct: marketing total INR 107,461.42 = the hand report.
