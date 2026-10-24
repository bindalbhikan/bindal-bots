# Myntra daily reports — `python run.py myntra-reports`

**When**: 10 AM IST daily (not earlier: a report asked for at 7 AM had only 23 of the day's
239 MAIN orders) (`myntra-reports.yml`, GitHub schedule). By hand: run the workflow
(`date` = another day, YYYY-MM-DD).

* Each Myntra account (MAIN / DBCC / FMCC, `MYNTRA_REPORT_ACCOUNTS`), saved portal sessions:
  Seller_Orders_Report (4, the last 30 days; the file keeps yesterday's lines), Seller_Returns_Report (12) and Return_Delivery_Report (84) for
  yesterday, Seller_Listings_Report (14, a snapshot, no dates) of today. Different reports are
  asked for together; a job already on the portal for the same dates is reused.
* Files: Drive `BINDAL EXPORTS/AGENT WORK/MYNTRA DAILY REPORTS/<yesterday>/`, e.g.
  `MAIN Orders 2026-10-23.csv`, `MAIN Listings 2026-10-24.csv` (`AGENT_WORK_FOLDER_ID`).
* Telegram: one message, first the total return delivery of all accounts (Return vs RTO), then per account each report's rows (orders: lines, value, cancelled;
  returns / delivery: Return vs RTO; listings: SKUs, active) and the folder link. A report not
  got is named at the end.
* `AGENT WORK/MYNTRA DAILY RETURN DELIVERY REPORT.xlsx`: one row per account and day - DATE, PORTAL
  (MYNTRA MAIN / DBCC / FMCC), CUSTOMER RETURN, RTO, CANCELLED, TOTAL. The delivery report has
  RTO and cancelled as one type (RTO_or_Cancel): the order's status in the 30-day orders report
  tells them apart (F = cancelled, else RTO). A day run again replaces its rows.
