# Daily sale — `python run.py sale`

**When**: 3:30 PM daily (report days = day before yesterday + yesterday, `SALE_REPORT_DAYS`);
7:00 AM `--sjit-request` asks Myntra for the SJIT report. On demand: `/sale`, `/sale today`,
`/sale FROM TO`, `/salepic [YYYY-MM]` (sheet picture).

**Result**: `DAILY SALE REPORT BINDAL/SALE REPORT.xlsx` (`SALE_DRIVE_FILE_ID`), one sheet per month
(blocks per channel, rows GROSS / RETURN / Cancel / NET per day), Telegram day + month table.

**Sources**
* OMSGuru: GROSS per channel by order date (required: no orders at all = workbook untouched).
* Flipkart FBF: GROSS + cancel from Seller Hub metrics on Flipkart's own warehouses.
* Myntra SJIT: `Seller_Orders_Report` of MAIN from the partner portal (saved session); reports
  already on the portal are reused, only uncovered days get a new job. po_type SJIT / FBM only;
  GROSS by created on, Cancel = status F by cancelled on. A CSV dropped into the `sjit` Drive
  folder still counts. Days no report covers are 0 only in empty cells.
* RETURN is typed by hand and never touched.

**Notes**: Since Oct 2026 a COCOBLU block follows AZ/FX/FBA and there is no SNAPDEAL block; older
sheets keep their layout. The run also keeps the DBCC / FMCC Myntra sessions alive and alerts on
Telegram when a Myntra session expired or expires within 3 days.
