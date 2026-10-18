# Return analysis (monthly + yearly) — `python run.py returns`

**When**: on the 15th: 7:00 AM `--request` (Myntra orders + first returns report per account,
Flipkart Sales Report + Returns), 2:00 PM the report. On demand: `/returns [YYYY-MM]`.

**Windows**: sale = last month 1st to last day; returns = last month 1st to this month's 14th.

**Rules**: the hand-run tool `RETURN ANALYSIS/main.py` as `engine.py` (unchanged; SEP-26 output
identical file by file). Portals: Myntra MAIN / DBCC / FMCC, Ajio, Flipkart.

| Input | Source |
|---|---|
| Myntra orders / returns | reports 4 / 12 per account (two returns jobs 15 min apart, or reused) |
| Ajio orders / RTV | dropship order + RTV downloads (31 days each at most) |
| Flipkart sales / returns | Tax `Sales Report` + Fulfilment `Returns` |
| MasterSheet (+ optional Cost Sheet) | Drive `RETURN ANALYSIS/Supporting` |

**Output** (Drive `RETURN ANALYSIS`, `RETURN_DRIVE_FOLDER_ID`): `Output/<MON-YY>/`, one report per
portal + MYNTRA COMBINED + ALL PORTAL; `Template/<PORTAL> <MON-YY> TEMPLATE.csv`; Telegram table +
ALL PORTAL workbook. A portal that cannot be fetched, or whose Flipkart returns are incomplete, is
left out and named. `--drive-input` uses the Drive `Input/` folder, `--no-upload` is a test run.

**Yearly** (`yearly.py`): each monthly run saves every portal's rows (template columns + STATE) to
`Yearly/<YYYY>/<PORTAL> <MON-YY>.csv` and rebuilds `Output/<YYYY>/` (the same 7 workbooks, January
to December; Monthly Summary shows the months). `--backfill-year 2026` built the year once.
**Flipkart Jan to Jun 2026 are left out** of the yearly: Flipkart's Returns report only holds about
four months, so those months have no returns data (their saved CSVs are emptied).
