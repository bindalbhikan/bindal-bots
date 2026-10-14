"""Google Sheet master: read the _data tab, write every report tab.

Tabs
  Summary      counts, TAT %, courier table           (rewritten each run)
  TAT Report   one row per order, colour-coded         (rewritten each run)
  Late Orders  TAT Result = NOT CORRECT                (rewritten each run)
  Run Log      one line appended per run
  _data        raw master fields; the state that survives between runs
  _meta        tracking as-of timestamp

_data is written RAW with ISO timestamps so it reads back exactly, whatever
locale the spreadsheet uses. The visible tabs are written USER_ENTERED so
Sheets turns the timestamps into real dates.
"""
from __future__ import annotations

import logging

import gspread
import pandas as pd

from ..core.gauth import credentials
from .tat_engine import DATA_COLS, REPORT_COLS, STATUS_COLS, TIME_COLS, normalize_master, summary_rows

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TABS = ["Summary", "TAT Report", "Late Orders", "Run Log", "_data", "_meta"]
RUN_LOG_HEADER = ["Run At", "Mode", "Orders Fetched", "AWBs Tracked", "Master Orders", "New",
                  "Updated", "CORRECT", "NOT CORRECT", "PENDING", "CHECK", "TAT %", "Errors"]

GREEN = {"red": 0.776, "green": 0.937, "blue": 0.808}
RED = {"red": 1.0, "green": 0.780, "blue": 0.808}
AMBER = {"red": 1.0, "green": 0.922, "blue": 0.612}
GREY = {"red": 0.906, "green": 0.902, "blue": 0.902}
HEAD = {"red": 0.122, "green": 0.306, "blue": 0.471}
FILLS = {"On Time": GREEN, "CORRECT": GREEN,
         "Late": RED, "Pending Pickup": RED, "Not Processed": RED, "NOT CORRECT": RED,
         "Picked (Time N/A)": AMBER, "CHECK": AMBER, "PENDING": AMBER, "Next Day Pickup": AMBER,
         "Cancelled": GREY, "CANCELLED": GREY}


def _cell(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if hasattr(v, "item"):  # numpy scalar
        return v.item()
    return v


def _grid(df: pd.DataFrame) -> list[list]:
    return [list(df.columns)] + [[_cell(v) for v in row] for row in df.itertuples(index=False)]


def summary_layout(grid: list[list]) -> dict[str, int]:
    """0-based rows of the Summary tab's parts, found in the grid itself so the
    formatting follows the layout whenever summary_rows() changes."""
    first = {}
    for i, row in enumerate(grid):
        if row and row[0] in ("TAT Result", "TAT %", "Courier"):
            first.setdefault(row[0], i)
    return {"counts_head": first["TAT Result"], "pct": first["TAT %"], "courier_head": first["Courier"]}


class MasterSheet:
    def __init__(self, sheet_id: str):
        self.gc = gspread.authorize(credentials(SCOPES))
        self.book = self.gc.open_by_key(sheet_id)
        self.url = self.book.url
        existing = {ws.title: ws for ws in self.book.worksheets()}
        self.ws = {}
        for name in TABS:
            self.ws[name] = existing.get(name) or self.book.add_worksheet(name, rows=100, cols=20)
        if not self.ws["Run Log"].row_values(1):
            self.ws["Run Log"].update([RUN_LOG_HEADER], "A1")

    # ------------------------------------------------------------ read
    def read_master(self) -> tuple[pd.DataFrame, pd.Timestamp | None]:
        values = self.ws["_data"].get_all_values()
        if len(values) < 2:
            return pd.DataFrame(columns=DATA_COLS), None
        m = normalize_master(pd.DataFrame(values[1:], columns=values[0]))
        meta = self.ws["_meta"].get_all_values()
        as_of = pd.to_datetime(meta[1][0], errors="coerce") if len(meta) > 1 else None
        return m, (None if as_of is None or pd.isna(as_of) else as_of)

    # ------------------------------------------------------------ write
    def _replace(self, name: str, grid: list[list], raw: bool) -> None:
        ws = self.ws[name]
        rows, cols = max(len(grid), 2), max((len(r) for r in grid), default=1)
        ws.clear()
        ws.resize(rows=rows, cols=max(cols, 1))
        if grid:
            ws.update(grid, "A1", value_input_option="RAW" if raw else "USER_ENTERED")

    def write_all(self, df: pd.DataFrame, as_of: pd.Timestamp, source_note: str, summary: dict) -> None:
        report = df[REPORT_COLS]
        late = report[report["TAT Result"] == "NOT CORRECT"]
        # state first: if a later tab fails, the next run still has the full master
        self._replace("_data", _grid(df[DATA_COLS]), raw=True)
        self._replace("_meta", [["as_of"], [_cell(as_of)]], raw=True)
        self._replace("TAT Report", _grid(report), raw=False)
        self._replace("Late Orders", _grid(late), raw=False)
        grid = summary_rows(summary, as_of, source_note)
        self._replace("Summary", [[_cell(v) for v in r] for r in grid], raw=False)
        self._format(len(report) + 1, len(late) + 1, len(summary["couriers"]), summary_layout(grid))
        log.info("Google Sheet updated: %s", self.url)

    def append_run_log(self, row: list) -> None:
        self.ws["Run Log"].append_row([_cell(v) for v in row], value_input_option="USER_ENTERED")

    # ------------------------------------------------------------ formatting
    def _format(self, report_rows: int, late_rows: int, n_couriers: int, at: dict[str, int]) -> None:
        meta = self.book.fetch_sheet_metadata()
        requests = []
        by_id = {s["properties"]["sheetId"]: s for s in meta["sheets"]}
        for name in ("_data", "_meta"):
            requests.append({"updateSheetProperties": {
                "properties": {"sheetId": self.ws[name].id, "hidden": True}, "fields": "hidden"}})

        ncol = len(REPORT_COLS)
        for name, nrows in (("TAT Report", report_rows), ("Late Orders", late_rows)):
            sid = self.ws[name].id
            # drop old conditional rules so they do not pile up run after run
            for i in reversed(range(len(by_id.get(sid, {}).get("conditionalFormats", [])))):
                requests.append({"deleteConditionalFormatRule": {"sheetId": sid, "index": i}})
            requests += [
                {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
                    "frozenRowCount": 1, "frozenColumnCount": 1}},
                    "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
                {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                                "cell": {"userEnteredFormat": {
                                    "backgroundColor": HEAD, "wrapStrategy": "WRAP",
                                    "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                                    "textFormat": {"bold": True, "foregroundColor":
                                                   {"red": 1, "green": 1, "blue": 1}}}},
                                "fields": "userEnteredFormat"}},
                {"setBasicFilter": {"filter": {"range": {"sheetId": sid, "startRowIndex": 0,
                                                         "endRowIndex": max(nrows, 2),
                                                         "startColumnIndex": 0, "endColumnIndex": ncol}}}},
            ]
            for c in TIME_COLS:
                j = REPORT_COLS.index(c)
                pattern = "dd-mmm-yy" if c == "Last Updated" else "dd-mmm-yy hh:mm AM/PM"
                requests.append({"repeatCell": {
                    "range": {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": j, "endColumnIndex": j + 1},
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE_TIME", "pattern": pattern}}},
                    "fields": "userEnteredFormat.numberFormat"}})
            for c in STATUS_COLS:
                j = REPORT_COLS.index(c)
                for txt, color in FILLS.items():
                    requests.append({"addConditionalFormatRule": {"index": 0, "rule": {
                        "ranges": [{"sheetId": sid, "startRowIndex": 1, "startColumnIndex": j,
                                    "endColumnIndex": j + 1}],
                        "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": txt}]},
                                        "format": {"backgroundColor": color}}}}})
            requests.append({"autoResizeDimensions": {"dimensions": {
                "sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": ncol}}})

        sid = self.ws["Summary"].id
        requests += [
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1,
                                      "startColumnIndex": 0, "endColumnIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}},
                            "fields": "userEnteredFormat.textFormat"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": at["pct"], "endRowIndex": at["pct"] + 1,
                                      "startColumnIndex": 1, "endColumnIndex": 4},
                            "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
                            "fields": "userEnteredFormat.numberFormat"}},
        ]
        for r0 in (at["counts_head"], at["courier_head"]):  # 0-based header rows of the two tables
            requests.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r0 + 1,
                                                      "startColumnIndex": 0, "endColumnIndex": 7},
                                            "cell": {"userEnteredFormat": {"backgroundColor": HEAD, "textFormat": {
                                                "bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}}},
                                            "fields": "userEnteredFormat(backgroundColor,textFormat)"}})
        if n_couriers:
            requests.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": at["courier_head"] + 1,
                                                      "endRowIndex": at["courier_head"] + 1 + n_couriers,
                                                      "startColumnIndex": 6, "endColumnIndex": 7},
                                            "cell": {"userEnteredFormat": {"numberFormat": {
                                                "type": "PERCENT", "pattern": "0.0%"}}},
                                            "fields": "userEnteredFormat.numberFormat"}})
        # Summary first in the tab strip
        requests.append({"updateSheetProperties": {"properties": {"sheetId": sid, "index": 0}, "fields": "index"}})
        self.book.batch_update({"requests": requests})
