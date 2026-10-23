# -*- coding: utf-8 -*-
"""
MYNTRA SPF MASTER builder
=========================

The hand-run tool BINDAL EXPORTS/SPF/MYNTRA/main.py, kept unchanged except that its folder can
be set with MYNTRA_SPF_DIR; the bot (bindal/myntra_spf/pipeline.py) fetches the inputs into a
work folder, runs main() there and puts the master back on Drive.

Merges the Myntra returns reports for 3 accounts (MAIN / DBCC / FMCC) into a single
workbook with one sheet per account, removes duplicates, and writes the claim-aging
formulas defined by SPF POLICY.pdf.

Usage
-----
    python main.py                # build everything
    python main.py --dry-run      # report what would happen, write nothing
    python main.py --no-cache     # re-parse every input instead of using the cache

Requires: openpyxl, xlsxwriter, and python-calamine (optional but worth having - it
is the fast xlsx reader; without it the script falls back to openpyxl and takes about
four times as long).

    pip install openpyxl xlsxwriter python-calamine

Speed
-----
Every input file is parsed once and its result cached on disk, keyed on the file's
size and mtime, under %LOCALAPPDATA%/myntra-spf-cache (outside the project so Google
Drive does not sync it). A re-run after dropping in one new report only parses that
file. The run prints where its time went; if a change to a parser ever needs the old
cache thrown away, bump CACHE_VERSION.

Input folder layout (drop the portal CSV/XLSX in as-is, no renaming needed)
--------------------------------------------------------------------------
    input/
      MAIN/                       # seller 19170
        Return In Transit/        # Seller Returns Report   (monthly or full range)
        Return Delivered/         # Return Delivery Report
        Ticket1.xlsx              # SPF ticket report (fills TICKET ID + STATUS +
                                  #   REJECTION REASON). Either layout works - the
                                  #   original portal export or the newer wide one
        payment till <date>.xlsx  # Myntra payment report (SPF PAID, UTR NO,
                                  #                        RETURN CHARGE)
        Order Report/             # Seller Orders Report, monthly (fills ORDER AMOUNT)
      DBCC/  ...same...           # seller 32207
      FMCC/  ...same...           # seller 35426
      MANUAL RETURN/              # your own sheet with the extra 'RECD' column
      GR GRN Report*.xlsx         # ERP goods-receipt export (marks OMSGURU STATUS)
      Dispatch.xlsx               # ERP dispatch export (DISPATCH STATUS / DATE / VOUCHER)

Report type is detected from the file's headers, not from the folder name, so a file
still works if it is dropped in the wrong place.

Each run first tidies input/: a report in the wrong folder is moved to the folder of
its type (the account comes from the seller id in the portal's file name), and the
in-transit, delivered and order reports are renamed to the period they cover, e.g.
"2026-08-01 to 2026-08-28.csv". Two downloads of one period keep both ("... (2).csv");
an exact copy of a file already there is deleted. Just drop new downloads anywhere
under input/ - even at the top - and the next run files them.

After the master is written, every in-transit / delivered report whose returns are all
in it, and every order report more than ORDER_KEEP_DAYS past its period, is deleted -
input/ keeps only files with data still pending. Payment, ticket, GRN and dispatch
exports are never deleted; replace them with the newer download.

Rules applied
-------------
1. "Return In Transit" reports also contain delivered rows. Those are NOT kept as rows -
   only return / RTO shipments still in transit are. The delivered picture comes from the
   "Return Delivery Report" instead. The dropped rows are still read as a lookup, because
   they carry `model`, which the delivery report does not have at all.
2. No duplicates. Each row is keyed on (order_line_id, return_id). If the same return
   appears in several monthly files, the newest file wins and the columns from both
   report types are merged into one row.
3. The previous master is loaded as the base, so re-running after adding new reports
   updates rows in place - it never appends a second copy and never loses old rows.
4. Columns you fill in yourself (OMSGURU STATUS, MANUAL RETURN STATUS) are never
   overwritten, except that a hit in the manual return sheet sets MANUAL RETURN STATUS
   to "Return Received", and a GRN hit sets OMSGURU STATUS to "Return Received ERP".
5. The GR/GRN report is the ERP receiving a parcel into the warehouse. Its
   `Order Tracking Id` (against the AWB columns) or its `Order Item Id` (the store order
   id, marking every item of that order) is proof the return came back, so it
   overwrites OMSGURU STATUS - a booked-in parcel outranks an in-flight status.
6. The payment report pays SPF claims out as NOD lines (Order_Type = NOD, an SPF
   marker in NOD_Comment). Those carry an order_line_id, so the claim amount
   (Settled_Amount - NOT paymentAmount, which is the whole NEFT batch total) and the
   UTR land on the matching row.
7. The payment report's Forward lines are the sale settlement - what Myntra actually
   paid out for the order after commission and logistics. Summed per order line into
   ORDER SETTLEMENT.
8. The same payment report carries the return charge: on a Reverse line
   Logistics_Commission is the return logistics fee including GST. It lands in RETURN
   CHARGE, matched on return_id.
9. The Seller Orders Report (input/<ACCOUNT>/Order Report/, one CSV per month) is the
   only report carrying the order value. Its `final amount` lands in ORDER AMOUNT and
   its `store order id` in STORE ORDER ID, matched on `order line id`.
10. Returns created before EARLIEST_RETURN (2026-01-01) are dropped. The 2026-01 reports
   reach back into December 2025, so without this every run would resurrect the 2025
   rows that were deliberately deleted from the master.
11. Output goes to the root folder: MYNTRA SPF MASTER.xlsx, sheets MAIN / DBCC / FMCC.
"""

import argparse
import csv
import datetime as dt
import filecmp
import hashlib
import os
import pickle
import re
import shutil
import sys
import tempfile
import time
import zipfile

import openpyxl
from openpyxl.utils import get_column_letter as L

# The reading engine. python-calamine is a Rust xlsx parser and is 3-5x faster than
# openpyxl on these reports (the 83 MB payment export: 94s with openpyxl, ~20s with
# calamine), so it is used whenever it is installed. openpyxl stays as the fallback,
# and is always what reads the previous master, because that needs formula TEXT out of
# the cells and calamine only ever returns values.
try:
    import python_calamine
except ImportError:
    python_calamine = None

# The writer. xlsxwriter streams the sheet out row by row instead of building a cell
# object per cell, which matters at ~1.6M cells.
import xlsxwriter

# --------------------------------------------------------------------------- config
# The bot points this at a work folder (MYNTRA_SPF_DIR) holding input/ and the master
HERE = os.environ.get('MYNTRA_SPF_DIR') or os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(HERE, 'MYNTRA SPF MASTER.xlsx')
INPUT_DIR = os.path.join(HERE, 'input')
BACKUP_KEEP = 10

SHEETS = ['MAIN', 'DBCC', 'FMCC']

# Sheet layout: a SUBTOTAL band, then the headers, then the rows.
SUBTOTAL_ROW = 1
HEADER_ROW = 2
FIRST_DATA_ROW = 3

# Returns created before this are not kept. The 2026-01 reports reach back into
# December 2025, so without a cutoff every run would resurrect the 2025 rows that were
# deliberately removed from the master on 2026-09-09. A row with no return_created_date
# at all is never dropped by this - only a date that is genuinely older.
EARLIEST_RETURN = dt.datetime(2026, 1, 1)

# A return still in transit, not yet received, and created fewer than this many days ago
# is too young to act on. Each run asks in the terminal whether such rows go into the
# sheet (y) or are left out (n); anything already received is always kept.
YOUNG_TRANSIT_DAYS = 60
# Rows left out (n) wait on this hidden sheet of the master and come back on their own.
PARKED_SHEET = 'YOUNG IN TRANSIT'
PARKED_ACCOUNT = 'ACCOUNT'
PARKED = {}

# Raw columns, in the requested order. Both report types carry `type` / `status`, so
# that pair appears once.
RAW = [
    'seller_id', 'warehouse_id', 'model',
    'seller_order_id', 'order_group_id', 'order_line_id', 'return_id',
    'return_created_date', 'RETURN MONTH', 'deliver_to_seller_date',
    'type', 'status', 'return_status',
    'forward_tracking_number', 'forward_tracking_number_2', 'return_tracking_number',
]

# The working columns, matching example.xlsx columns AZ:BG, plus REJECTION REASON
# which comes from the ticket report alongside TICKET ID and STATUS.
WORK = ['AGE (DAYS)', 'CLAIM STATUS', 'CLAIM TYPE', 'OMSGURU STATUS',
        'MANUAL RETURN STATUS', 'FINAL RETURN STATUS', 'TICKET ID', 'CLAIM TAT', 'STATUS',
        'REJECTION REASON', 'ORDER AMOUNT', 'ORDER SETTLEMENT', 'RETURN DEBIT',
        'RETURN CHARGE', 'SPF PAID', 'NET', 'UTR NO']

# STORE ORDER ID is raw portal data, not a working column, so it sits with the ids -
# straight after seller_order_id - and is deliberately kept out of WORK so the yellow
# fill does not apply to it.
_SID_AFTER = 'seller_order_id'
# Dispatch data comes from the ERP's dispatch sheet. Like STORE ORDER ID it is recorded
# data rather than a working column, so it follows the tracking numbers, unfilled.
DISPATCH_COLS = ['DISPATCH STATUS', 'DISPATCH DATE', 'DISPATCH VOUCHER']
# The POD checks come from the courier POD sheet (input/MYNTRA RETURN PICKUP POD.xlsx),
# one column per POD type plus the return POD's date, and are the last columns of the
# sheet. Each type name is also the TYPE value the POD sheet uses.
POD_TYPES = ['PICKUP POD', 'RETURN POD']
RETURN_POD_DATE = 'RETURN POD DATE'
POD_COLS = POD_TYPES + [RETURN_POD_DATE]
HEADERS = (RAW[:RAW.index(_SID_AFTER) + 1] + ['STORE ORDER ID']
           + RAW[RAW.index(_SID_AFTER) + 1:] + DISPATCH_COLS + WORK + POD_COLS)
C = {h: L(i + 1) for i, h in enumerate(HEADERS)}

# Rewritten on every run
FORMULA_COLS = ['RETURN MONTH', 'AGE (DAYS)', 'CLAIM STATUS', 'CLAIM TYPE',
                'FINAL RETURN STATUS', 'NET']
# Filled in by hand - the script preserves whatever is there
MANUAL_COLS = ['OMSGURU STATUS', 'MANUAL RETURN STATUS']
# Filled from the ticket report; the previous value survives if no ticket is found
TICKET_COLS = ['TICKET ID', 'CLAIM TAT', 'STATUS', 'REJECTION REASON']
# Filled from the payment report; the previous value survives if no payout is found
PAYMENT_COLS = ['SPF PAID', 'UTR NO', 'RETURN CHARGE', 'ORDER SETTLEMENT',
                'RETURN DEBIT']
# Filled from the order report; the previous value survives if the order is not in it
ORDER_COLS = ['ORDER AMOUNT', 'STORE ORDER ID', 'DISPATCH STATUS', 'DISPATCH DATE',
              'DISPATCH VOUCHER']

DATE_COLS = {'return_created_date', 'DISPATCH DATE', RETURN_POD_DATE}
DTIME_COLS = {'deliver_to_seller_date'}
NUM_COLS = {'seller_id', 'warehouse_id', 'order_group_id', 'order_line_id',
            'return_id', 'forward_tracking_number', 'forward_tracking_number_2',
            'return_tracking_number'}
MONEY_COLS = {'ORDER AMOUNT', 'ORDER SETTLEMENT', 'RETURN DEBIT', 'RETURN CHARGE',
              'SPF PAID', 'NET'}

JUNK = {'#N/A', '#N/A!', 'N/A', '#VALUE!', '#REF!', '#NAME?', '#DIV/0!',
        'NA', 'NULL', 'NONE', 'NAN', '-', ''}

# The portal writes 1970-01-01 as its "empty date" placeholder
DUMMY_DATES = {'1970-01-01', '1970-01-01 00:00:00', '1970-01-01 00:00:00.000'}


# =========================================================== report type detection
RETURNS_MARKERS = {'return_status', 'forward_tracking_number', 'model'}
DELIVERY_MARKERS = {'deliver_to_seller_date', 'forward_tracking_number_1'}
# The ticket report comes in two layouts: the original portal export and the newer
# one, which carries many more columns and no longer has a 'rejectionReason'. Both
# name the ticket and the order line the same way; only the older one is guaranteed
# to carry issueCategory, so either pair identifies the report.
TICKET_MARKERS = {'ticketId', 'orderLineId'}
TICKET_MARKERS_OLD = {'ticketId', 'issueCategory'}
# Your manual return sheet: a normal returns report with a 'RECD' column added
MANUAL_MARKERS = {'RECD'}
# An older MYNTRA SPF MASTER workbook, kept only to harvest the columns you filled in
LEGACY_MARKERS = {'order_line_id', 'OMSGURU STATUS'}
# The Myntra payment report - every payout line for the account, SPF claims included
PAYMENT_MARKERS = {'utrNumber', 'paymentAmount', 'NOD_Comment'}
# The Myntra Seller Orders Report, one CSV per month per account. It carries the order
# value, which none of the returns reports do.
ORDER_MARKERS = {'order line id', 'final amount', 'total mrp'}
# The ERP's Goods Receipt Note export. A row here means the parcel was physically
# booked into the warehouse, so its tracking id proves the return came back.
GRN_MARKERS = {'Voucher No.', 'Order Tracking Id', 'Stock Type'}
# The ERP's dispatch export: one row per parcel that left the warehouse
DISPATCH_MARKERS = {'Voucher No.', 'Order ID', 'Created By', 'Order Portal Name'}
# The courier POD sheet: RETURN and PICKUP sheets of Tracking ID / DATE / TYPE
POD_MARKERS = {'Tracking ID', 'TYPE', 'Source File'}


def detect_kind(headers):
    h = set(headers)
    if GRN_MARKERS <= h:
        return 'GRN'
    if DISPATCH_MARKERS <= h:
        return 'DISPATCH'
    if POD_MARKERS <= h:
        return 'POD'
    if ORDER_MARKERS <= h:
        return 'ORDER'
    if PAYMENT_MARKERS <= h:
        return 'PAYMENT'
    # checked first: an old master also carries return_status / model / forward AWB, so
    # it would otherwise be mistaken for a Seller Returns Report
    if LEGACY_MARKERS <= h:
        return 'LEGACY'
    if MANUAL_MARKERS <= h:
        return 'MANUAL'
    if TICKET_MARKERS <= h or TICKET_MARKERS_OLD <= h:
        return 'TICKET'
    if DELIVERY_MARKERS <= h:
        return 'DELIVERED'
    if RETURNS_MARKERS <= h:
        return 'TRANSIT'
    return None


# Source column names for one output column, first to last - the first non-blank one
# wins. The two report types name things differently, and two pairs of columns have
# been merged into one:
#   return_created_date <- RTO rows have no return_created_date; they carry the RTO
#       creation date instead (order_rto_date in the transit report, rto_cancel_date in
#       the delivery report). The two are never populated together - verified across
#       84,901 rows - so one column is safe. `type` tells you which one it is.
#   forward_tracking_number <- the delivery report calls this forward_tracking_number_1.
#       On the 34,580 overlapping rows both values were identical.
SRC = {
    'return_created_date': ['return_created_date', 'order_rto_date', 'rto_cancel_date'],
    'forward_tracking_number': ['forward_tracking_number', 'forward_tracking_number_1'],
    # SPF PAID was called PAYMENT AMOUNT until 2026-09-08
    'SPF PAID': ['SPF PAID', 'PAYMENT AMOUNT'],
}


# The four fields read out of the ticket report, whichever layout the file uses -
# first column present and non-blank wins, so one file of each layout can sit in the
# same account folder.
TICKET_SRC = {
    'orderLineId': ['orderLineId', 'order_line_id'],
    'ticketId': ['ticketId', 'ticket_id'],
    'status': ['status', 'statusLabel'],
    # the named rejection columns of either layout
    'reason': ['rejectionReason', 'rejectReason', 'rejectReasonDescription'],
    # The newer layout leaves all three of those empty and puts the text in
    # validationReason instead - empty on all 1,098 rejected tickets of the current
    # export, which carry their reason there. But that column also holds an
    # eligibility note on accepted tickets ("Item not yet delivered to Seller"), and
    # REJECTION REASON is only ever about a rejection, so it is read for rejects only.
    'reject_reason': ['validationReason'],
    'createdOn': ['createdOn', 'created_on'],
}


# ----------------------------------------------------------------------- value cleanup
# A formula linking another workbook, e.g. =VLOOKUP(G5,[1]Orders!$E:$F,2,) pasted into a
# yellow column. The master is read for its formula text, so such a cell comes back as
# that text, and written back it is a formula pointing at a workbook the file does not
# link to - Excel then "repairs" the file on every open. Its value cannot be worked out
# without that workbook, so it counts as empty.
EXTERNAL_FORMULA = re.compile(r"^=.*\[\d+\]")


def clean(v):
    """Excel error litter, empty strings, the 1970 placeholder date and formulas linking
    another workbook -> None."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:          # NaN
        return None
    if isinstance(v, str):
        s = v.strip()
        if s.upper() in JUNK or s in DUMMY_DATES or EXTERNAL_FORMULA.match(s):
            return None
        return s
    return v


def pick(row, names):
    """The first non-blank value among `names`, or None. Lets one reader serve two
    report layouts that name the same field differently."""
    for n in names:
        if n in row:
            v = clean(row[n])
            if v is not None:
                return v
    return None


DATE_FMTS = ('%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d')
DTIME_FMTS = ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%fZ',
              '%Y-%m-%dT%H:%M:%SZ', '%m-%d-%Y %H:%M:%S', '%d-%m-%Y %H:%M:%S')


def to_money(v):
    """A report's money cell as a rounded float, or None when it is not a number."""
    v = clean(v)
    try:
        return round(float(v), 2) if v is not None else None
    except (TypeError, ValueError):
        return None


def to_dt(v, fmts):
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    s = str(v).strip()
    if not s:
        return None
    for f in fmts:
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            pass
    # also accept a timestamp like "2026-08-09 14:11:39.000" in a date-only column
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def typed(col, v):
    v = clean(v)
    if v is None:
        return None
    if col in DATE_COLS:
        d = to_dt(v, DATE_FMTS + DTIME_FMTS)
        return None if d is None else dt.datetime(d.year, d.month, d.day)
    if col in DTIME_COLS:
        return to_dt(v, DTIME_FMTS + DATE_FMTS)
    if col in NUM_COLS:
        s = str(v)
        if re.fullmatch(r'-?\d+', s):
            n = int(s)
            return n if abs(n) < 2 ** 53 else s
        return s
    return str(v)


# ------------------------------------------------------------------- reading engine
# Every input file is opened once per run and its parsed result is cached on disk,
# keyed on the file's size and mtime. Before this, six separate passes over input/
# each re-opened all 104 files only to ask "what kind of report is this?" - 10 seconds
# a pass, ~70 seconds a run, for an answer that cannot change while the file does not.
CACHE_VERSION = 2              # bump to throw every cached parse away
# The cache lives outside the project folder on purpose: the project sits in Google
# Drive, and tens of MB of pickles rewritten on every run is not worth syncing.
CACHE_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir(),
                         'myntra-spf-cache')
CACHE_STATS = {'hit': 0, 'miss': 0}
USE_CACHE = True               # --no-cache turns it off for one run
TIMINGS = []


class phase(object):
    """Times one step of the run. Printed as a table at the end, so a run that feels
    slow says which step it was instead of leaving you to guess."""

    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.started = time.time()
        return self

    def __exit__(self, *exc):
        TIMINGS.append((self.label, time.time() - self.started))


def _sig(path):
    """What makes a cached parse of this file still valid."""
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def cached(tag, path, build):
    """`build(path)` once per version of that file, then read back from disk.

    Re-running after dropping in one new report re-parses that one file and takes the
    rest from the cache, which is the difference between a six minute run and a five
    second one. A missing or corrupt cache file is simply a miss - the parse is always
    the source of truth, never the cache.
    """
    if not USE_CACHE:
        CACHE_STATS['miss'] += 1
        return build(path)
    key = (CACHE_VERSION, tag, _sig(path))
    name = '%s-%s.pkl' % (tag, hashlib.sha1(
        os.path.abspath(path).lower().encode('utf-8', 'replace')).hexdigest()[:16])
    cp = os.path.join(CACHE_DIR, name)
    try:
        with open(cp, 'rb') as fh:
            blob = pickle.load(fh)
        if blob.get('key') == key:
            CACHE_STATS['hit'] += 1
            return blob['data']
    except Exception:
        pass
    CACHE_STATS['miss'] += 1
    data = build(path)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = cp + '.tmp'
        with open(tmp, 'wb') as fh:
            pickle.dump({'key': key, 'data': data}, fh, pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, cp)
    except Exception:
        pass                   # an unwritable cache slows the next run, nothing more
    return data


def _cell(v):
    """One cell, normalised so both engines hand back the same Python value.

    calamine differs from openpyxl in three ways, all of which matter here: an empty
    cell comes back as '' instead of None; a whole number comes back as a float
    (11346385173.0), which would leave every id written as "11346385173.0"; and a
    date-only cell comes back as a date rather than a datetime.
    """
    if v is None:
        return None
    if isinstance(v, str):
        return v or None
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        if v != v:                                  # NaN
            return None
        return int(v) if v.is_integer() and abs(v) < 2 ** 53 else v
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    return v


def _hdr_of(row):
    """A header row as strings, with trailing empty columns cut off - calamine reports
    the sheet's full declared width where openpyxl trims it."""
    hdr = [str(h).strip() if h is not None and h != '' else '' for h in row]
    while hdr and not hdr[-1]:
        hdr.pop()
    return hdr


def _iter_sheet(path, sheet=None):
    """Rows of one sheet as lists of normalised values, on whichever engine is
    available. `sheet` is a sheet name; None means the first sheet."""
    if python_calamine is not None:
        wb = python_calamine.CalamineWorkbook.from_path(path)
        sh = (wb.get_sheet_by_name(sheet) if sheet is not None
              else wb.get_sheet_by_index(0))
        for row in sh.iter_rows():
            yield [_cell(v) for v in row]
        return
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet is not None else wb.worksheets[0]
        for row in ws.iter_rows(values_only=True):
            yield [_cell(v) for v in row]
    finally:
        wb.close()


_SHEET_RE = re.compile(r'<sheet[^>]*?name="([^"]*)"')


def _sheet_names(path):
    """The sheet names, straight out of the workbook's own index in the zip. Opening
    the file with either engine to ask this costs seconds on a large export; reading
    one small XML part out of the zip costs nothing."""
    try:
        with zipfile.ZipFile(path) as z:
            names = _SHEET_RE.findall(z.read('xl/workbook.xml').decode('utf-8',
                                                                      'replace'))
        if names:
            return [n.replace('&amp;', '&').replace('&lt;', '<')
                     .replace('&gt;', '>').replace('&quot;', '"')
                     .replace('&apos;', "'") for n in names]
    except Exception:
        pass
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()




# ------------------------------------------------------------------- classification
def _find_report(path):
    """(sheet name, header row number, headers, kind) for one input file.

    Normally row 1 of the first sheet. Two files are not: the newer ticket export
    ships 'Summary' and 'Comments' sheets beside its data, and a copy of this script's
    own master keeps its headers on row 2 under the subtotal band. So the first two
    rows of every sheet are tried and the first recognised report wins, with row 1 of
    the first sheet as the fallback.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == '.csv':
        with open(path, 'r', encoding='utf-8-sig', newline='') as fh:
            hdr = _hdr_of(next(csv.reader(fh), []))
        return None, 1, hdr, detect_kind(hdr)
    if ext not in ('.xlsx', '.xlsm'):
        return None, 1, [], None
    # openpyxl on purpose, even when calamine is installed: calamine parses the whole
    # sheet before it hands back a single row (12 seconds on the 83 MB payment export),
    # while openpyxl reads row by row and stops after two. One open covers every sheet.
    fallback = None
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            for n, row in enumerate(ws.iter_rows(max_row=2, values_only=True), start=1):
                hdr = _hdr_of([_cell(v) for v in row])
                kind = detect_kind(hdr)
                if kind:
                    return ws.title, n, hdr, kind
                if fallback is None:
                    fallback = (ws.title, n, hdr, None)
    finally:
        wb.close()
    return fallback or (None, 1, [], None)


_REPORTS = {}          # abspath -> (sig, sheet, header row, headers, kind)


def report_of(path):
    """How to read one input file, answered once per run and remembered between
    runs. Returns (sheet, header row, headers, kind)."""
    key = os.path.abspath(path)
    sig = _sig(path)
    hit = _REPORTS.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    found = tuple(cached('kind', path, _find_report))
    _REPORTS[key] = (sig, found)
    return found


def kind_of(path):
    """Which report this file is, or None."""
    return report_of(path)[3]


def read_headers(path):
    """The header row of the recognised report in this file."""
    return report_of(path)[2]


# ----------------------------------------------------------------------- row readers
def iter_report(path, cols=None):
    """Stream the recognised report of one file as dicts.

    `cols` keeps only those columns, which is most of the win on the wide exports: the
    ticket report has 77 columns and five of them are read, the payment report 60 and
    eight. Building the other 64 into a dict per row costs more than the parsing does.
    """
    sheet, hrow, hdr, _ = report_of(path)
    if not hdr:
        return
    take = [(h, i) for i, h in enumerate(hdr) if h and (cols is None or h in cols)]
    if os.path.splitext(path)[1].lower() == '.csv':
        with open(path, 'r', encoding='utf-8-sig', newline='') as fh:
            rd = csv.reader(fh)
            for _ in range(hrow):
                next(rd, None)
            for row in rd:
                n = len(row)
                yield {h: (row[i] or None) if i < n else None for h, i in take}
        return
    it = _iter_sheet(path, sheet)
    for _ in range(hrow):
        next(it, None)
    for row in it:
        n = len(row)
        rec = {h: row[i] if i < n else None for h, i in take}
        if any(v is not None for v in rec.values()):
            yield rec


def read_rows(path):
    """(headers, every row as a dict). Only for the readers that want the lot."""
    return read_headers(path), list(iter_report(path))


def walk_reports(root):
    """Every report file under root, oldest to newest."""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath).lower() == 'archive':
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.startswith('~$'):
                continue
            if os.path.splitext(fn)[1].lower() in ('.csv', '.xlsx', '.xlsm'):
                out.append(os.path.join(dirpath, fn))
    out.sort(key=lambda p: (os.path.getmtime(p), p))
    return out


def files_of(kind, root=None):
    """Every file under root (input/ by default) that is a report of this kind, oldest
    first. One classification pass answers this for all of them."""
    return [p for p in walk_reports(root or INPUT_DIR) if kind_of(p) == kind]


def input_files(account):
    """Files inside one account's folder."""
    return walk_reports(os.path.join(INPUT_DIR, account))


# ------------------------------------------------------------------ input organiser
# Every run starts by tidying input/. A report in the wrong folder is moved to the one
# its type belongs in, and a portal export is renamed to the period it covers:
#   DBCC/Return In Transit/2026-08-01 to 2026-08-28.csv
# The type comes from the file's headers and the account from the seller id in the
# portal's file name, so neither depends on where the file was dropped.
SELLER_IDS = {'19170': 'MAIN', '32207': 'DBCC', '35426': 'FMCC'}
SELLER_RE = re.compile(r'_Report_(\d+)_\d{4}-\d{2}-\d{2}_')
# The portal names every export after the period it covers, and the organiser keeps it:
#   ..._Seller_Returns_Report_19170_2026-08-01_2026-08-26.csv
#   2026-08-01 to 2026-08-26 (2).csv
PERIOD_RE = re.compile(r'(\d{4}-\d{2}-\d{2})(?:_| to )(\d{4}-\d{2}-\d{2})'
                       r'(?:\s*\(\d+\))?\.\w+$')
# Where each report type lives inside its account folder ('' = the account folder).
ACCOUNT_FOLDER = {'TRANSIT': 'Return In Transit', 'DELIVERED': 'Return Delivered',
                  'ORDER': 'Order Report', 'TICKET': '', 'PAYMENT': ''}
# ERP exports serve all three accounts and sit in input/ itself. Your own sheets
# (MANUAL, LEGACY) are never moved or renamed.
ROOT_KINDS = ('GRN', 'DISPATCH', 'POD')
RENAMED_KINDS = ('TRANSIT', 'DELIVERED', 'ORDER')


def _home(path, kind):
    """The folder this report belongs in, or None when that cannot be told."""
    if kind in ROOT_KINDS:
        return INPUT_DIR
    if kind not in ACCOUNT_FOLDER:
        return None
    m = SELLER_RE.search(os.path.basename(path))
    account = SELLER_IDS.get(m.group(1)) if m else None
    top = os.path.relpath(path, INPUT_DIR).split(os.sep)
    if account is None and len(top) > 1 and top[0] in SHEETS:
        account = top[0]          # already renamed - the folder is the only clue
    if account is None:
        return None
    return os.path.join(INPUT_DIR, account, ACCOUNT_FOLDER[kind])


def _proper_name(path, kind):
    name = os.path.basename(path)
    m = PERIOD_RE.search(name)
    if kind not in RENAMED_KINDS or not m:
        return name
    return '%s to %s%s' % (m.group(1), m.group(2), os.path.splitext(name)[1].lower())


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def organize_inputs(log, dry_run=False):
    """Move every report to its folder and give the portal exports their period as
    the name. Two downloads of the same period keep both, the second as "(2)"; an
    exact copy of a file already in place is deleted. Moving
    keeps the file's modified time, so which copy counts as the newer one - what
    decides every merge - does not change."""
    taken = {}            # planned destination -> source, so a dry run plans right
    moved = dupes = 0
    for path in walk_reports(INPUT_DIR):
        kind = kind_of(path)
        home = _home(path, kind)
        rel = os.path.relpath(path, INPUT_DIR)
        if home is None:
            if kind in ACCOUNT_FOLDER:
                log('  %-58s account not known - left where it is' % rel[:58])
            continue
        name = _proper_name(path, kind)
        stem, ext = os.path.splitext(name)
        dest, n, dup = os.path.join(home, name), 2, False
        while True:
            if _same(dest, path):
                dest = None           # already where and what it should be
                break
            # a dry run moves nothing, so a name it has already handed out is
            # compared against the file planned to get it
            other = taken.get(os.path.normcase(dest)) if dry_run else None
            if other is None and os.path.exists(dest):
                other = dest
            if other is None:
                break
            if filecmp.cmp(other, path, shallow=False):
                dup = True
                break
            dest, n = os.path.join(home, '%s (%d)%s' % (stem, n, ext)), n + 1
        if dest is None:
            continue
        if dup:
            dupes += 1
            log('  %-58s exact duplicate of %s - deleted'
                % (rel[:58], os.path.relpath(dest, INPUT_DIR)))
            if not dry_run:
                os.remove(path)
            continue
        moved += 1
        if dry_run:
            taken[os.path.normcase(dest)] = path
        log('  %-58s -> %s' % (rel[:58], os.path.relpath(dest, INPUT_DIR)))
        if not dry_run:
            found = report_of(path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.rename(path, dest)
            # the header read is not repeated just because the name changed
            _REPORTS[os.path.abspath(dest)] = (_sig(dest), found)
    if moved or dupes:
        log('  %d file(s) %s, %d exact duplicate(s) %s\n'
            % (moved, 'to move / rename' if dry_run else 'moved / renamed', dupes,
               'to delete' if dry_run else 'deleted'))
    else:
        log('  every file is already in its folder under its period name\n')


# Columns read out of the manual sheet. IDs and AWBs are kept in separate sets so a
# number of one kind can never accidentally match a column of the other kind.
MANUAL_ID_COLS = ['order_line_id', 'return_id', 'order_id']
MANUAL_AWB_COLS = ['forward_tracking_number', 'return_tracking_number']
MASTER_ID_COLS = ['order_line_id', 'return_id']
MASTER_AWB_COLS = ['forward_tracking_number', 'forward_tracking_number_2',
                   'return_tracking_number']
# The GRN column holding the AWB, and what a hit is written as
GRN_AWB_SRC = 'Order Tracking Id'
GRN_ITEM_SRC = 'Order Item Id'
# 1335846-7248313-6189603-1 -> store order id 133584672483136189603, item number ignored
GRN_ITEM_RE = re.compile(r'^(\d{7})-(\d{7})-(\d{7})-\d+$')
GRN_STATUS = 'Return Received ERP'
# Bot only: (order_line_id, return_id) -> OMSGuru status, filled by bindal/myntra_spf/pipeline.py
OMS_RESULTS = {}
DISPATCH_ID_SRC = 'Order ID'
DISPATCH_STATUS = 'Dispatched'
# master columns a dispatch Order ID may carry - tracking first, it is what matches
DISPATCH_MATCH_COLS = MASTER_AWB_COLS + ['STORE ORDER ID', 'order_line_id']



def _parse_manual(path):
    """One manual return sheet -> the ids and AWBs it marks as physically received."""
    cols = set(['RECD'] + MANUAL_ID_COLS + MANUAL_AWB_COLS)
    ids, awbs = set(), set()
    marked = 0
    for r in iter_report(path, cols):
        v = clean(r.get('RECD'))
        # blank, or only a stray date, does not count as received
        if v is None or isinstance(v, (dt.datetime, dt.date)):
            continue
        marked += 1
        for c in MANUAL_ID_COLS:
            n = norm(r.get(c))
            if n:
                ids.add(n)
        for c in MANUAL_AWB_COLS:
            n = norm(r.get(c))
            if n:
                awbs.add(n)
    return ids, awbs, marked


def read_manual_marks(log):
    """Read the manual return sheet(s) from anywhere under input/.

    A row with anything written in its RECD column means that parcel physically reached
    you. As soon as its order id / return id / AWB matches a master row, that row's
    MANUAL RETURN STATUS becomes "Return Received".
    """
    ids, awbs = set(), set()
    files = 0
    for path in files_of('MANUAL'):
        f_ids, f_awbs, marked = cached('manual', path, _parse_manual)
        ids |= f_ids
        awbs |= f_awbs
        files += 1
        log('  %-58s MANUAL   %6d RECD rows'
            % (os.path.relpath(path, INPUT_DIR)[:58], marked))
    if files:
        log('  match keys from manual sheet: %d id, %d awb\n' % (len(ids), len(awbs)))
    return ids, awbs


# --------------------------------------------------------------------- payment report
# What an SPF payout looks like in the payment report:
#   Order_Type   = NOD           (Non Order Deduction/disbursement - claims, penalties,
#                                 adjustments; the normal Forward / Reverse lines are
#                                 order settlements and have nothing to do with SPF)
#   NOD_Comment  carries an SPF marker: "spf_rbnr" for the claims raised from the SPF
#                ticket portal, "ForwardAutoSPF" for the ones Myntra pays automatically.
#                Other NOD lines (M27KAIN..., SplitNOD...) are unrelated adjustments,
#                mostly negative, and are left alone.
# The amount is Settled_Amount. paymentAmount is the total of the whole NEFT batch -
# the same 104,060.97 sits on every line of that UTR - so it is not per claim.
#
# The return charge comes off a different set of lines: Order_Type = Reverse, which is
# the return / exchange leg of an order. `Logistics_Commission` there is the whole
# return logistics fee including 18% GST - verified, it comes to exactly 1.18 x the fee
# base (Shipping_Fee + pick_and_pack_fee + fixed_fee + Payment_Gateway_Fee, or
# total_logistics_deduction where Myntra reports the base as one figure). It is only a
# fee: the customer refund itself sits in Settled_Amount and is not a charge.
# 41,824 of the 76,548 Reverse lines carry no fee at all - those returns were free.
PAY_ORDER_TYPE = 'NOD'
PAY_MARKER = 'SPF'
PAY_REVERSE = 'REVERSE'
# The sale leg. Settled_Amount there is what Myntra actually paid out for the order,
# after commission and logistics. 26,147 order lines carry two or more Forward lines
# (Myntra splits a settlement across payouts) and not one of the 217,300 is negative,
# so the lines for an order line are summed.
PAY_FORWARD = 'FORWARD'
PAY_CHARGE_COL = 'Logistics_Commission'
PAY_COLS = ['Order_Type', 'NOD_Comment', 'order_line_id', 'seller_order_id',
            'return_id', 'Settled_Amount', 'utrNumber', 'Logistics_Commission']



def _pay_rows(path):
    """Stream the payment report - only the columns above, one row at a time. It runs
    to ~300k rows, so building a dict of all 60 columns per row costs more than the
    parsing does."""
    return iter_report(path, set(PAY_COLS))



def _parse_payment(path):
    """One payment export, totalled on its own. Never added to another file's totals:
    a full dump and a shorter one usually sit in input/ together and overlap, and
    adding them would multiply every charge by the number of copies lying around."""
    f_pay, f_soi, f_rid, f_oli, f_rev, f_set = {}, {}, {}, {}, {}, {}
    f_drid, f_doli = {}, {}
    nod = hits = charged = unmarked = 0
    for r in _pay_rows(path):
        kind = norm(r.get('Order_Type'))
        oli = norm(r.get('order_line_id'))

        if kind == PAY_FORWARD:
            amt = to_money(r.get('Settled_Amount'))
            if oli and amt is not None:
                f_set[oli] = round(f_set.get(oli, 0) + amt, 2)
            continue

        if kind == PAY_REVERSE:
            rid = norm(r.get('return_id'))
            f_rev[oli] = f_rev.get(oli, 0) + 1
            deb = to_money(r.get('Settled_Amount'))
            if deb is not None:
                if rid:
                    f_drid[rid] = round(f_drid.get(rid, 0) + deb, 2)
                if oli:
                    f_doli[oli] = round(f_doli.get(oli, 0) + deb, 2)
            fee = to_money(r.get(PAY_CHARGE_COL)) or 0
            if fee:
                charged += 1
                if rid:
                    f_rid[rid] = round(f_rid.get(rid, 0) + fee, 2)
                if oli:
                    f_oli[oli] = round(f_oli.get(oli, 0) + fee, 2)
            continue

        if kind != PAY_ORDER_TYPE:
            continue
        nod += 1
        if not oli:
            continue          # a NOD line with no order line cannot be placed
        if PAY_MARKER not in norm(r.get('NOD_Comment')):
            # an identified NOD line that is not flagged SPF: not ours to claim.
            # Logged because a label change on Myntra's side would show up here.
            unmarked += 1
            continue
        amt = to_money(r.get('Settled_Amount'))
        utr = clean(r.get('utrNumber'))
        prev = f_pay.get(oli)
        if prev:
            amt = round((prev[0] or 0) + (amt or 0), 2)
            utr = ' / '.join(
                dict.fromkeys([u for u in [prev[1], utr] if u])) or None
        f_pay[oli] = (amt, utr)
        soi = norm(r.get('seller_order_id'))
        if soi:
            f_soi[soi] = f_pay[oli]
        hits += 1

    return (f_pay, f_soi, f_rid, f_oli, f_rev, f_set, f_drid, f_doli,
            nod, hits, charged, unmarked)


def read_payments(log):
    """SPF payouts, return charges and order settlements from the payment report(s).

    Returns five lookups:
      pay_oli / pay_soi   (amount, utr) of an SPF payout, by order_line_id and by
                          seller_order_id. The second is only a fallback for a row whose
                          order_line_id does not match; both ids come off the same payout
                          line, so they can never disagree.
      chg_rid / chg_oli   return charge, by return_id and by order_line_id.
      deb_rid / deb_oli   the whole return reversal (negative), same two keys. The
                          charge is one part of this, not something extra on top.
      settled             order settlement, by order_line_id.

    Several payment exports usually sit in input/ at once - a full dump, a shorter one,
    the same file saved as both .csv and .xlsx - and they overlap heavily. So each file
    is totalled on its own and the files are then merged newest last: a key the newest
    export has takes its value, a key only an older export has is kept. Amounts are
    never added across files, which would silently multiply a charge by the number of
    copies of the report lying in the folder.

    return_id is the safe key for the charge - it is what the master is keyed on - but
    Myntra leaves it blank on about 9% of the Reverse lines. Those fall back to
    order_line_id, and to keep that fallback honest it is built only for order lines
    with a single Reverse line: where an order line has two returns there is no way to
    tell which one the charge belongs to, so it is left blank rather than guessed.
    """
    by_oli, by_soi, chg_rid, chg_oli, rev_lines, settled = {}, {}, {}, {}, {}, {}
    deb_rid, deb_oli = {}, {}
    unmarked = 0
    for path in files_of('PAYMENT'):
        (f_pay, f_soi, f_rid, f_oli, f_rev, f_set, f_drid, f_doli,
         nod, hits, charged, f_unmarked) = cached('payment', path, _parse_payment)
        unmarked += f_unmarked
        for whole, part in ((by_oli, f_pay), (by_soi, f_soi), (chg_rid, f_rid),
                            (chg_oli, f_oli), (rev_lines, f_rev), (settled, f_set),
                            (deb_rid, f_drid), (deb_oli, f_doli)):
            whole.update(part)
        log('  %-58s PAYMENT  %6d SPF payouts (of %d NOD lines), %d charged returns,'
            ' %d settled orders'
            % (os.path.relpath(path, INPUT_DIR)[:58], hits, nod, charged, len(f_set)))
    if unmarked:
        log('  %d NOD lines had an order line but no SPF marker - not counted as a '
            'claim payout' % unmarked)
    # drop the ambiguous order lines from the fallback (see the docstring)
    chg_oli = {k: v for k, v in chg_oli.items() if rev_lines.get(k) == 1}
    deb_oli = {k: v for k, v in deb_oli.items() if rev_lines.get(k) == 1}
    return by_oli, by_soi, chg_rid, chg_oli, settled, deb_rid, deb_oli



# ----------------------------------------------------------------------- order report
# The Myntra Seller Orders Report - input/<ACCOUNT>/Order Report/, one CSV per month per
# account, 46 columns. It is the only report that carries what an order was worth; the
# returns reports have no money column at all. Its headers are spaced rather than
# underscored ("order line id"), which is what tells it apart from every other input.
#
# ORDER AMOUNT = `final amount`: what the customer paid for the line, i.e. `total mrp`
# minus `discount` (1499 - 942 = 557). The alternatives were rejected: `total mrp` is
# the list price nobody paid, and `seller price` is the smaller ex-charges figure the
# payment report calls customer_paid_amt - that one was used until 2026-09-09 and was
# the wrong number.
ORDER_AMOUNT_SRC = 'final amount'
ORDER_KEY_SRC = 'order line id'
# Myntra writes this one with a leading apostrophe so Excel keeps it as text - it is 21
# digits, far past what a float can hold, so it stays a string here too.
ORDER_STORE_ID_SRC = 'store order id'


def _parse_order(path):
    """One order report -> order value and store order id by order line."""
    amounts, store_ids = {}, {}
    n = 0
    for r in iter_report(path, {ORDER_KEY_SRC, ORDER_AMOUNT_SRC, ORDER_STORE_ID_SRC}):
        k = norm(r.get(ORDER_KEY_SRC))
        if not k:
            continue
        v = clean(r.get(ORDER_AMOUNT_SRC))
        try:
            v = round(float(v), 2) if v is not None else None
        except (TypeError, ValueError):
            v = None
        if v is not None:
            amounts[k] = v
            n += 1
        sid = clean(r.get(ORDER_STORE_ID_SRC))
        if sid is not None:
            store_ids[k] = str(sid).lstrip("'").strip()
    return amounts, store_ids, n


def read_orders(log):
    """Order value by order_line_id, from every order report under input/."""
    amounts, store_ids = {}, {}
    files = 0
    for path in files_of('ORDER'):
        f_amt, f_sid, n = cached('order', path, _parse_order)
        amounts.update(f_amt)
        store_ids.update(f_sid)
        files += 1
        log('  %-58s ORDER    %6d order lines'
            % (os.path.relpath(path, INPUT_DIR)[:58], n))
    if files:
        log('  %d order report files, %d order lines with a value' % (files, len(amounts)))
    return amounts, store_ids



def _parse_grn(path):
    """One GRN export -> the tracking ids and store order ids it booked in."""
    seen, store_ids = set(), set()
    n = 0
    for r in iter_report(path, {GRN_AWB_SRC, GRN_ITEM_SRC}):
        v = norm(clean(r.get(GRN_AWB_SRC)))
        if v and v != '-':
            seen.add(v)
            n += 1
        m = GRN_ITEM_RE.match(str(clean(r.get(GRN_ITEM_SRC)) or '').strip())
        if m:
            store_ids.add(''.join(m.groups()))
    return seen, store_ids, n


def read_grn(log):
    """Tracking ids the ERP has booked in, from the GR/GRN report(s) under input/.

    A GRN row is the warehouse receiving the parcel, so it is physical proof the return
    came back. Two keys come out of it:
      - `Order Tracking Id`, matched against the master's AWB columns
      - `Order Item Id`, which for Myntra reads like 1335846-7248313-6189603-1: the
        21-digit store order id split into three groups of seven, plus a trailing item
        number. That item number is the ERP's own counter, not Myntra's, so it is dropped
        and the whole order counts as received - every item of it.
    Other portals' ids (Flipkart BIN-OD..., etc.) do not fit the pattern and are ignored.

    Returns (tracking ids, store order ids).
    """
    seen, store_ids = set(), set()
    for path in files_of('GRN'):
        f_awb, f_sid, n = cached('grn', path, _parse_grn)
        seen |= f_awb
        store_ids |= f_sid
        log('  %-58s GRN      %6d tracking ids, %d store order ids'
            % (os.path.relpath(path, INPUT_DIR)[:58], n, len(f_sid)))
    return seen, store_ids



def _parse_dispatch(path):
    """One dispatch export -> {id: (voucher date, voucher no.)}, latest voucher kept."""
    seen = {}
    n = 0
    for r in iter_report(path, {DISPATCH_ID_SRC, 'Voucher Date', 'Voucher No.'}):
        oid = norm(clean(r.get(DISPATCH_ID_SRC)))
        if not oid or oid == '-':
            continue
        when = clean(r.get('Voucher Date'))
        if not isinstance(when, dt.datetime):
            when = None
        prev = seen.get(oid)
        if prev is None or (when or dt.datetime.min) >= (prev[0] or dt.datetime.min):
            seen[oid] = (when, clean(r.get('Voucher No.')))
        n += 1
    return seen, n


def read_dispatch(log):
    """Parcels the ERP dispatched, keyed by the id in the export's Order ID column.

    For Myntra rows that Order ID is the shipment AWB (MYSP... / MYSC... / MYEC...),
    not an order number, so against the master it lands on forward_tracking_number.
    It is also tried against STORE ORDER ID and order_line_id in case an export fills
    it that way. A parcel dispatched more than once keeps its latest voucher.

    Returns {normalised id: (voucher date, voucher no.)}.
    """
    seen = {}
    for path in files_of('DISPATCH'):
        f_seen, n = cached('dispatch', path, _parse_dispatch)
        for oid, val in f_seen.items():
            prev = seen.get(oid)
            if prev is None or (val[0] or dt.datetime.min) >= (prev[0] or dt.datetime.min):
                seen[oid] = val
        log('  %-58s DISPATCH %6d dispatched ids'
            % (os.path.relpath(path, INPUT_DIR)[:58], n))
    return seen



POD_ID_SRC = 'Tracking ID'
POD_TYPE_SRC = 'TYPE'
POD_DATE_SRC = 'DATE'
POD_YES, POD_NO = 'YES', 'NO'


def pod_merge(types, typ, when):
    """Record one POD line, keeping the earliest date seen for that type."""
    prev = types.get(typ)
    if typ not in types or (when and (prev is None or when < prev)):
        types[typ] = when


def _parse_pod(path):
    """One POD workbook -> {tracking id: {POD type: earliest date or None}}. Every sheet
    is read - the file keeps RETURN POD and PICKUP POD on separate sheets with the same
    headers."""
    seen = {}
    n = 0
    for sheet in _sheet_names(path):
        it = _iter_sheet(path, sheet)
        hdr = _hdr_of(next(it, []))
        if not POD_MARKERS <= set(hdr):
            continue
        i_id, i_type = hdr.index(POD_ID_SRC), hdr.index(POD_TYPE_SRC)
        i_date = hdr.index(POD_DATE_SRC) if POD_DATE_SRC in hdr else None
        for row in it:
            tid = norm(clean(row[i_id])) if i_id < len(row) else None
            if not tid or tid == '-':
                continue
            typ = clean(row[i_type]) if i_type < len(row) else None
            when = (clean(row[i_date]) if i_date is not None and i_date < len(row)
                    else None)
            if not isinstance(when, dt.datetime):
                when = None
            pod_merge(seen.setdefault(tid, {}), str(typ or sheet).strip().upper(), when)
            n += 1
    return seen, n


def read_pod(log):
    """Tracking ids in the courier POD sheet(s), with the POD type(s) each one is in.

    The forward AWB turns up under PICKUP POD (parcel handed to the courier) and, for an
    RTO, again under RETURN POD; a return AWB only ever under RETURN POD.

    Returns ({normalised tracking id: {type: earliest date}}, whether any POD file was
    found).
    """
    seen = {}
    files = 0
    for path in files_of('POD'):
        f_seen, n = cached('pod-dates', path, _parse_pod)
        for tid, types in f_seen.items():
            mine = seen.setdefault(tid, {})
            for typ, when in types.items():
                pod_merge(mine, typ, when)
        files += 1
        log('  %-58s POD      %6d tracking ids'
            % (os.path.relpath(path, INPUT_DIR)[:58], n))
    return seen, bool(files)


def pod_types(rec, pod):
    """{POD type: earliest date} over every AWB of this row."""
    types = {}
    for c in MASTER_AWB_COLS:
        n = norm(rec.get(c)) if rec.get(c) else None
        if n:
            for typ, when in pod.get(n, {}).items():
                pod_merge(types, typ, when)
    return types


def read_all_sheets(path):
    """Every row of every sheet in a workbook, as dicts. A legacy master keeps one
    sheet per account, so reading only the first would lose two thirds of it."""
    out = []
    for sheet in _sheet_names(path):
        # The header is row 1, or row 2 under a subtotal band. A sheet with no
        # order_line_id in either (SUMMARY, DATE SUMMARY) is not a data sheet.
        it = _iter_sheet(path, sheet)
        hdr = None
        for _ in range(2):
            probe = next(it, None)
            if probe is None:
                break
            cand = _hdr_of(probe)
            if 'order_line_id' in cand:
                hdr = cand
                break
        if hdr is None:
            continue
        out.extend(dict(zip(hdr, r)) for r in it)
    return out



def _parse_legacy(path):
    """One older master -> the OMSGURU / MANUAL cells it has filled in, by row key."""
    notes = {}
    for r in read_all_sheets(path):
        k = (norm(r.get('order_line_id')), norm(r.get('return_id')))
        if not k[0]:
            continue
        keep = {c: clean(r.get(c)) for c in MANUAL_COLS}
        keep = {c: v for c, v in keep.items() if v is not None}
        if keep:
            notes.setdefault(k, {}).update(keep)
    return notes


def read_legacy_notes(log):
    """Harvest OMSGURU STATUS / MANUAL RETURN STATUS from an older master workbook.

    Drop any previous MYNTRA SPF MASTER into input/ (a LEGACY MASTER/ folder keeps it
    tidy) and the columns you filled in there are carried into the matching rows of the
    new master. Only empty cells are filled, so nothing in the current workbook is
    touched. Rows are matched on the usual (order_line_id, return_id) key.
    """
    notes = {}
    for path in files_of('LEGACY'):
        found = cached('legacy', path, _parse_legacy)
        for k, v in found.items():
            notes.setdefault(k, {}).update(v)
        log('  %-58s LEGACY   %6d filled rows'
            % (os.path.relpath(path, INPUT_DIR)[:58], len(found)))
    return notes


def manual_received(rec, ids, awbs):
    for c in MASTER_ID_COLS:
        n = norm(rec.get(c))
        if n and n in ids:
            return True
    for c in MASTER_AWB_COLS:
        n = norm(rec.get(c))
        if n and n in awbs:
            return True
    return False


# --------------------------------------------------------------------------- keys
def norm(v):
    if v is None:
        return ''
    s = str(v).strip()
    if s.endswith('.0') and re.fullmatch(r'\d+\.0', s):
        s = s[:-2]
    return s.upper()


def row_key(rec):
    """Identity of one return.

    (order_line_id, return_id). RTO rows have a blank return_id, but an order line only
    ever has one RTO, so the key stays unique anyway - verified across the whole data
    set: 45,410 keys, not a single collision. Both columns are present in the master
    too, which is why re-reading it matches the old rows exactly and a re-run can never
    append a duplicate.
    """
    return (norm(rec.get('order_line_id')), norm(rec.get('return_id')))


def too_old(rec):
    """True for a return created before EARLIEST_RETURN."""
    v = rec.get('return_created_date')
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        v = dt.datetime(v.year, v.month, v.day)
    return isinstance(v, dt.datetime) and v < EARLIEST_RETURN


def is_delivered_status(status):
    return 'DELIVERED' in norm(status)


# --------------------------------------------------------------------------- collect
def _parse_ticket(path):
    """One ticket report -> {order line: (created, ticket id, status, reason)}.

    Both layouts are read through TICKET_SRC, and where a file holds several tickets
    for one order line the newest one wins.
    """
    tickets = {}
    n = 0
    cols = set()
    for names in TICKET_SRC.values():
        cols.update(names)
    for r in iter_report(path, cols):
        oid = norm(pick(r, TICKET_SRC['orderLineId']))
        if not oid:
            continue
        created = to_dt(pick(r, TICKET_SRC['createdOn']) or '',
                        DTIME_FMTS + DATE_FMTS)
        status = pick(r, TICKET_SRC['status'])
        reason = pick(r, TICKET_SRC['reason'])
        if reason is None and 'REJECT' in norm(status):
            reason = pick(r, TICKET_SRC['reject_reason'])
        prev = tickets.get(oid)
        if prev is None or (created or dt.datetime.min) >= prev[0]:
            tickets[oid] = (created or dt.datetime.min,
                            pick(r, TICKET_SRC['ticketId']), status, reason)
        n += 1
    return tickets, n


def merge_tickets(tickets, portal_oids, found, from_portal):
    """One ticket report's {order line: (created, ticket id, status, reason)} into `tickets`:
    the newest ticket per order line wins. Bot only: the portal's own ticket list
    (from_portal) is the current status of a ticket, so a hand export of the same ticket - an
    older snapshot - never overrides it, whichever file is read first; and the portal's list
    carries no rejection reason, so a reason another report gave for the same ticket is kept."""
    for oid, val in found.items():
        prev = tickets.get(oid)
        same = prev is not None and str(prev[1]) == str(val[1])
        if same and oid in portal_oids and not from_portal:
            continue
        if prev is None or same or val[0] >= prev[0]:
            if from_portal:
                portal_oids.add(oid)
            if same and val[3] is None:
                val = val[:3] + (prev[3],)
            tickets[oid] = val


def _parse_returns(path):
    """One returns report -> (records kept, in-transit-only lookup, warehouse models,
    counters).

    Everything that depends on this file alone happens here, because this is what gets
    cached: the column mapping, the type conversion, the too-old cut-off and rule 1
    (a delivered row of an in-transit report is a lookup, not a row). Only the
    cross-file work - deduplication against the other reports - is left to collect().
    """
    kind = kind_of(path)
    recs, only_transit, wh_model = [], {}, {}
    stats = {'too_old': 0, 'dropped_delivered': 0}
    for r in iter_report(path):
        if kind == 'TRANSIT':
            mdl = clean(r.get('model'))
            wh = norm(r.get('warehouse_id'))
            if mdl and wh:
                wh_model.setdefault(wh, set()).add(str(mdl))
            # Rule 1: the in-transit report also lists delivered rows. Do not keep
            # them as rows, only feed their TRANSIT_ONLY columns into the lookup.
            if is_delivered_status(r.get('status')):
                stats['dropped_delivered'] += 1
                look = {}
                for c in TRANSIT_ONLY:
                    for src in SRC.get(c, (c,)):
                        if src in r:
                            v = typed(c, r[src])
                            if v is not None:
                                look[c] = v
                                break
                k = (norm(r.get('order_line_id')), norm(r.get('return_id')))
                if k[0] and any(v is not None for v in look.values()):
                    only_transit.setdefault(k, {}).update(
                        {c: v for c, v in look.items() if v is not None})
                continue

        rec = {}
        for col in RAW:
            for src in SRC.get(col, (col,)):
                if src in r:
                    v = typed(col, r[src])
                    if v is not None:
                        rec[col] = v
                        break
        rec['_kind'] = kind

        if not norm(rec.get('order_line_id')):
            continue
        if too_old(rec):
            stats['too_old'] += 1
            continue
        recs.append(rec)
    return kind, recs, only_transit, wh_model, stats


def collect(account, log):
    """Read one account's inputs, filter them, merge them, drop duplicates."""
    merged = {}          # key -> record dict
    tickets = {}         # order_line_id -> (createdOn, ticketId, status, reason)
    # Delivered rows in the "Return In Transit" report are not kept as rows, but they do
    # carry `model`, which the Return Delivery Report does not have at all. So they are
    # kept as a lookup: no row is added, the column just gets filled.
    only_transit = {}    # key -> {col: value}
    wh_model = {}        # warehouse_id -> set of models seen  (PPMP / SJIT / FBM)
    portal_oids = set()
    stats = {'TRANSIT': 0, 'DELIVERED': 0, 'TICKET': 0, 'too_old': 0,
             'dropped_delivered': 0, 'dupes': 0, 'skipped': []}

    for path in input_files(account):
        rel = os.path.relpath(path, INPUT_DIR)
        kind = kind_of(path)
        if kind in ('MANUAL', 'LEGACY', 'PAYMENT', 'ORDER', 'GRN', 'DISPATCH', 'POD'):
            continue          # read separately, once, for all three sheets
        if kind is None:
            stats['skipped'].append(rel)
            continue

        if kind == 'TICKET':
            found, n = cached('ticket', path, _parse_ticket)
            merge_tickets(tickets, portal_oids, found, os.path.basename(path) in PORTAL_TICKET_FILES)
            stats['TICKET'] += n
            log('  %-58s TICKET   %6d' % (rel[:58], n))
            continue

        _, recs, f_only, f_wh, st = cached('returns', path, _parse_returns)
        for k, v in f_only.items():
            only_transit.setdefault(k, {}).update(v)
        for wh, mdls in f_wh.items():
            wh_model.setdefault(wh, set()).update(mdls)
        stats['too_old'] += st['too_old']
        stats['dropped_delivered'] += st['dropped_delivered']

        kept = 0
        for rec in recs:
            k = row_key(rec)
            if k in merged:
                stats['dupes'] += 1
                merge_into(merged[k], rec)
            else:
                merged[k] = rec
            kept += 1

        stats[kind] += kept
        log('  %-58s %-8s %6d' % (rel[:58], kind, kept))

    return merged, tickets, only_transit, wh_model, stats


def merge_into(old, new):
    """Fold two sources into one row. The delivery report is the newer state, so it
    wins; but a column that only the other report supplies is never blanked out."""
    new_wins = (new.get('_kind') == 'DELIVERED' and old.get('_kind') != 'DELIVERED')
    for col in RAW:
        nv = new.get(col)
        ov = old.get(col)
        if nv is None:
            continue
        if ov is None:
            old[col] = nv
        elif col == 'type':
            # "RTO" is more informative than "RTO_or_Cancel"
            if norm(ov) == 'RTO_OR_CANCEL' and norm(nv) != 'RTO_OR_CANCEL':
                old[col] = nv
        elif new_wins or new.get('_kind') == old.get('_kind'):
            old[col] = nv
    if new.get('_kind') == 'DELIVERED':
        old['_kind'] = 'DELIVERED'


# Only the "Return In Transit" report supplies these columns - they do not exist in the
# Return Delivery Report, so delivered rows have to get them from the lookup.
TRANSIT_ONLY = ['model', 'return_status', 'seller_order_id',
                'order_group_id', 'forward_tracking_number']


# --------------------------------------------------------------------------- formulas
def build_formulas(n):
    """The logic of SPF POLICY.pdf (v5) Annexure 1.

    The age clock starts at deliver_to_seller_date when there is one, otherwise at
    return_created_date. So the two cases are:

    delivery date present - the portal says the parcel came back. If you did not
               actually get it, that is the "return not received" claim, and the window
               is 7 days from the delivery date. AGE already counts from that date, so
               the test is simply AGE <= 7.
    no delivery date (clause 8) - the shipment was never delivered back
               -> a 60 to 120 day window from the return / RTO creation date.

    Either way, if you did receive the parcel (FINAL RETURN STATUS = Return Received)
    there is nothing to claim.
    """
    # Fallback clock start: return creation date for Return rows, RTO creation date for
    # RTO rows. The two never coexist, which is why they were merged into one column.
    rcd = C['return_created_date'] + str(n)
    dlv = C['deliver_to_seller_date'] + str(n)   # the day it came back to the seller
    st = C['status'] + str(n)
    ty = C['type'] + str(n)
    trk = C['return_tracking_number'] + str(n)
    age = C['AGE (DAYS)'] + str(n)
    final = C['FINAL RETURN STATUS'] + str(n)
    tkt = C['TICKET ID'] + str(n)
    oms = C['OMSGURU STATUS'] + str(n)
    man = C['MANUAL RETURN STATUS'] + str(n)

    # AGE = days from the clock start until TODAY. The clock starts at the delivery date
    # when there is one, otherwise at the return / RTO creation date.
    start = 'IF({d}<>"",{d},{r})'.format(d=dlv, r=rcd)
    f_age = '=IF({p}="","",MAX(0,TODAY()-INT({p})))'.format(p=start)

    # yyyy-mm of the return create date, so sorting this column A to Z is chronological
    f_month = '=IF({r}="","",TEXT({r},"yyyy-mm"))'.format(r=rcd)

    # First match wins:
    #   SPF Paid          - Myntra paid the claim (SPF PAID filled)
    #   SPF Accepted      - the SPF ticket passed (STATUS ACCEPT / INVOICE_INITIATED /
    #                       PAYMENT_COMPLETED), the payout not on the payment report yet
    #   Return Received   - OMSGURU / MANUAL say the parcel is back, or MANUAL says
    #                       "Pickup Not Done": the parcel never left the customer, so
    #                       nothing is lost and it must not count as pending
    #   Payment Received  - the sale was settled and nothing was taken back: no refund
    #                       (RETURN DEBIT) and no return charge on the payment report
    spf = C['SPF PAID'] + str(n)
    settle = C['ORDER SETTLEMENT'] + str(n)
    debit = C['RETURN DEBIT'] + str(n)
    fee = C['RETURN CHARGE'] + str(n)
    tks = C['STATUS'] + str(n)
    f_final = ('=IF({s}<>"","SPF Paid",'
               'IF(AND({tid}<>"",OR({tk}="ACCEPT",{tk}="INVOICE_INITIATED",{tk}="PAYMENT_COMPLETED")),"SPF Accepted",'
               'IF(OR(ISNUMBER(SEARCH("RECEIVED",{o})),ISNUMBER(SEARCH("RECEIVED",{m})),'
               'ISNUMBER(SEARCH("PICKUP NOT DONE",{m}))),'
               '"Return Received",'
               'IF(AND(ISNUMBER({st}),{d}="",{fe}=""),"Payment Received",""))))'
               .format(s=spf, tk=tks, tid=tkt, o=oms, m=man, st=settle, d=debit, fe=fee))

    # A delivery date means the portal handed it back, so it is a "return not received"
    # case unless you confirm you actually have it.
    f_type = ('=IF({f}="Return Received","RECEIVED",'
              'IF(OR({d}<>"",{s}="Ret Delivered",{s}="Delivered"),"RETURN NOT RECEIVED",'
              'IF(OR({s}="Ret Created",{s}="Ret Dispatched",{s}="RTO Created",'
              '{s}="Dispatched"),"RETURN IN TRANSIT",'
              'IF(AND({t}="Return",{k}=""),"NO RETURN TRACKING","RETURN IN TRANSIT"))))'
              .format(d=dlv, f=final, s=st, t=ty, k=trk))

    # SJIT and FBM orders are not worked for SPF: never a claim (kept as rows only)
    mdl = C['model'] + str(n)
    not_worked = 'OR(' + ','.join('{m}="{x}"'.format(m=mdl, x=x) for x in NOT_WORKED_MODELS) + ')'
    f_status = ('=IF({k}<>"","CLAIM DONE",'
                'IF({nw},"NON ELIGIBLE",'
                'IF(NOT(ISNUMBER({a})),"NON ELIGIBLE",'
                'IF({f}<>"","NON ELIGIBLE",'
                'IF({d}<>"",IF({a}<=7,"ELIGIBLE","EXPIRED"),'
                'IF({a}<60,"UPCOMING CLAIM",IF({a}<=120,"ELIGIBLE","EXPIRED")))))))'
                .format(k=tkt, d=dlv, a=age, f=final, nw=not_worked))

    # What this order line actually left in the bank: the sale, minus the return
    # reversal, plus any SPF payout. RETURN CHARGE is NOT added here - it is already
    # inside RETURN DEBIT, and counting it again would double it.
    legs = [C['ORDER SETTLEMENT'] + str(n), C['RETURN DEBIT'] + str(n),
            C['SPF PAID'] + str(n)]
    f_net = '=IF(COUNT({0})=0,"",SUM({0}))'.format(','.join(legs))

    return {'RETURN MONTH': f_month, 'AGE (DAYS)': f_age, 'CLAIM STATUS': f_status,
            'CLAIM TYPE': f_type, 'FINAL RETURN STATUS': f_final, 'NET': f_net}


# ------------------------------------------------------------------- previous master
def read_old_master():
    """Read the whole previous master and use it as the base.

    This is what makes a re-run after dropping in new reports safe:
      - old rows are never duplicated (the same key matches)
      - old rows never disappear, even if their source file is gone from input/
      - the OMSGURU / MANUAL columns you filled in stay exactly as they were

    Nothing you typed into a yellow column is ever replaced or removed. The workbook is
    opened with data_only=False on purpose: a formula cell then reads back as its
    formula text ("=IF(...")), so a cell holding anything else is something you typed
    over the formula. Those cells are recorded in rec['_locked'] and written back
    verbatim instead of being regenerated.
    """
    base = {s: {} for s in SHEETS}
    if not os.path.exists(MASTER):
        return base
    # openpyxl, not the fast engine: this is the one file read for its formula TEXT
    # rather than its values, and calamine only ever returns values. read_only keeps it
    # to a row-by-row scan instead of building a cell object for all 1.6M cells.
    wb = openpyxl.load_workbook(MASTER, read_only=True, data_only=False)
    for sheet in SHEETS + [PARKED_SHEET]:
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        # The header is on row 2 now, but a master written before the SUBTOTAL band
        # was added has it on row 1 - so look for it rather than assuming.
        rows = ws.iter_rows(values_only=True)
        hdr = []
        for probe in rows:
            hdr = [str(h).strip() if h is not None else '' for h in probe]
            if 'order_line_id' in hdr:
                break
        idx = {h: i for i, h in enumerate(hdr)}
        if 'order_line_id' not in idx:
            continue
        for r in rows:
            raw = {h: (r[i] if i < len(r) else None) for h, i in idx.items()}
            rec = {}
            # SRC is used here too, so a master saved in an older column layout still
            # upgrades cleanly instead of losing those values.
            for c in (RAW + MANUAL_COLS + TICKET_COLS + PAYMENT_COLS + ORDER_COLS
                      + POD_COLS):
                if c in FORMULA_COLS:
                    continue
                for src in SRC.get(c, (c,)):
                    if src not in idx:
                        continue
                    v = typed(c, raw.get(src)) if c in RAW else clean(raw.get(src))
                    if v is not None:
                        rec[c] = v
                        break
            # a formula column holding something other than our formula = you edited it
            locked = set()
            for c in FORMULA_COLS:
                if c not in idx:
                    continue
                v = raw.get(c)
                if isinstance(v, str) and v.lstrip().startswith('='):
                    continue                      # still our formula, regenerate it
                v = clean(v)
                if v is not None:
                    rec[c] = v
                    locked.add(c)

            # a parked row names its account sheet in its first column
            name = sheet if sheet in base else raw.get(PARKED_ACCOUNT)
            k = row_key(rec)
            if name not in base or not k[0] or too_old(rec):
                continue
            if locked:
                rec['_locked'] = locked
            rec['_kind'] = 'DELIVERED' if rec.get('deliver_to_seller_date') else 'TRANSIT'
            base[name][k] = rec
    wb.close()
    return base


# --------------------------------------------------------------------------- write
# Working columns (AGE through STATUS) are yellow, every other column has no fill.
# Text is black everywhere.
WORK_FILL = '#FFFF00'
BAND_FILL = '#D9E1F2'
THIN = {'bottom': 1, 'bottom_color': '#BFBFBF'}
BOLD = {'bold': True, 'font_color': 'black', 'font_size': 10}


# xlsxwriter rewrites the newer Excel functions (XLOOKUP, FILTER, LAMBDA, ...) into
# their _xlfn. names by running about sixty regex substitutions over every formula
# string handed to it. This workbook writes 290,000 formulas and uses none of those
# functions, so that pass was 24 of the 40 seconds the write took. A formula built only
# from the classic functions below cannot be touched by any of those substitutions, so
# it takes a shortcut; anything else still goes through xlsxwriter untouched.
CLASSIC_FUNCTIONS = {
    'AND', 'COUNT', 'COUNTIF', 'COUNTIFS', 'DATE', 'IF', 'INT', 'ISNUMBER', 'LEFT',
    'LEN', 'MAX', 'MIN', 'NOT', 'OR', 'RIGHT', 'ROUND', 'SEARCH', 'SUBTOTAL', 'SUM',
    'SUMIF', 'SUMIFS', 'TEXT', 'TODAY', 'TRIM', 'VALUE',
}
_FUNC_RE = re.compile(r'([A-Za-z][A-Za-z0-9_.]*)\s*\(')
_FAST_FORMULAS = [False]


def install_formula_fast_path():
    """Install the shortcut, but only after checking it against xlsxwriter itself.

    The two are compared on the formulas this script actually writes plus one that must
    take the long path. If they ever disagree - a new xlsxwriter release changing what
    the pass does - the shortcut is dropped and the library keeps doing the work.
    """
    if _FAST_FORMULAS[0]:
        return
    original = xlsxwriter.worksheet.Worksheet._prepare_formula

    def prepare(self, formula, expand_future_functions=False):
        if ('_xlfn.' not in formula and not formula.startswith('{')
                and not formula.endswith('}')
                and all(fn.upper() in CLASSIC_FUNCTIONS
                        for fn in _FUNC_RE.findall(formula))):
            return formula[1:] if formula.startswith('=') else formula
        return original(self, formula, expand_future_functions)

    probe = type('probe', (), {'use_future_functions': False})()
    samples = list(build_formulas(FIRST_DATA_ROW).values()) + [
        '=SUBTOTAL(9,AE3:AE99)',
        '=COUNTIFS(\'MAIN\'!$C$3:$C$99,"<>SJIT")',
        '=SUMIFS(\'MAIN\'!$AE$3:$AE$99,\'MAIN\'!$J$3:$J$99,"2026-01")',
        '=XLOOKUP(A1,B:B,C:C)',            # must still take the long path
    ]
    for f in samples:
        if prepare(probe, f) != original(probe, f):
            return                          # leave xlsxwriter alone
    xlsxwriter.worksheet.Worksheet._prepare_formula = prepare
    _FAST_FORMULAS[0] = True


def build_formats(wb):
    """Every format the workbook uses, made once.

    An xlsxwriter format is a workbook-level object shared by the cells that use it,
    not an attribute of the cell, so they are all built up front and handed out by
    name. Fewer than twenty of them cover 1.7M cells.
    """
    head = dict(BOLD, align='center', valign='vcenter', **THIN)
    f = {
        'head': wb.add_format(head),
        'head_work': wb.add_format(dict(head, bg_color=WORK_FILL)),
        'band': wb.add_format(dict(THIN, bg_color=BAND_FILL)),
        'band_total': wb.add_format(dict(BOLD, bg_color=BAND_FILL,
                                         num_format='0.00', **THIN)),
        'date': wb.add_format({'num_format': 'dd-mm-yyyy'}),
        'money': wb.add_format({'num_format': '0.00'}),
        'num': wb.add_format({'num_format': '0'}),
        'count': wb.add_format({'num_format': '0'}),
        'amount': wb.add_format({'num_format': '#,##0.00'}),
    }
    # the two total bands of the summary sheets: mid-table per account, grand total
    for tag, fill in (('mid', BAND_FILL), ('grand', WORK_FILL)):
        base = dict(BOLD, bg_color=fill)
        f['tot_%s' % tag] = wb.add_format(base)
        f['tot_%s_count' % tag] = wb.add_format(dict(base, num_format='0'))
        f['tot_%s_amount' % tag] = wb.add_format(dict(base, num_format='#,##0.00'))
    return f


def cell_format(f, header):
    """The format a data cell of this column is written with, or None for plain."""
    if header in DATE_COLS or header in DTIME_COLS:
        return f['date']
    if header in MONEY_COLS:
        return f['money']
    if header in NUM_COLS:
        return f['num']
    return None


def put(ws, row, col, v, fmt=None):
    """One cell, 0-indexed, dispatched on what the value actually is. A string
    starting with '=' is a formula - that is how the formula columns arrive."""
    if v is None:
        if fmt is not None:
            ws.write_blank(row, col, None, fmt)
    elif isinstance(v, str) and v.startswith('='):
        ws.write_formula(row, col, v, fmt)
    elif isinstance(v, (dt.datetime, dt.date)):
        ws.write_datetime(row, col, v, fmt)
    else:
        ws.write(row, col, v, fmt)

WIDTHS = {
    'seller_id': 10, 'warehouse_id': 12, 'model': 9, 'seller_order_id': 24,
    'order_group_id': 14, 'order_line_id': 14, 'return_id': 14,
    'return_created_date': 16, 'RETURN MONTH': 12, 'deliver_to_seller_date': 17,
    'type': 10, 'status': 14, 'return_status': 12,
    'forward_tracking_number': 17, 'forward_tracking_number_2': 17,
    'return_tracking_number': 17,
    'AGE (DAYS)': 10, 'CLAIM STATUS': 16, 'CLAIM TYPE': 21, 'OMSGURU STATUS': 20,
    'MANUAL RETURN STATUS': 21, 'FINAL RETURN STATUS': 19, 'TICKET ID': 12, 'CLAIM TAT': 12, 'STATUS': 12,
    'REJECTION REASON': 46, 'SPF PAID': 15, 'UTR NO': 30,
    'RETURN CHARGE': 14, 'ORDER AMOUNT': 14, 'ORDER SETTLEMENT': 16,
    'RETURN DEBIT': 14, 'NET': 12,
    'STORE ORDER ID': 22, 'DISPATCH STATUS': 15, 'DISPATCH DATE': 14,
    'DISPATCH VOUCHER': 18, 'PICKUP POD': 12, 'RETURN POD': 12,
    RETURN_POD_DATE: 16,
}

NOTES = {
    'return_created_date': ('Fallback clock start. For type=Return this is the return\n'
                            'creation date, for type=RTO the RTO creation date. The\n'
                            'source reports keep these in two separate columns but never\n'
                            'fill both (verified across 84,901 rows), so they are one.'),
    'RETURN MONTH': ('The return_created_date month as yyyy-mm, so sorting or\n'
                     'filtering this column A to Z gives chronological order.\n'
                     'Rows are already written oldest month first.'),
    'deliver_to_seller_date': ('From the Return Delivery Report - when the portal handed\n'
                               'the shipment back to the seller. When this is filled, it\n'
                               'is where AGE starts counting from.'),
    'forward_tracking_number': ('Forward AWB. The Return Delivery Report calls this\n'
                                'forward_tracking_number_1 - on the 34,580 overlapping\n'
                                'rows both values were identical, so it is one column.'),
    'AGE (DAYS)': ('Days from the clock start until TODAY.\n'
                   'Clock start = deliver_to_seller_date when there is one,\n'
                   'otherwise return_created_date.'),
    'CLAIM STATUS': ('Per SPF POLICY.pdf Annexure 1.\n\n'
                     'CLAIM DONE - a ticket has already been raised\n\n'
                     'SJIT / FBM order -> NON ELIGIBLE, these are not worked\n\n'
                     'you confirmed you have it (FINAL RETURN STATUS =\n'
                     'Return Received) -> NON ELIGIBLE, nothing to claim\n\n'
                     'delivery date present - return not received claim,\n'
                     '7 days from the delivery date:\n'
                     '  AGE <= 7  -> ELIGIBLE\n'
                     '  AGE > 7   -> EXPIRED\n\n'
                     'no delivery date (clause 8):\n'
                     '  AGE < 60      -> UPCOMING CLAIM\n'
                     '  AGE 60 to 120 -> ELIGIBLE\n'
                     '  AGE > 120     -> EXPIRED'),
    'CLAIM TYPE': ('RECEIVED            - you confirmed you have it\n'
                   'RETURN NOT RECEIVED - portal handed it back, you never got it\n'
                   'RETURN IN TRANSIT   - still on the way\n'
                   'NO RETURN TRACKING  - the return has no tracking number at all'),
    'OMSGURU STATUS': ('Yours to fill in. The script leaves it alone, with one'
                       '\n'
                       'exception: when the GR/GRN report has booked this'
                       '\n'
                       'parcel in, it is set to "Return Received ERP". A goods'
                       '\n'
                       'receipt is physical proof the return is back, so it'
                       '\n'
                       'outranks whatever in-flight status was there.'),
    'MANUAL RETURN STATUS': ('Yours to fill in. The script only writes here when the\n'
                             'manual return sheet has a RECD row whose order id,\n'
                             'return id or AWB matches this row.'),
    'TICKET ID': 'From the ticket report. Your previous value stays if none is found.',
    'CLAIM TAT': ('Was the claim raised inside the SPF window?\n'
                  'WITHIN TAT: within 7 days of the return delivered to us, or from\n'
                  'day 60 to day 120 after return_created_date if it never came back.\n'
                  'OUTSIDE TAT: any other day. Blank: the ticket date is not known.'),
    'STATUS': 'The SPF ticket portal status (ACCEPT / REJECT / OPEN).',
    'REJECTION REASON': ('From the ticket report - why Myntra rejected the claim.\n'
                         'Only filled when STATUS is REJECT.'),
    'SPF PAID': ('What Myntra actually paid for this claim, taken from the\n'
                 'payment report (Settled_Amount on the NOD payout line).\n\n'
                 'NOT the paymentAmount column - that one is the total of the\n'
                 'whole NEFT batch and is identical on every line sharing a UTR.\n\n'
                 'Blank means the payment report has no payout for this order\n'
                 'line yet.'),
    'NET': ('What this order line actually left in the bank:'
            '\n'
            'ORDER SETTLEMENT + RETURN DEBIT + SPF PAID.'
            '\n\n'
            'RETURN CHARGE is not added - it already sits inside RETURN DEBIT.'
            '\n\n'
            'Negative means the return cost you more than the sale and the'
            '\n'
            'SPF claim brought back.'),
    'UTR NO': ('Bank reference of the payout that carried this claim - use it to\n'
               'find the credit in the bank statement. Several claims normally\n'
               'share one UTR because they were paid in the same batch.'),
    'STORE ORDER ID': ('The portal store order id, from the Seller Orders Report,\n'
                       'matched on order line id.\n\n'
                       'Myntra exports it with a leading apostrophe because it is 21\n'
                       'digits - too long to survive as a number - so it is kept as\n'
                       'text here and the apostrophe stripped.'),
    'ORDER AMOUNT': ('What the customer paid for this line, from the Seller Orders\n'
                     'Report: `final amount` = `total mrp` - `discount`.\n\n'
                     'Not `total mrp`, which is the list price nobody paid, and not\n'
                     '`seller price`, the smaller ex-charges figure the payment\n'
                     'report calls customer_paid_amt - that is the wrong number this\n'
                     'column held before 2026-09-09.\n\n'
                     'Blank means this order line is not in the order report yet.'),
    'ORDER SETTLEMENT': ('What Myntra actually paid out for the order, from the'
                         '\n'
                         'payment report (Settled_Amount on the Forward lines). This'
                         '\n'
                         'is ORDER AMOUNT less commission, logistics and tax'
                         '\n'
                         'deductions - the money that reached the bank for the sale.'
                         '\n\n'
                         'Myntra sometimes splits one order across two payouts;'
                         '\n'
                         'those lines are added together here.'
                         '\n\n'
                         'It is the SALE leg only. What the return took back is'
                         '\n'
                         'not netted off - see RETURN CHARGE and SPF PAID.'),
    'RETURN DEBIT': ('The whole return reversal, from the payment report'
                     '\n'
                     '(Settled_Amount on the Reverse line). Negative: Myntra takes'
                     '\n'
                     'back the refund it gave the customer AND charges the return'
                     '\n\n'
                     'RETURN CHARGE is one PART of this figure, not an extra'
                     '\n'
                     'deduction on top of it. Do not subtract it again.'),
    'RETURN CHARGE': ('What Myntra charged you for handling this return, from the\n'
                      'payment report (Logistics_Commission on the Reverse line).\n'
                      'GST included - it works out to exactly 1.18 x the fee base.\n\n'
                      'This is the fee only. The refund of what the customer paid is\n'
                      'a separate figure and is NOT counted here.\n\n'
                      'Blank means no fee was charged on this return, or the payment\n'
                      'report has no Reverse line that can be matched to it.'),
}


def backup():
    if not os.path.exists(MASTER):
        return
    stamp = dt.datetime.now().strftime('%Y-%m-%d_%H%M')
    dest = os.path.join(HERE, 'MYNTRA SPF MASTER_BACKUP_%s.xlsx' % stamp)
    shutil.copy2(MASTER, dest)
    print('  backup: %s' % os.path.basename(dest))
    olds = sorted(f for f in os.listdir(HERE)
                  if f.startswith('MYNTRA SPF MASTER_BACKUP_') and f.endswith('.xlsx'))
    for f in olds[:-BACKUP_KEEP]:
        os.remove(os.path.join(HERE, f))


# Columns that get a SUBTOTAL band, and the label that sits to their left
SUBTOTAL_COLS = ['ORDER AMOUNT', 'ORDER SETTLEMENT', 'RETURN DEBIT', 'RETURN CHARGE',
                 'SPF PAID', 'NET']

def write_subtotals(ws, f, last):
    """SUBTOTAL(9) over the data for every money column, in row 1.

    SUBTOTAL(9) - not SUM - because it counts only the rows the autofilter is showing.
    Filter CLAIM STATUS to ELIGIBLE and the band immediately totals just those rows.
    """
    for i, h in enumerate(HEADERS):
        if h in SUBTOTAL_COLS:
            ws.write_formula(SUBTOTAL_ROW - 1, i,
                             '=SUBTOTAL(9,{0}{1}:{0}{2})'.format(
                                 L(i + 1), FIRST_DATA_ROW, last),
                             f['band_total'])
        else:
            ws.write_blank(SUBTOTAL_ROW - 1, i, None, f['band'])
    ws.set_row(SUBTOTAL_ROW - 1, 18)


# ------------------------------------------------------------------------- summary
# FINAL RETURN STATUS is the column everything turns on: a value there (Return
# Received / SPF Paid / SPF Accepted / Payment Received) means the return is settled one way or
# another, and BLANK means it is still unresolved. The summaries are built around it.
#
# Every cell is a COUNTIFS / SUMIFS over the full account sheet, so a status edited by
# hand, or a re-sort of the sheet, still gives the right numbers.
#   (label, kind, value column, [(criteria column, criteria), ...])
_PENDING = ('FINAL RETURN STATUS', '')
SUMMARY_COLS = [
    ('TOTAL RETURNS', 'count', None, []),
    ('RETURN RECEIVED', 'count', None, [('FINAL RETURN STATUS', 'Return Received')]),
    ('SPF PAID', 'count', None, [('FINAL RETURN STATUS', 'SPF Paid')]),
    ('SPF ACCEPTED', 'count', None, [('FINAL RETURN STATUS', 'SPF Accepted')]),
    ('SPF ACCEPTED AMT', 'sum', 'ORDER AMOUNT', [('FINAL RETURN STATUS', 'SPF Accepted')]),
    ('PAYMENT RECEIVED', 'count', None, [('FINAL RETURN STATUS', 'Payment Received')]),
    ('PENDING', 'count', None, [_PENDING]),
    ('PENDING AMT', 'sum', 'ORDER AMOUNT', [_PENDING]),
    ('ELIGIBLE CLAIMS', 'count', None, [('CLAIM STATUS', 'ELIGIBLE')]),
    ('PENDING CLAIM AMT', 'sum', 'ORDER AMOUNT', [('CLAIM STATUS', 'ELIGIBLE')]),
    ('UPCOMING CLAIM AMT', 'sum', 'ORDER AMOUNT', [('CLAIM STATUS', 'UPCOMING CLAIM')]),
    ('CLAIM DONE', 'count', None, [('CLAIM STATUS', 'CLAIM DONE')]),
    ('SPF PAID AMT', 'sum', 'SPF PAID', []),
    ('SETTLEMENT RECEIVED', 'sum', 'ORDER SETTLEMENT', []),
    ('RETURN DEBIT', 'sum', 'RETURN DEBIT', []),
    ('RETURN CHARGE', 'sum', 'RETURN CHARGE', []),
    ('NET', 'sum', 'NET', []),
]
# The day-wise sheet has ~750 rows, so it carries only the columns that answer "what is
# still open from that day" - enough to act on without making Excel crawl.
DATE_SUMMARY_COLS = [c for c in SUMMARY_COLS if c[0] in (
    'TOTAL RETURNS', 'RETURN RECEIVED', 'SPF PAID', 'SPF ACCEPTED', 'SPF ACCEPTED AMT', 'PAYMENT RECEIVED', 'PENDING',
    'PENDING AMT', 'ELIGIBLE CLAIMS', 'PENDING CLAIM AMT', 'SPF PAID AMT')]
SUMMARY_SHEET = 'SUMMARY'
DATE_SUMMARY_SHEET = 'DATE SUMMARY'
# SJIT and FBM orders are not worked for SPF: no claim, no OMSGuru check, left out of both
# summaries - only the rest (PPMP) is totalled. Their rows stay on the account sheets.
NOT_WORKED_MODELS = ['SJIT', 'FBM']
SUMMARY_EXCLUDE_MODELS = NOT_WORKED_MODELS



# ------------------------------------------------------------------- claim TAT sheet
# Every claim raised (a TICKET ID on an account sheet), by account and the month the
# ticket was created: how many, and how many inside the SPF policy window -
#   delivered back (deliver_to_seller_date) -> raised within 7 days of the delivery
#   never delivered back                    -> raised from day 60 to day 120 after
#                                              return_created_date
# with what became of them. Myntra order lines are one unit each, so QTY = claims.
# Worked out here as values (the ticket's date is not on the account sheets).
CLAIM_TAT_SHEET = 'CLAIM TAT'
CLAIM_TAT_COLS = ['CLAIMS RAISED', 'CLAIM QTY', 'CLAIM AMT', 'WITHIN TAT', 'WITHIN TAT QTY',
                  'WITHIN TAT AMT', 'OUTSIDE TAT', 'OUTSIDE TAT QTY', 'OUTSIDE TAT AMT',
                  'ACCEPTED', 'ACCEPTED AMT', 'PAID', 'PAID AMT', 'SPF PAID AMT',
                  'REJECTED', 'REJECTED AMT', 'OPEN', 'OPEN AMT']
CLAIM_PAID_STATUSES = {'PAYMENT_COMPLETED'}
CLAIM_ACCEPTED_STATUSES = {'ACCEPT', 'INVOICE_CREATED', 'INVOICE_INITIATED', 'PAYMENT_INITIATED'}


def claim_tat(rec):
    """WITHIN / OUTSIDE the policy window for the claim on this row, '' when its date is unknown."""
    raised = rec.get('_ticket_created')
    if not isinstance(raised, dt.datetime) or raised == dt.datetime.min:
        return ''
    delivered = rec.get('deliver_to_seller_date')
    if isinstance(delivered, dt.datetime) and delivered.date() <= raised.date():
        return 'WITHIN' if (raised.date() - delivered.date()).days <= 7 else 'OUTSIDE'
    created = rec.get('return_created_date')
    if not isinstance(created, dt.datetime):
        return ''
    return 'WITHIN' if 60 <= (raised.date() - created.date()).days <= 120 else 'OUTSIDE'


def claim_outcome(rec):
    st = norm(rec.get('STATUS'))
    if st in CLAIM_PAID_STATUSES or rec.get('SPF PAID') is not None:
        return 'PAID'
    if st in CLAIM_ACCEPTED_STATUSES:
        return 'ACCEPTED'
    if st == 'REJECT':
        return 'REJECTED'
    return 'OPEN'


def claim_tat_rows(data):
    """{(account, month): {column: value}} over every row with a ticket."""
    out = {}
    for name in SHEETS:
        for rec in data[name]:
            tid = rec.get('TICKET ID')
            if tid in (None, ''):
                continue
            raised = rec.get('_ticket_created')
            month = (raised.strftime('%Y-%m') if isinstance(raised, dt.datetime)
                     and raised != dt.datetime.min else 'NO DATE')
            amt = rec.get('ORDER AMOUNT') if isinstance(rec.get('ORDER AMOUNT'), (int, float)) else 0
            row = out.setdefault((name, month), dict.fromkeys(CLAIM_TAT_COLS, 0))
            row['CLAIMS RAISED'] += 1
            row['CLAIM QTY'] += 1
            row['CLAIM AMT'] += amt
            tat = claim_tat(rec) or str(rec.get('CLAIM TAT') or '').replace(' TAT', '')
            if tat:
                row['%s TAT' % tat] += 1
                row['%s TAT QTY' % tat] += 1
                row['%s TAT AMT' % tat] += amt
            outcome = claim_outcome(rec)
            row[outcome] += 1
            row[outcome + ' AMT'] += amt
            if isinstance(rec.get('SPF PAID'), (int, float)):
                row['SPF PAID AMT'] += rec['SPF PAID']
    return out


def write_claim_tat(wb, f, data):
    rows = claim_tat_rows(data)
    ws = wb.add_worksheet(CLAIM_TAT_SHEET)
    head = ['ACCOUNT', 'TICKET MONTH'] + CLAIM_TAT_COLS
    for i, h in enumerate(head):
        put(ws, 0, i, h, f['head_work'])
        ws.set_column(i, i, 12 if i < 2 else 15)
    ws.freeze_panes(1, 2)

    def line(r, account, label, vals, tag=None):
        put(ws, r, 0, account, f['tot_%s' % tag] if tag else None)
        put(ws, r, 1, label, f['tot_%s' % tag] if tag else None)
        for j, c in enumerate(CLAIM_TAT_COLS, start=2):
            kind = 'amount' if c.endswith('AMT') else 'count'
            put(ws, r, j, vals[c], f['tot_%s_%s' % (tag, kind)] if tag else f[kind])

    r, grand = 1, dict.fromkeys(CLAIM_TAT_COLS, 0)
    for name in SHEETS:
        months = sorted(m for a, m in rows if a == name)
        if not months:
            continue
        total = dict.fromkeys(CLAIM_TAT_COLS, 0)
        for m in months:
            line(r, name, m, rows[(name, m)])
            for c in CLAIM_TAT_COLS:
                total[c] += rows[(name, m)][c]
                grand[c] += rows[(name, m)][c]
            r += 1
        line(r, name, 'TOTAL', total, 'mid')
        r += 2
    line(r, 'ALL', 'GRAND TOTAL', grand, 'grand')
    r += 2
    put(ws, r, 0, 'WITHIN TAT: raised within 7 days of the return delivered to us, or from day 60 to '
                  'day 120 after the return was created when it never came back (SPF policy).')
    put(ws, r + 1, 0, 'Amounts are ORDER AMOUNT of the claimed order lines; SPF PAID AMT is what Myntra paid.')


def write_summary(wb, f, data, title, cols, by_day):
    """Account-wise summary on its own sheet, bucketed by return month or by day.

    Months match the RETURN MONTH text. Days match return_created_date with a >= / <
    window rather than equality, so a date that carries a time of day still counts.
    The sheets are created before the account sheets because xlsxwriter cannot reorder
    them afterwards, and these two belong at the front.
    """
    ws = wb.add_worksheet(title)
    head = ['ACCOUNT', 'RETURN DATE' if by_day else 'RETURN MONTH'] + [c[0] for c in cols]
    for i, h in enumerate(head):
        put(ws, 0, i, h, f['head_work'])
        ws.set_column(i, i, 12 if i < 2 else 19)
    ws.freeze_panes(1, 2)

    def put_total(r, account, label, rows, tag):
        put(ws, r - 1, 0, account, f['tot_%s' % tag])
        put(ws, r - 1, 1, label, f['tot_%s' % tag])
        for j, (_, kind, _, _) in enumerate(cols, start=3):
            col = L(j)
            formula = ('=SUM(%s%d:%s%d)' % (col, rows[0], col, rows[1])
                       if isinstance(rows, tuple)
                       else '=' + '+'.join('%s%d' % (col, x) for x in rows))
            put(ws, r - 1, j - 1, formula,
                f['tot_%s_%s' % (tag, 'count' if kind == 'count' else 'amount')])

    r = 2
    totals = []
    for name in SHEETS:
        recs = [rec for rec in data[name]
                if isinstance(rec.get('return_created_date'), dt.datetime)
                and str(rec.get('model') or '').upper() not in SUMMARY_EXCLUDE_MODELS]
        if not recs:
            continue
        last = len(data[name]) + FIRST_DATA_ROW - 1
        ranges = {h: "'%s'!$%s$%d:$%s$%d" % (name, C[h], FIRST_DATA_ROW, C[h], last)
                  for h in HEADERS}
        rng = ranges.__getitem__
        base = []
        for mdl in SUMMARY_EXCLUDE_MODELS:
            base += [rng('model'), '"<>%s"' % mdl]
        if by_day:
            buckets = sorted({rec['return_created_date'].date() for rec in recs})
        else:
            buckets = sorted({rec['return_created_date'].strftime('%Y-%m') for rec in recs})

        first = r
        for bk in buckets:
            put(ws, r - 1, 0, name)
            if by_day:
                put(ws, r - 1, 1, dt.datetime(bk.year, bk.month, bk.day), f['date'])
                d = 'DATE(%d,%d,%d)' % (bk.year, bk.month, bk.day)
                when = [rng('return_created_date'), '">="&' + d,
                        rng('return_created_date'), '"<"&(' + d + '+1)']
            else:
                put(ws, r - 1, 1, bk)
                when = [rng('RETURN MONTH'), '"%s"' % bk]
            for j, (_, kind, val, crit) in enumerate(cols, start=3):
                parts = when + base
                for ch, cv in crit:
                    parts += [rng(ch), '"%s"' % cv]
                formula = ('=COUNTIFS(%s)' % ','.join(parts) if kind == 'count'
                           else '=SUMIFS(%s,%s)' % (rng(val), ','.join(parts)))
                put(ws, r - 1, j - 1, formula,
                    f['count'] if kind == 'count' else f['amount'])
            r += 1
        put_total(r, name, 'TOTAL', (first, r - 1), 'mid')
        totals.append(r)
        r += 2
    if totals:
        put_total(r, 'ALL', 'GRAND TOTAL', totals, 'grand')
    return ws, r


# ------------------------------------------------------------------ input coverage
COVERAGE_KINDS = [('TRANSIT', 'RETURN IN TRANSIT'), ('DELIVERED', 'RETURN DELIVERED'),
                  ('ORDER', 'ORDER REPORT')]


def _gaps(periods):
    """Sorted (from, to) date pairs -> the day ranges none of them cover, as text."""
    out = []
    reach = periods[0][1]
    for start, end in periods[1:]:
        if start > reach + dt.timedelta(days=1):
            a, b = reach + dt.timedelta(days=1), start - dt.timedelta(days=1)
            out.append(a.strftime('%d-%m-%Y') if a == b else
                       '%s to %s' % (a.strftime('%d-%m-%Y'), b.strftime('%d-%m-%Y')))
        reach = max(reach, end)
    return ', '.join(out)


# Every returns report ever processed is listed on the INPUT LOG sheet of the master,
# because the files themselves are deleted once used (see cleanup_inputs). The coverage
# table is built from that log, so it still shows the whole history.
INPUT_LOG_SHEET = 'INPUT LOG'
INPUT_LOG_COLS = ['ACCOUNT', 'REPORT', 'FROM', 'TO', 'FILE', 'PROCESSED ON']


def read_input_log():
    """The INPUT LOG of the previous master, as (account, report, from, to, file,
    processed on) tuples."""
    if not os.path.exists(MASTER) or INPUT_LOG_SHEET not in _sheet_names(MASTER):
        return []
    it = _iter_sheet(MASTER, INPUT_LOG_SHEET)
    next(it, None)
    out = []
    for row in it:
        row = (list(row) + [None] * 6)[:6]
        if row[0] and isinstance(row[2], dt.datetime) and isinstance(row[3], dt.datetime):
            out.append(tuple(row))
    return out


def input_log():
    """The previous log plus every dated returns report in input/ now."""
    seen = {(e[0], e[1], e[4]): e for e in read_input_log()}
    today = dt.datetime.combine(dt.date.today(), dt.time())
    labels = dict(COVERAGE_KINDS)
    for name in SHEETS:
        for path in input_files(name):
            label = labels.get(kind_of(path))
            m = PERIOD_RE.search(os.path.basename(path))
            if not label or not m:
                continue
            a, b = (dt.datetime.strptime(x, '%Y-%m-%d') for x in m.groups())
            fn = os.path.basename(path)
            seen.setdefault((name, label, fn), (name, label, a, b, fn, today))
    order = {n: i for i, n in enumerate(SHEETS)}
    return sorted(seen.values(), key=lambda e: (order.get(e[0], 9), e[1], e[2], e[3]))


def input_coverage(log):
    """Per account and returns report type: (files, first day, last day, missing days,
    files in input/ with no period in their name)."""
    undated = {}
    for name in SHEETS:
        for path in input_files(name):
            kind = kind_of(path)
            if (kind in dict(COVERAGE_KINDS)
                    and not PERIOD_RE.search(os.path.basename(path))):
                undated[(name, kind)] = undated.get((name, kind), 0) + 1
    cov = []
    for name in SHEETS:
        for kind, label in COVERAGE_KINDS:
            periods = sorted((e[2], e[3]) for e in log if e[0] == name and e[1] == label)
            u = undated.get((name, kind), 0)
            if periods:
                cov.append((name, label, len(periods) + u, periods[0][0],
                            max(p[1] for p in periods), _gaps(periods) or 'NONE', u))
            else:
                cov.append((name, label, u, None, None, '', u))
    return cov


def orders_missing(rows):
    """(count, first, last return date) of the returns with no ORDER AMOUNT - their
    order is in no order report read so far, which says which one to download."""
    days = [r.get('return_created_date') for r in rows if r.get('ORDER AMOUNT') is None]
    dated = [d for d in days if isinstance(d, dt.datetime)]
    return len(days), min(dated, default=None), max(dated, default=None)


def write_coverage(ws, f, r, log, data):
    """The date range of the reports processed so far, below the summary."""
    put(ws, r - 1, 0, 'INPUT DATA COVERAGE (every report processed - see INPUT LOG)',
        f['tot_grand'])
    r += 1
    for i, h in enumerate(['ACCOUNT', 'REPORT', 'FROM', 'TO', 'FILES', 'MISSING DATES',
                           'NOTE']):
        put(ws, r - 1, i, h, f['head_work'])
    for name, label, n, first, last, gaps, undated in input_coverage(log):
        if label == 'ORDER REPORT':
            count, a, b = orders_missing(data.get(name, []))
            if count:
                put(ws, r, 6, '%d returns have no order amount (returned %s to %s) - '
                    'download the order report for the weeks before them'
                    % (count, a.strftime('%d-%m-%Y') if a else '?',
                       b.strftime('%d-%m-%Y') if b else '?'))
        r += 1
        put(ws, r - 1, 0, name)
        put(ws, r - 1, 1, label)
        put(ws, r - 1, 2, first, f['date'])
        put(ws, r - 1, 3, last, f['date'])
        put(ws, r - 1, 4, n, f['count'])
        note = gaps if first else 'NO FILES'
        if undated:
            note += '  (%d file(s) with no period in the name)' % undated
        put(ws, r - 1, 5, note)


def write_input_log(wb, f, log):
    ws = wb.add_worksheet(INPUT_LOG_SHEET)
    for i, h in enumerate(INPUT_LOG_COLS):
        put(ws, 0, i, h, f['head_work'])
        ws.set_column(i, i, (12, 20, 12, 12, 42, 14)[i])
    ws.freeze_panes(1, 0)
    for r, e in enumerate(log, start=1):
        for i, v in enumerate(e):
            put(ws, r, i, v, f['date'] if i in (2, 3, 5) else None)



def write_master(data):
    """Write the workbook.

    xlsxwriter, not openpyxl: 47k rows x 36 columns is ~1.7M cells and openpyxl builds
    a Python object for each one. In constant_memory mode each row is serialised the
    moment it is written and then dropped, so the sheet never sits in memory - the one
    rule that imposes is that rows have to be written in order, which is why the
    SUBTOTAL band (row 1) is written before the headers and the data.
    """
    install_formula_fast_path()
    # read before the old master is overwritten
    log = input_log()
    wb = xlsxwriter.Workbook(MASTER, {'constant_memory': True,
                                      'default_date_format': 'dd-mm-yyyy'})
    f = build_formats(wb)
    # the two summary sheets first - xlsxwriter keeps sheets in creation order
    ws, r = write_summary(wb, f, data, SUMMARY_SHEET, SUMMARY_COLS, False)
    write_coverage(ws, f, r + 3, log, data)
    write_summary(wb, f, data, DATE_SUMMARY_SHEET, DATE_SUMMARY_COLS, True)
    write_claim_tat(wb, f, data)

    col_fmt = [cell_format(f, h) for h in HEADERS]
    # Which columns hold what, decided once for all three sheets. Per cell this was
    # three isinstance checks and a dict lookup, 1.7M times over.
    is_date = [h in DATE_COLS or h in DTIME_COLS for h in HEADERS]
    is_formula = [h in FORMULA_COLS for h in HEADERS]
    for name in SHEETS:
        ws = wb.add_worksheet(name)
        last = len(data[name]) + FIRST_DATA_ROW - 1
        # Row 1 is the SUBTOTAL band, row 2 the headers, data from row 3. The totals sit
        # ABOVE the headers on purpose: that keeps them outside the autofilter range, so
        # filtering can never hide them, and the frozen pane keeps them on screen while
        # you scroll 27k rows.
        write_subtotals(ws, f, last)
        for i, h in enumerate(HEADERS):
            put(ws, HEADER_ROW - 1, i, h, f['head_work'] if h in WORK else f['head'])
            # the number format is set on the column, so a data cell carries no format
            # of its own - and an empty cell needs no write at all to keep it
            ws.set_column(i, i, WIDTHS.get(h, 14), col_fmt[i])
            if h in NOTES:
                ws.write_comment(HEADER_ROW - 1, i, NOTES[h],
                                 {'width': 430, 'height': 230})
        ws.set_row(HEADER_ROW - 1, 18)
        ws.freeze_panes(FIRST_DATA_ROW - 1, 0)

        for r, rec in enumerate(data[name], start=FIRST_DATA_ROW):
            row = r - 1
            formulas = build_formulas(r)
            # a formula column you overwrote by hand keeps your value
            locked = rec.get('_locked') or ()
            for i, h in enumerate(HEADERS):
                if is_formula[i] and h not in locked:
                    ws.write_formula(row, i, formulas[h])
                    continue
                v = rec.get(h)
                if v is None:
                    continue
                if is_date[i] and isinstance(v, (dt.datetime, dt.date)):
                    ws.write_datetime(row, i, v)
                else:
                    ws.write(row, i, v)
        if data[name]:
            ws.autofilter(HEADER_ROW - 1, 0, last - 1, len(HEADERS) - 1)
    write_parked(wb, is_date)
    write_ticket_details(wb, f)
    write_input_log(wb, f, log)
    wb.close()


def write_ticket_details(wb, f):
    """Bot only: the TICKETS sheet - every recent / open SPF claim of the three accounts as its
    portal page shows it (status history, reason, payout, Seller Support's last reply), from
    TICKET_DETAILS (bindal/myntra_spf/tickets.py). Claims waiting on our answer come first."""
    from .tickets import DETAIL_COLS, DETAIL_SHEET
    if not TICKET_DETAILS:
        return
    rows = sorted(TICKET_DETAILS, key=lambda d: (d.get('NEEDS OUR REPLY') != 'YES', str(d.get('ACCOUNT')),
                                                 -int(str(d.get('TICKET ID') or 0) or 0)))
    ws = wb.add_worksheet(DETAIL_SHEET)
    hdr = wb.add_format({'bold': True, 'bg_color': '#1F4E78', 'font_color': '#FFFFFF', 'border': 1,
                         'text_wrap': True, 'valign': 'vcenter'})
    alert = wb.add_format({'bg_color': '#FFC7CE'})
    widths = {'ACCOUNT': 8, 'TICKET ID': 11, 'ORDER LINE ID': 14, 'RETURN ID': 14, 'ORDER ID': 14,
              'CATEGORY': 18, 'STATUS': 18, 'NEEDS OUR REPLY': 9, 'CREATED': 17, 'LAST UPDATED': 17,
              'STATUS HISTORY': 40, 'REASON': 40, 'COMPENSATION': 12, 'UTR': 18, 'PAYMENT INITIATED': 17,
              'DESCRIPTION': 30, 'SUPPORT TICKET': 12, 'REPLIES': 8, 'LAST REPLY BY': 16,
              'LAST REPLY AT': 17, 'LAST REPLY': 70}
    for i, h in enumerate(DETAIL_COLS):
        ws.write(0, i, h, hdr)
        ws.set_column(i, i, widths.get(h, 14))
    for r, d in enumerate(rows, start=1):
        for i, h in enumerate(DETAIL_COLS):
            v = d.get(h)
            if v not in (None, ''):
                ws.write(r, i, v, alert if h == 'NEEDS OUR REPLY' else None)
    ws.freeze_panes(1, 2)
    ws.autofilter(0, 0, len(rows), len(DETAIL_COLS) - 1)


# Bot only: the ticket files the bot writes from the portal (bindal/myntra_spf/tickets.py)
PORTAL_TICKET_FILES = {'SPF Tickets (portal).xlsx', 'SPF Tickets (new).xlsx', 'SPF Tickets (raised by bot).xlsx'}
# Bot only: the TICKETS sheet's rows, filled by bindal/myntra_spf/pipeline.py
TICKET_DETAILS = []


def write_parked(wb, is_date):
    """The young in-transit rows left out of the account sheets, on a hidden sheet of
    their own, so the next run reads them back (read_old_master) and moves each one to
    its account sheet once it is YOUNG_TRANSIT_DAYS old. Without this a return left out
    would be gone for good once it falls outside the portal reports' date window."""
    rows = [(name, rec) for name in SHEETS for rec in PARKED.get(name, [])]
    if not rows:
        return
    ws = wb.add_worksheet(PARKED_SHEET)
    ws.hide()
    hdr = [PARKED_ACCOUNT] + HEADERS
    for i, h in enumerate(hdr):
        ws.write(0, i, h)
    formula = set(FORMULA_COLS)
    for r, (name, rec) in enumerate(rows, start=1):
        ws.write(r, 0, name)
        locked = rec.get('_locked') or ()
        for i, h in enumerate(HEADERS, start=1):
            v = rec.get(h)
            if v is None or (h in formula and h not in locked):
                continue
            if is_date[i - 1] and isinstance(v, (dt.datetime, dt.date)):
                ws.write_datetime(r, i, v)
            else:
                ws.write(r, i, v)


# ------------------------------------------------------------------ input cleanup
# Once the master is written, a report whose data is all in it has done its job and is
# deleted, so input/ only ever holds files with something still pending. The master
# keeps every row it has ever had (see read_old_master), so nothing is lost with them.
#   in-transit / delivered report - used once every return in it is a master row. A
#       young in-transit return left out of the sheet keeps its file until it gets in.
#   order report - its value is only read when a return for that order turns up, and
#       returns come within 26 days of the order in 99.9% of 49,282 cases (the longest
#       seen was 113). So it is kept until ORDER_KEEP_DAYS after its period ends.
#   payment, ticket, GRN, dispatch - running exports refreshed on every run, never
#       deleted here: replace them with the newer download yourself.
ORDER_KEEP_DAYS = 90
CLEANED_KINDS = ('TRANSIT', 'DELIVERED', 'ORDER')


def _pending(path, kind, keys, today):
    """What in this report is not used up yet, as text, or None when nothing is."""
    if kind == 'ORDER':
        m = PERIOD_RE.search(os.path.basename(path))
        if not m:
            return 'no period in the name'
        end = dt.datetime.strptime(m.group(2), '%Y-%m-%d').date()
        left = ORDER_KEEP_DAYS - (today - end).days
        return 'returns can still come for %d days' % left if left >= 0 else None
    _, recs, _, _, _ = cached('returns', path, _parse_returns)
    n = sum(1 for r in recs if row_key(r) not in keys)
    return '%d returns not in the master yet' % n if n else None


def cleanup_inputs(data, log, dry_run=False):
    """Delete the reports whose data is all in the master that was just written."""
    today = dt.date.today()
    gone = kept = 0
    for name in SHEETS:
        keys = {row_key(r) for r in data[name]}
        for path in input_files(name):
            kind = kind_of(path)
            if kind not in CLEANED_KINDS:
                continue
            rel = os.path.relpath(path, INPUT_DIR)
            why = _pending(path, kind, keys, today)
            if why:
                kept += 1
                log('  %-58s kept - %s' % (rel[:58], why))
                continue
            gone += 1
            log('  %-58s used - %s' % (rel[:58], 'to delete' if dry_run else 'deleted'))
            if not dry_run:
                os.remove(path)
    log('  %d report(s) %s, %d still pending\n'
        % (gone, 'to delete' if dry_run else 'deleted', kept))


# ------------------------------------------------------------- young in-transit rows
def is_settled(rec):
    """Python twin of the FINAL RETURN STATUS formula: True when that cell would hold a
    value (SPF Paid / SPF Accepted / Return Received / Payment Received), i.e. the return is resolved."""
    if rec.get('FINAL RETURN STATUS') not in (None, ''):     # typed over the formula
        return True
    if rec.get('SPF PAID') is not None:
        return True
    if norm(rec.get('STATUS')) in SPF_ACCEPTED_STATUSES and rec.get('TICKET ID') not in (None, ''):
        return True
    oms = str(rec.get('OMSGURU STATUS') or '').upper()
    man = str(rec.get('MANUAL RETURN STATUS') or '').upper()
    if 'RECEIVED' in oms or 'RECEIVED' in man or 'PICKUP NOT DONE' in man:
        return True
    return (isinstance(rec.get('ORDER SETTLEMENT'), (int, float))
            and rec.get('RETURN DEBIT') is None and rec.get('RETURN CHARGE') is None)


# SPF ticket statuses that mean the claim passed (FINAL RETURN STATUS = SPF Accepted)
SPF_ACCEPTED_STATUSES = {'ACCEPT', 'INVOICE_INITIATED', 'PAYMENT_COMPLETED'}
IN_TRANSIT_STATUSES = {'RET CREATED', 'RET DISPATCHED', 'RTO CREATED', 'DISPATCHED'}


def is_young_in_transit(rec, today):
    """Exactly the rows the sheet shows as CLAIM TYPE = RETURN IN TRANSIT with
    AGE (DAYS) < YOUNG_TRANSIT_DAYS, and nothing else.

    Never matched: anything delivered (a delivery date, or "Delivered" in the status),
    anything resolved (FINAL RETURN STATUS would hold a value), and a Return with no
    return tracking number whose status is not a transit one - the sheet calls that
    NO RETURN TRACKING, not in transit. With no delivery date, AGE counts from
    return_created_date, so that is the date the age is taken from.
    """
    created = rec.get('return_created_date')
    if not isinstance(created, dt.datetime):
        return False
    if rec.get('deliver_to_seller_date') or is_delivered_status(rec.get('status')):
        return False
    if is_settled(rec):
        return False
    if (norm(rec.get('status')) not in IN_TRANSIT_STATUSES
            and norm(rec.get('type')) == 'RETURN'
            and not rec.get('return_tracking_number')):
        return False
    return (today - created.date()).days < YOUNG_TRANSIT_DAYS


def ask_yes_no(question):
    while True:
        try:
            ans = input(question).strip().lower()
        except EOFError:
            return None
        if ans in ('y', 'yes'):
            return True
        if ans in ('n', 'no'):
            return False
        print('  please type y or n')


def drop_young_in_transit(data, args):
    """Ask whether young in-transit, unreceived returns belong in the sheet.

    --in-transit y/n answers up front. Otherwise the question is asked in the terminal.
    With no terminal to ask (output piped, scheduled run) the rows are kept, since
    leaving data out silently is the worse mistake. A dry run with no answer only
    reports the count.
    """
    today = dt.date.today()
    young = {name: [r for r in rows if is_young_in_transit(r, today)]
             for name, rows in data.items()}
    total = sum(len(v) for v in young.values())
    if not total:
        return
    print('RETURNS IN TRANSIT, NOT RECEIVED, CREATED LESS THAN %d DAYS AGO: %d'
          % (YOUNG_TRANSIT_DAYS, total))
    for name in SHEETS:
        print('  %-5s %6d' % (name, len(young.get(name, []))))

    if args.in_transit:
        keep = args.in_transit == 'y'
    elif args.dry_run:
        print('  (dry run - not asking; pass --in-transit y or n to preview either way)\n')
        return
    elif sys.stdin is not None and sys.stdin.isatty():
        keep = ask_yes_no('Include these rows in the sheet? [y/n]: ')
        if keep is None:
            keep = True
    else:
        print('  no terminal to ask - keeping them (use --in-transit n to leave them out)')
        keep = True

    if keep:
        print('  -> kept\n')
        return
    for name, rows in data.items():
        drop = {id(r) for r in young.get(name, [])}
        data[name] = [r for r in rows if id(r) not in drop]
        PARKED[name] = young.get(name, [])
    print('  -> left out of the sheet, kept on the hidden %s sheet (they come back on a'
          ' later run once they are older, delivered or received)\n' % PARKED_SHEET)


# --------------------------------------------------------------------------- main
def report_time(started):
    """Where the run went, and how much of it came out of the cache."""
    print('\ntime spent')
    for label, secs in TIMINGS:
        print('  %-24s %6.1fs' % (label, secs))
    print('  %-24s %6.1fs' % ('TOTAL', time.time() - started))
    print('  input files: %d read from cache, %d parsed%s'
          % (CACHE_STATS['hit'], CACHE_STATS['miss'],
             '' if USE_CACHE else '  (--no-cache)'))


def main():
    ap = argparse.ArgumentParser(description='MYNTRA SPF MASTER builder')
    ap.add_argument('--dry-run', action='store_true',
                    help='report only, do not write the workbook')
    ap.add_argument('--in-transit', choices=['y', 'n'],
                    help='answer the young in-transit question without being asked: '
                         'y keeps those rows, n leaves them out')
    ap.add_argument('--no-cache', action='store_true',
                    help='re-parse every input file instead of reusing the cache')
    args = ap.parse_args()
    global USE_CACHE
    USE_CACHE = not args.no_cache
    started = time.time()

    print('MYNTRA SPF MASTER  -  %s' % dt.date.today())
    print('input : %s' % INPUT_DIR)
    print('output: %s\n' % MASTER)

    print('INPUT FILES%s' % ('  (dry run - nothing is moved)' if args.dry_run else ''))
    with phase('organise input folder'):
        organize_inputs(print, args.dry_run)

    with phase('previous master'):
        base = read_old_master()
    carried = sum(len(v) for v in base.values())
    if carried:
        print('previous master found - %d rows carried as the base '
              '(new data updates them in place, no duplicates)\n' % carried)

    print('MANUAL RETURN')
    with phase('manual sheet'):
        man_ids, man_awbs = read_manual_marks(print)
    with phase('payment reports'):
        (pay_oli, pay_soi, chg_rid, chg_oli, settled,
         deb_rid, deb_oli) = read_payments(print)
    with phase('order reports'):
        order_amt, store_ids = read_orders(print)
    with phase('GRN report'):
        grn_awbs, grn_orders = read_grn(print)
    with phase('dispatch report'):
        dispatched = read_dispatch(print)
    with phase('POD report'):
        pod, have_pod = read_pod(print)
    if grn_awbs:
        print('  %d tracking ids booked in by the ERP' % len(grn_awbs))
    if pay_oli:
        print('  %d order lines have an SPF payout' % len(pay_oli))
        print('')
    with phase('legacy master'):
        legacy = read_legacy_notes(print)
    if legacy:
        print('  %d rows of OMSGURU / MANUAL notes available from an older master\n'
              % len(legacy))

    data = {}
    totals = {'TRANSIT': 0, 'DELIVERED': 0, 'dropped_delivered': 0, 'dupes': 0,
              'new': 0, 'model_lookup': 0, 'model_wh': 0, 'model_fbm': 0, 'model_blank': 0,
              'manual_hit': 0, 'legacy_hit': 0, 'payment_hit': 0, 'charge_hit': 0,
              'order_hit': 0, 'too_old': 0, 'settled_hit': 0, 'grn_hit': 0,
              'debit_hit': 0, 'dispatch_hit': 0,
              'pod_hit': {c: 0 for c in POD_TYPES}}
    for name in SHEETS:
        print('%s' % name)
        with phase('read %s' % name):
            merged, tickets, only_transit, wh_model, st = collect(name, print)
        for skipped in st['skipped']:
            print('  %-58s SKIP (report type not recognised)' % skipped[:58])

        # previous master rows are the base, the new reports merge on top
        out = base[name]
        for k, rec in merged.items():
            if k in out:
                st['dupes'] += 1
                merge_into(out[k], rec)
            else:
                out[k] = rec
                totals['new'] += 1

        # warehouse_id -> model (PPMP / SJIT / FBM). In the data seen so far a
        # warehouse only ever has one model, so this fills rows that exist only in the
        # Return Delivery Report, which does not carry the column at all. If a warehouse
        # ever turns out to serve two models the fallback is skipped for it rather than
        # guessing - better a blank cell than a wrong model.
        for rec in out.values():
            wh = norm(rec.get('warehouse_id'))
            m = rec.get('model')
            if m and wh:
                wh_model.setdefault(wh, set()).add(str(m))
        # our own warehouses: the ones whose orders are PPMP
        own_wh = {wh for wh, models in wh_model.items() if 'PPMP' in models}

        # how many master rows share one order line - the return charge fallback is
        # only safe where the answer is exactly one (see read_payments)
        per_oli = {}
        for rec in out.values():
            oli = norm(rec.get('order_line_id'))
            per_oli[oli] = per_oli.get(oli, 0) + 1

        rows = []
        for k, rec in out.items():
            # fill the columns the Return Delivery Report does not have
            look = only_transit.get(k)
            if look:
                for c, v in look.items():
                    if rec.get(c) is None:
                        rec[c] = v
                        if c == 'model':
                            totals['model_lookup'] += 1
            if rec.get('model') is None:
                seen = wh_model.get(norm(rec.get('warehouse_id')))
                if seen and len(seen) == 1:
                    rec['model'] = next(iter(seen))
                    totals['model_wh'] += 1
                elif rec.get('warehouse_id') is not None and norm(rec.get('warehouse_id')) not in own_wh:
                    # not one of our own (PPMP) warehouses: a Myntra warehouse order, worked
                    # like the other FBM orders - i.e. not at all
                    rec['model'] = 'FBM'
                    totals['model_fbm'] += 1
                else:
                    totals['model_blank'] += 1

            # carry forward what you filled into an older master, empty cells only
            old_notes = legacy.get(k)
            if old_notes:
                for c, v in old_notes.items():
                    if rec.get(c) is None:
                        rec[c] = v
                        totals['legacy_hit'] += 1

            # a hit in the manual return sheet means the parcel physically reached you.
            # Only ever written into an empty cell - whatever you typed there stays.
            if (rec.get('MANUAL RETURN STATUS') is None
                    and manual_received(rec, man_ids, man_awbs)):
                rec['MANUAL RETURN STATUS'] = 'Return Received'
                totals['manual_hit'] += 1

            # The ticket report refreshes its own three columns, but only for rows it
            # actually has a ticket for. A ticket id you typed yourself for a row the
            # report has not caught up with is left alone.
            t = tickets.get(norm(rec.get('order_line_id')))
            if t and t[1] is not None:
                same = str(rec.get('TICKET ID')) == str(t[1])
                rec['TICKET ID'], rec['STATUS'] = t[1], t[2]
                rec['_ticket_created'] = t[0]          # for the CLAIM TAT sheet
                tat = claim_tat(rec)
                if tat:
                    rec['CLAIM TAT'] = tat + ' TAT'
            # a ticket status with no ticket id (left by an old hand report) is no claim
            if rec.get('TICKET ID') in (None, '') and rec.get('STATUS') is not None:
                rec['STATUS'] = None
                if t[3] is not None or not same:      # bot only: keep a known reason
                    rec['REJECTION REASON'] = t[3]

            # Same idea for the payout: refreshed whenever the payment report has one
            # for this order line, left as it was when it does not. seller_order_id is
            # only a fallback - a handful of paid lines are not in the returns reports
            # under their order_line_id.
            pay = (pay_oli.get(norm(rec.get('order_line_id')))
                   or pay_soi.get(norm(rec.get('seller_order_id'))))
            if pay:
                rec['SPF PAID'], rec['UTR NO'] = pay
                totals['payment_hit'] += 1

            # What the return itself cost you. return_id is the exact key; the
            # order_line_id fallback is used only when this order line has one return
            # in the master and one Reverse line in the payment report, so a charge is
            # never pinned on the wrong return.
            oli = norm(rec.get('order_line_id'))
            rid = norm(rec.get('return_id'))
            fee = chg_rid.get(rid) if rid else None
            if fee is None and per_oli.get(oli) == 1:
                fee = chg_oli.get(oli)
            if fee is not None:
                rec['RETURN CHARGE'] = fee
                totals['charge_hit'] += 1

            deb = deb_rid.get(rid) if rid else None
            if deb is None and per_oli.get(oli) == 1:
                deb = deb_oli.get(oli)
            if deb is not None:
                rec['RETURN DEBIT'] = deb
                totals['debit_hit'] += 1

            amt = order_amt.get(oli)
            if amt is not None:
                rec['ORDER AMOUNT'] = amt
                totals['order_hit'] += 1
            sid = store_ids.get(oli)
            if sid is not None:
                rec['STORE ORDER ID'] = sid

            # The ERP booked this parcel in, so it is physically back. This is the one
            # thing that overwrites OMSGURU STATUS: a GRN outranks whatever the status
            # was mid-flight ("Return Init", "Ready to ship", ...).
            # Dispatch is refreshed every run from the ERP export; a row it does not
            # list keeps whatever it had.
            for c in DISPATCH_MATCH_COLS:
                d = dispatched.get(norm(rec.get(c))) if rec.get(c) else None
                if d:
                    rec['DISPATCH STATUS'] = DISPATCH_STATUS
                    rec['DISPATCH DATE'], rec['DISPATCH VOUCHER'] = d
                    totals['dispatch_hit'] += 1
                    break

            # Matched on tracking, or on the store order id - the latter marks every
            # item of that order, since the GRN's item number is internal to the ERP.
            if (any(norm(rec.get(c)) in grn_awbs and norm(rec.get(c))
                    for c in MASTER_AWB_COLS)
                    or (rec.get('STORE ORDER ID') and
                        str(rec.get('STORE ORDER ID')) in grn_orders)):
                if rec.get('OMSGURU STATUS') != GRN_STATUS:
                    totals['grn_hit'] += 1
                rec['OMSGURU STATUS'] = GRN_STATUS

            # Refreshed every run from the POD sheet; without one the last value stays.
            if have_pod:
                types = pod_types(rec, pod)
                for c in POD_TYPES:
                    rec[c] = POD_YES if c in types else POD_NO
                    if c in types:
                        totals['pod_hit'][c] += 1
                rec[RETURN_POD_DATE] = types.get('RETURN POD')
            # Bot only: what OMSGuru says about a return the ERP has not booked in (checked
            # after the ERP, before the manual sheet). A received status is never replaced.
            oms = OMS_RESULTS.get(row_key(rec))
            if oms and 'RECEIVED' not in str(rec.get('OMSGURU STATUS') or '').upper():
                rec['OMSGURU STATUS'] = oms
                totals['oms_hit'] = totals.get('oms_hit', 0) + 1

            paid_out = settled.get(oli)
            if paid_out is not None:
                rec['ORDER SETTLEMENT'] = paid_out
                totals['settled_hit'] += 1
            rows.append(rec)

        # oldest return-create date first, so the months always run in order
        rows.sort(key=lambda r: r.get('return_created_date') or dt.datetime.min)
        data[name] = rows

        for k in ('TRANSIT', 'DELIVERED', 'dropped_delivered', 'dupes', 'too_old'):
            totals[k] += st[k]
        print('  ------------------------------------------------------------------')
        print('  in-transit rows          : %6d' % st['TRANSIT'])
        print('  delivered rows           : %6d' % st['DELIVERED'])
        print('  delivered rows dropped   : %6d  (from the in-transit reports, kept '
              'only as a lookup)' % st['dropped_delivered'])
        print('  duplicates merged        : %6d' % st['dupes'])
        print('  tickets found            : %6d' % len(tickets))
        print('  FINAL unique rows        : %6d\n' % len(rows))

    drop_young_in_transit(data, args)

    print('==================================================================')
    print('TOTAL unique rows: %d  (MAIN %d / DBCC %d / FMCC %d)'
          % (sum(len(v) for v in data.values()),
             len(data['MAIN']), len(data['DBCC']), len(data['FMCC'])))
    print('new rows added        : %d' % totals['new'])
    print('duplicate keys merged : %d  (no extra row created)' % totals['dupes'])
    print('model filled - %d from lookup, %d from warehouse, %d FBM (not our warehouse), %d still blank'
          % (totals['model_lookup'], totals['model_wh'], totals['model_fbm'], totals['model_blank']))
    print('marked "Return Received" from the manual sheet: %d rows'
          % totals['manual_hit'])
    print('OMSGURU / MANUAL cells restored from an older master: %d'
          % totals['legacy_hit'])
    print('rows with an SPF payout (SPF PAID + UTR): %d of %d payouts matched'
          % (totals['payment_hit'], len(pay_oli)))
    print('rows with a return charge: %d' % totals['charge_hit'])
    print('rows with an order amount: %d' % totals['order_hit'])
    print('rows with an order settlement: %d' % totals['settled_hit'])
    print('rows with a return debit: %d' % totals['debit_hit'])
    print('rows marked dispatched from the dispatch sheet: %d' % totals['dispatch_hit'])
    for c in POD_TYPES:
        print('rows in %s: %d' % (c, totals['pod_hit'][c]))
    print('OMSGURU STATUS set to "%s" from the GRN report: %d rows'
          % (GRN_STATUS, totals['grn_hit']))
    print('rows skipped as older than %s: %d'
          % (EARLIEST_RETURN.date(), totals['too_old']))

    if args.dry_run:
        print('\nINPUT CLEANUP  (dry run - nothing is deleted)')
        cleanup_inputs(data, print, True)
        report_time(started)
        print('\n--dry-run: nothing was written.')
        return

    with phase('write workbook'):
        backup()
        write_master(data)
    print('\nwritten: %s' % MASTER)
    print('\nINPUT CLEANUP')
    with phase('input cleanup'):
        cleanup_inputs(data, print)
    report_time(started)


if __name__ == '__main__':
    main()
