"""Build the single AJIO master workbook used for analysis.

Inputs
    AJIO MASTER.xlsx              - AJIO returns report (one row per return line)
    AJIO RETURN SHEET OMS GURU.xlsx - OMS Guru return log (what the warehouse
                                      actually booked in)
    AJIO RETURN SHEET MANUAL.xlsx - warehouse's physical "received" register

The OMS Guru and manual files are read from the shared drive when it is
reachable, otherwise from the copies sitting next to this script - and, on
top of that, every copy dropped into input/Manual Sheet/ (the file name says
which sheet it is: "OMS GURU" or "MANUAL").

Output
    AJIO MASTER.xlsx              - the same workbook, rewritten in place:
                                    Sheet1, one row per activity, and
                                    SUMMARY, live formulas per account/month

Rules
    * One activity = one invoice (FWD B2B INVOICE No) + SKU. If it appears
      more than once, only the latest return survives; two SKUs on one
      invoice are two activities and both stay.
    * OMS Guru and the manual register are lookups only: they say whether a
      return came back. Their rows are never added to the master.

Input folder
    Drop new downloads anywhere in input/. Each run first files them: the
    returns reports (recognised by their headers) go to input/RTV Report/,
    named after the days they cover - "2026-09-01 to 2026-09-20.xlsx" - and
    tickets, payment, GRN and dispatch sit in input/ itself. An exact copy of
    a file already there is deleted.

    Once the master is saved, every returns report read is deleted: its rows
    are in the master now. The INPUT LOG sheet records each report read
    (account, first and last return, rows, file), and the SUMMARY sheet shows
    the account-wise date range of all of them, with any missing days.
    Tickets, payment, GRN and dispatch are never deleted - replace them with
    the newer download.
"""

import calendar
import filecmp
import re
import shutil
from collections import Counter
from copy import copy
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


SHARED = Path(r"Z:\Serein\SereinData\serein\return sheet_W\OMSGURU RETURN SHEETS_08-12-2023")

SOURCE_MASTER = Path("AJIO MASTER.xlsx")
# Shared drive first, local copy as the offline fallback.
SOURCE_OMS = (SHARED / "AJIO RETURN SHEET OMS GURU.xlsx", Path("AJIO RETURN SHEET OMS GURU.xlsx"))
SOURCE_MANUAL = (SHARED / "AJIO RETURN SHEET MANUAL.xlsx", Path("AJIO RETURN SHEET MANUAL.xlsx"))
# Copies of both sheets dropped into input/Manual Sheet/ are read as well, every
# one of them, on top of whichever of the above is reachable. The file name says
# which sheet it is.
MANUAL_SHEET_DIR = Path("input") / "Manual Sheet"
OMS_FILENAME_HINT = "OMS GURU"
MANUAL_FILENAME_HINT = "MANUAL"

BACKUP_DIR = Path("tmp/spreadsheets/backup")

# Fresh AJIO report exports are dropped in input/ and deleted once the master
# holds them (see cleanup_inputs). ingest_input() is still idempotent: a line
# the master already holds is never added again, and a report older than what
# the master knows never pulls a status backwards.
INPUT_DIR = Path("input")

# Stamped on every row that arrived through input/. Those returns are too new
# for OMS Guru or the register to know about, and the warehouse fills their
# status in by hand, so the lookups below must leave these columns alone.
IMPORTED_HEADER = "IMPORTED ON"

# Typed in by the warehouse and absent from the report, so a row that loses
# the AWB collapse hands these to the survivor instead of taking them with
# it. "#N/A" counts as empty - it is what a stale lookup left behind.
HAND_KEPT_HEADERS = (
    "TICKET ID",
    "STATUS",
    "SUBISSUETYPE",
    "SELLER LAST COMMENT",
    "SELLER COMMENT DATE",
    "SUPPORT LAST REPLY",
    "SUPPORT REPLY BY",
    "SUPPORT REPLY DATE",
    "G4 PAYMENT",
    "IC REVERSE",
)
HAND_KEPT_EMPTY = ("", "#N/A")

# The report ships every field as text ("-580.0"), which lands in Excel as
# text and will not add up, so these columns are converted back to numbers.
#
# EAN is here by explicit request, and it costs something: a spreadsheet
# number is a double, exact only to 2**53, and an 18-digit EAN is past that,
# so its last two or three digits are lost on the way in. That is not an
# Excel quirk to work around - the xlsx file itself stores the rounded
# figure. Most rows were already stored this way; this makes the column
# consistent rather than half exact.
#
# The AWB columns are here too, but only their numeric entries can convert:
# a value like "R1811453747AJI" has letters in it and stays text.
NUMERIC_HEADERS = (
    # measures
    "Return QTY",
    "Return Value",
    "Credit Note Value",
    "Credit Note Pre Tax Value",
    "Credit Note Tax Value",
    "CGST AMOUNT",
    "CGST PERCENTAGE",
    "IGST AMOUNT",
    "IGST PERCENTAGE",
    "SGST AMOUNT",
    "SGST PERCENTAGE",
    "FWD B2B Invoice Amt",
    "MRP",
    # identifiers short enough to survive being a double
    "JioCode",
    "HSN",
    "Return Shipment ID",
    "Credit Note Number",
    "FWD Seller Order ID",
    "FWD PO No",
    # mostly numeric; the lettered entries stay text
    "Delivery Challan No",
    "Ret Doc No",
    "Return AWB No",
    "FWD AWB",
    # past 2**53, so it converts at the cost of its last digits
    "EAN",
)
BLANK_ON_IMPORT = ("OMSGURU STATUS", "OMS RECEIVED DATE", "MANUAL RETURN STATUS")

SHEET = "Sheet1"
SUMMARY_SHEET = "SUMMARY"
# The two AJIO seller accounts are told apart by POB ID (DC CODE follows it).
SUMMARY_ACCOUNT_HEADER = "POB ID"
# What a return is worth to us - the invoice we raised, not the customer MRP.
SUMMARY_AMOUNT_HEADER = "FWD B2B Invoice Amt"
NO_TICKET = ("", "#N/A")
# Look copied from "summary example.xlsx": yellow header and grand total,
# light blue account totals, whole counts, two-decimal amounts.
SUMMARY_HEADER_FILL = "FFFFFF00"
SUMMARY_TOTAL_FILL = "FFD9E1F2"
COUNT_FORMAT = "0"
AMOUNT_FORMAT = "#,##0.00"
DATE_FORMAT = "dd-mm-yyyy"
DATETIME_FORMAT = "dd-mm-yyyy hh:mm"
SOURCE_FILL = "FFC8F7C8"
DERIVED_FILL = "FFFFFF00"

DERIVED_HEADERS = (
    "AGE (DAYS)",
    "CLAIM STATUS",
    "CLAIM TYPE",
    "OMSGURU STATUS",
    "OMS RECEIVED DATE",
    "MANUAL RETURN STATUS",
    "FINAL RETURN STATUS",
)

# One invoice + SKU is one activity; the latest return for it is the one kept.
INVOICE_HEADER = "FWD B2B INVOICE No"
RETURN_ORDER_HEADER = "RETURN ORDER NUMBER"
# Forward-shipment fields the monthly exports leave blank. They are the same
# for every return against one invoice, so a blank never replaces a value.
EXPORT_GAP_HEADERS = ("FWD AWB", "FWD Carrier Name")

# Fallback when a row has no invoice. Product-level SKU/JioCode/EAN are
# deliberately excluded because they can identify multiple distinct returns.
IDENTIFIER_HEADERS = (
    "RETURN ORDER NUMBER",
    "Return Shipment ID",
    "Return AWB No",
    "Delivery Challan No",
    "Ret Doc No",
    "Credit Note Number",
    "FWD Seller Order ID",
    "Cust Order No",
    "FWD PO No",
    "FWD B2B INVOICE No",
    "FWD AWB",
)

# The return AWB is shipment level, so it pins down the exact parcel. The other
# identifiers are order/invoice level and can cover several parcels, which is
# why they are only tried when the AWB finds nothing.
OMS_STRONG_LINKS = (("Return AWB No", "Return Tracker"),)
OMS_WEAK_LINKS = (
    ("Return AWB No", "FWD Tracker"),
    ("FWD AWB", "FWD Tracker"),
    ("FWD B2B INVOICE No", "Invoice Number"),
    ("FWD Seller Order ID", "Order Number"),
)
OMS_RECEIVED_STATUSES = {
    "RETURN RECEIVED",
    "CANCELLED RETURN RECEIVED",
    "PARTIAL RETURN RECEIVED",
    "PARTIAL CANCELLED RETURN RECEIVED",
}

# Later lifecycle stage wins when the same activity is repeated.
RETURN_STATUS_RANK = {
    "": 0,
    "RETURN_CREATED": 1,
    "RETURN_DELIVERED": 2,
    "RETURN_PROCESSED": 3,
    "CREDIT_NOTE_CREATED": 4,
}
OMS_STATUS_RANK = {
    "": 0,
    "#N/A": 0,
    "CANCEL INIT": 1,
    "RETURN INIT": 2,
    "IN TRANSIT": 3,
    "DELIVERED": 4,
    "PARTIAL RETURN RECEIVED": 5,
    "PARTIAL CANCELLED RETURN RECEIVED": 5,
    "CANCELLED RETURN RECEIVED": 6,
    "RETURN RECEIVED": 7,
}
PROGRESS_DATE_HEADERS = (
    "Credit Note Acceptance date",
    "Credit Note Generation Date",
    "QC completion date",
    "Delivery Challan Date",
    "Return Delivered Date",
    "Return Created Date",
)

RECEIVED_WORDS = {"RECD", "REDC", "RED", "RECEIVED", "RETURNRECEIVED"}
RECEIVED = "Return Received"
# AJIO paid the invoice (G4) and never took it back with a credit note (IC),
# so the return costs us nothing and there is nothing left to claim.
PAYMENT_DONE = "Payment Done"

# The warehouse's own goods-receipt register, dropped into input/ alongside
# the AJIO report. It covers every portal, so only the lines whose tracking
# id matches one of our AWBs are ours. A hit is the ERP saying the parcel is
# physically on the shelf, which is firmer than anything OMS Guru reports,
# so it overrides the status - under its own wording, to stay traceable.
GRN_FILENAME_HINT = "GRN"
GRN_RECEIVED = "Return Received ERP"
# A GRN line counts when either its order id or its tracking id matches one
# of our rows. Once an order is confirmed that way, every item of that order
# is marked received, because a multi-item order comes back in one parcel
# and the ERP often books only some of its articles.
GRN_TRACKING_HEADERS = ("FWD AWB", "Return AWB No")
GRN_ORDER_HEADER = "Cust Order No"

# The GRN wraps the order id as "NAN-FN0172809725-1". Older exports carry it
# in "Portal", newer ones in "Order Item Id".
GRN_ORDER_PREFIX = "NAN-"
GRN_ORDER_COLUMNS = ("Order Item Id", "Portal")

# The AJIO seller-portal ticket export, dropped into input/ as a workbook with
# "TICKET" in its name. Its columns get reordered between exports, so every
# field is found by header name (case, spaces and underscores ignored).
TICKET_FILENAME_HINT = "TICKET"
TICKET_SHEET = "Tickets"
# (master column, ticket export field)
TICKET_FIELDS = (
    ("TICKET ID", "ticketId"),
    ("STATUS", "status"),
    ("SUBISSUETYPE", "subIssueType"),
)
# The conversation split by side: our own latest comment and seller support's
# latest reply get separate columns, so it is plain who is waiting on whom.
SELLER_COMMENT_HEADERS = ("SELLER LAST COMMENT", "SELLER COMMENT DATE")
SUPPORT_REPLY_HEADERS = ("SUPPORT LAST REPLY", "SUPPORT REPLY BY", "SUPPORT REPLY DATE")
COMMENT_HEADERS = SELLER_COMMENT_HEADERS + SUPPORT_REPLY_HEADERS
TICKET_HEADERS = tuple(master for master, _ in TICKET_FIELDS) + COMMENT_HEADERS
# Superseded by the split columns above; dropped from Sheet1 on rewrite.
RETIRED_HEADERS = ("lastCommentText", "lastCommentBy")
# The export's "Comments" sheet holds every comment of every ticket, one row
# each, with the side that wrote it.
COMMENTS_SHEET = "Comments"
COMMENT_TICKET_FIELD = "ticketId"
COMMENT_SIDE_FIELD = "side"
COMMENT_AUTHOR_FIELD = "authorName"
COMMENT_TIME_FIELD = "createdAt"
COMMENT_TEXT_FIELD = "content"
COMMENT_SEQ_FIELD = "seq"
SELLER_SIDE = "SELLER"
# Without a Comments sheet only the single last comment is known; its type
# (SELLER or AGENT) says which column it belongs in.
LAST_COMMENT_FIELDS = ("lastCommentType", "lastCommentBy", "lastCommentAt", "lastCommentText")
# A ticket belongs to the master row with the same invoice + SKU; when one
# line has several tickets, the most recently created one is shown.
TICKET_INVOICE_FIELD = "forwardInvoiceNumber"
TICKET_SKU_FIELD = "skuId"
TICKET_CREATED_FIELD = "createdDate"

# Our own OMS dispatch register, dropped into input/ as a workbook with
# "DISPATCH" in its name: one line per article that left the warehouse. It
# covers every portal, so only its AJIO lines are ours.
#
# Its "Order ID" is the forward tracking number, which is our FWD AWB. That
# is the only id the two sides write the same way: the register's own order
# id is the OMS one, nothing like the report's, and its SKU has no brand
# prefix. The return AWB is deliberately not tried as a second key, because
# Delhivery hands out forward and return numbers from one series, so a
# return AWB can land on some other parcel's dispatch line.
DISPATCH_FILENAME_HINT = "DISPATCH"
DISPATCH_PORTAL = "AJIO"
DISPATCH_PORTAL_FIELD = "Order Portal Name"
DISPATCH_TRACKING_FIELD = "Order ID"
DISPATCH_SKU_FIELD = "SKU Code"
DISPATCH_DATE_FIELD = "Voucher Date"
DISPATCH_VOUCHER_FIELD = "Voucher No."
DISPATCH_TRACKING_HEADER = "FWD AWB"
DISPATCH_DATE_HEADER = "DISPATCH DATE"
DISPATCH_VOUCHER_HEADER = "DISPATCH VOUCHER NO"
DISPATCH_HEADERS = (DISPATCH_DATE_HEADER, DISPATCH_VOUCHER_HEADER)

# The AJIO vendor ledger export, dropped into input/ as a workbook with
# "PAYMENT" in its name: one row per accounting document. Fields are found by
# header name, so the export may reorder its columns.
PAYMENT_FILENAME_HINT = "PAYMENT"
PAYMENT_TYPE_FIELD = "DT"
PAYMENT_AMOUNT_FIELD = "AMOUNT"
PAYMENT_DOC_FIELD = "DOCNO"
# (master column, document type, ledger field, master field it matches,
#  master fields that weigh each row's share).
# G4 is the dropship invoice payment, keyed by our invoice number; IC is the
# credit note for the return, keyed by the delivery challan in PONO. One
# invoice or challan can span several SKU rows, so its amount is shared out
# by each row's own value (the first of the weight fields that has one).
PAYMENT_LINKS = (
    ("G4 PAYMENT", "G4", "REFERENCE", "FWD B2B INVOICE No", ("FWD B2B Invoice Amt",)),
    (
        "IC REVERSE",
        "IC",
        "PONO",
        "Delivery Challan No",
        ("Credit Note Value", "FWD B2B Invoice Amt"),
    ),
)
# The ledger books a G4 payment to us as a negative amount; the master shows
# it positive, as money received.
PAYMENT_SIGN_FLIPPED = ("G4 PAYMENT",)

# Columns Sheet1 keeps even if they were deleted in Excel, because the
# formulas or the SUMMARY sheet read them.
REQUIRED_HEADERS = (
    "RETURN ORDER NUMBER",
    "Return Created Date",
    "3PL Delivery Status",
    "Delivery Challan Date",
    "Credit Note Value",
    "FWD B2B Invoice Amt",
    "POB ID",
    "AGE (DAYS)",
    "CLAIM STATUS",
    "CLAIM TYPE",
    "OMSGURU STATUS",
    "MANUAL RETURN STATUS",
    "FINAL RETURN STATUS",
    "G4 PAYMENT",
    "IC REVERSE",
) + tuple(master for master, _ in TICKET_FIELDS)

DATE_PATTERNS = (
    "%a %b %d %H:%M:%S IST %Y",
    "%B %d %Y %I:%M:%S %p",
    "%b %d %Y %I:%M:%S %p",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
)


def text(value):
    """Comparable text form of a cell value."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip().upper()


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    for pattern in DATE_PATTERNS:
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            pass
    return None


def same_sku(report_sku, oms_sku):
    """Do a report SKU and an OMS Guru SKU describe the same article?

    The report sells it as LEA-/LA-/FSH-/MUR-COORD-CJ-1-N15-M while OMS Guru
    stores the same article unprefixed, so the brand prefix has to come off.
    """
    if not report_sku or not oms_sku:
        return False
    return report_sku == oms_sku or report_sku.endswith("-" + oms_sku)


def is_received(value):
    # Covers the manual file's RECD/REDC/RED spelling variants, while
    # excluding its "1-NOT RECD" row.
    return text(value).replace(" ", "") in RECEIVED_WORDS


def first_existing(candidates):
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:  # shared drive not mounted
            continue
    return None


def lookup_sources(candidates, hint, skip_hint=None):
    """The shared/local sheet (if reachable) plus every copy in input/Manual Sheet/."""
    paths = []
    found = first_existing(candidates)
    if found is not None:
        paths.append(found)
    if MANUAL_SHEET_DIR.exists():
        for path in sorted(MANUAL_SHEET_DIR.glob("*.xlsx")):
            name = path.name.upper()
            if path.name.startswith("~$") or hint not in name:
                continue
            if skip_hint and skip_hint in name:
                continue
            paths.append(path)
    return paths


def as_number(value):
    """The value as a float, or None when it is not a number at all."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def same_value(new, old, is_date=False):
    """Does the report still say what the master already holds?

    The report ships everything as text ("-580.0"), while the master holds
    real numbers, so the two have to meet as numbers. Long identifiers such as
    EAN are the awkward case: once stored numerically they have already lost
    their tail digits, so a hair of relative slack forgives that damage
    without hiding a genuinely different code.
    """
    if is_date:
        new_date, old_date = parse_date(new), parse_date(old)
        if new_date is not None or old_date is not None:
            return new_date == old_date
    new_number, old_number = as_number(new), as_number(old)
    if new_number is not None and old_number is not None:
        return abs(new_number - old_number) <= 1e-15 * max(
            abs(new_number), abs(old_number)
        )
    # Exports disagree on spacing ("BINDAL EXPORTS  LIMITED" against
    # "BINDAL EXPORTSLIMITED"), which is not a change worth recording.
    return text(new).replace(" ", "") == text(old).replace(" ", "")


def normalize_numbers(headers, rows):
    """Rewrite the measure columns as real numbers, in place.

    Runs over the whole master, not just today's import, so rows that an
    earlier run left as text are put right too.
    """
    columns = [
        position
        for position, header in enumerate(headers)
        if header in NUMERIC_HEADERS
    ]
    fixed = 0
    for row in rows:
        for column in columns:
            if not isinstance(row[column], str):
                continue
            number = as_number(row[column])
            if number is None:
                continue
            row[column] = int(number) if number.is_integer() else number
            fixed += 1
    return fixed


def make_activity_key(position_of):
    """Identity of one return line: forward invoice number + SKU.

    RETURN ORDER NUMBER is not unique enough - a cancelled pickup and the
    return that followed it carry different numbers for the same article - so
    the invoice decides. Two SKUs on one invoice stay two lines. Only a row
    with no invoice falls back to the first identifier it has.
    """

    def activity_key(row):
        column = position_of.get(INVOICE_HEADER)
        if column is not None and column < len(row) and text(row[column]):
            return text(row[column]), text(row[position_of["SELLER SKU"]])
        for header in IDENTIFIER_HEADERS:
            column = position_of.get(header)
            if column is None or column >= len(row):
                continue
            value = text(row[column])
            if value:
                return value, text(row[position_of["SELLER SKU"]])
        return "", text(row[position_of["SELLER SKU"]])

    return activity_key


def computed_formulas(column_of, row):
    """The four Excel-computed columns for one sheet row.

    Shared by the master and the output sheet so the two never drift apart.
    """

    def letter(header):
        return get_column_letter(column_of[header] + 1)

    age = letter("AGE (DAYS)")
    carrier_status = letter("3PL Delivery Status")
    # A return in transit ages from the day it was created. Once it reaches
    # us, it ages from the Delivery Challan Date - the day AJIO booked the
    # hand-over - not the carrier's Return Delivered Date.
    created = letter("Return Created Date")
    delivered = letter("Delivery Challan Date")
    oms = letter("OMSGURU STATUS")
    manual = letter("MANUAL RETURN STATUS")
    final = letter("FINAL RETURN STATUS")
    g4 = letter("G4 PAYMENT")
    ic = letter("IC REVERSE")
    # Text ("NA") and errors ("#N/A") in either column count as no amount.
    paid = f"IFERROR(N({g4}{row})<>0,FALSE)"
    reversed_ = f"IFERROR(N({ic}{row})<>0,FALSE)"

    return {
        "AGE (DAYS)": (
            f'=IF({delivered}{row}<>"",MAX(0,TODAY()-{delivered}{row}),'
            f'IF({created}{row}<>"",MAX(0,TODAY()-{created}{row}),""))'
        ),
        "CLAIM STATUS": (
            f'=IF({delivered}{row}<>"",'
            f'IF({age}{row}<=7,"ELIGIBLE","NON ELIGIBLE"),'
            f'IF({created}{row}<>"",'
            f'IF({age}{row}<60,"UPCOMING CLAIM",'
            f'IF({age}{row}<=90,"ELIGIBLE","NON ELIGIBLE")),""))'
        ),
        # What the claim would be about, once the parcel is missing.
        "CLAIM TYPE": (
            f'=IF(OR({final}{row}="{RECEIVED}",{final}{row}="{PAYMENT_DONE}"),"",'
            f'IF(OR({delivered}{row}<>"",{carrier_status}{row}="Return Delivered"),'
            f'"RETURN NOT RECEIVED",'
            f'IF(ISNUMBER(SEARCH("CANCEL",{carrier_status}{row})),'
            f'"RETURN CANCELLED","RETURN IN TRANSIT")))'
        ),
        # Cancelled/partial return received still means the parcel came back.
        # A parcel that never came back is settled once G4 is paid and no IC
        # reversed it.
        "FINAL RETURN STATUS": (
            f'=IF(OR(ISNUMBER(SEARCH("RECEIVED",{oms}{row})),'
            f'{manual}{row}="{RECEIVED}"),"{RECEIVED}",'
            f'IF(AND({paid},NOT({reversed_})),"{PAYMENT_DONE}",""))'
        ),
    }


def waiting_files():
    """Every workbook under input/, subfolders included, except an archive/ and
    the OMS Guru / manual sheets in Manual Sheet/, which are lookups."""
    if not INPUT_DIR.exists():
        return []
    skipped = {"archive", MANUAL_SHEET_DIR.name.casefold()}
    # ~$ files are Excel's lock files for a workbook someone still has open.
    return sorted(
        path
        for path in INPUT_DIR.rglob("*.xlsx")
        if not path.name.startswith("~$")
        and not skipped & {part.casefold() for part in path.relative_to(INPUT_DIR).parts[:-1]}
    )


# The AJIO returns report is told apart by its headers, so a file of any name, and
# a stray export from another portal, are both recognised for what they are.
REPORT_MARKERS = {"RETURN ORDER NUMBER", "Return Created Date", "POB ID"}
_REPORT_HEADERS = {}


def is_report(path):
    key = (path.resolve(), path.stat().st_mtime_ns)
    if key not in _REPORT_HEADERS:
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook[workbook.sheetnames[0]]
            # the portal writes a wrong sheet size, which would cut the row short
            sheet.reset_dimensions()
            first = next(sheet.iter_rows(values_only=True), ())
        finally:
            workbook.close()
        _REPORT_HEADERS[key] = REPORT_MARKERS <= {str(h).strip() for h in first if h}
    return _REPORT_HEADERS[key]


def is_grn(path):
    return GRN_FILENAME_HINT in path.name.upper()


def is_ticket(path):
    return TICKET_FILENAME_HINT in path.name.upper()


def is_payment(path):
    return PAYMENT_FILENAME_HINT in path.name.upper()


def is_dispatch(path):
    return DISPATCH_FILENAME_HINT in path.name.upper()


def input_files():
    """The AJIO report exports - every file in input/ with the report's headers,
    other than GRNs, tickets, payment ledgers and the dispatch register."""
    return [
        path
        for path in waiting_files()
        if not is_grn(path)
        and not is_ticket(path)
        and not is_payment(path)
        and not is_dispatch(path)
        and is_report(path)
    ]


# ------------------------------------------------------------------ input organiser
# Every run starts by tidying input/: the returns reports go to input/RTV Report/,
# named after the days they cover ("2026-09-01 to 2026-09-20.xlsx"), and the other
# exports (tickets, payment, GRN, dispatch) sit in input/ itself. Once the master is
# saved, every returns report read is deleted - the master holds all of its rows -
# and the INPUT LOG sheet keeps the record of what was read.
REPORT_DIR = INPUT_DIR / "RTV Report"
PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})(?:\s*\(\d+\))?\.xlsx$", re.I)
# The portal names its monthly export AJIO_DropShip_Rtv_Report_2026-09.xlsx
PORTAL_MONTH_RE = re.compile(r"_(\d{4})-(\d{2})\.xlsx$", re.I)
INPUT_LOG_SHEET = "INPUT LOG"
INPUT_LOG_HEADERS = (
    "ACCOUNT", "FROM", "TO", "ROWS", "FILE FROM", "FILE TO", "FILE", "PROCESSED ON",
)
# per report read this run: {path: {account: [first created, last created, rows]}}
FILE_STATS = {}


def created_dates(path):
    """{account: [first, last, rows]} of one report, by Return Created Date."""
    workbook = load_workbook(path, data_only=True)
    try:
        stream = workbook[workbook.sheetnames[0]].iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else "" for h in next(stream, ())]
        created = headers.index("Return Created Date")
        account = headers.index(SUMMARY_ACCOUNT_HEADER)
        found = {}
        for values in stream:
            if created >= len(values):
                continue
            when = parse_date(values[created])
            if when is None:
                continue
            entry = found.setdefault(text(values[account]), [when, when, 0])
            entry[0], entry[1], entry[2] = min(entry[0], when), max(entry[1], when), entry[2] + 1
        return found
    finally:
        workbook.close()


def report_period(path):
    """(first day, last day) a report covers, or None.

    Taken from the name when it already carries it. A portal monthly export covers
    its whole month once the month is over; downloaded before the month ended, it
    runs up to its last return."""
    match = PERIOD_RE.search(path.name)
    if match:
        return tuple(datetime.strptime(x, "%Y-%m-%d").date() for x in match.groups())
    found = created_dates(path)
    if not found:
        return None
    first = min(entry[0] for entry in found.values())
    last = max(entry[1] for entry in found.values())
    match = PORTAL_MONTH_RE.search(path.name)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        downloaded = date.fromtimestamp(path.stat().st_mtime)
        return date(year, month, 1), (month_end if downloaded > month_end else last)
    return first, last


def organize_inputs():
    """Move every export to its folder and name the returns reports after their
    period. Two downloads of one period keep both, the second as "(2)"; an exact
    copy of a file already in place is deleted. Moving keeps the modified time,
    which is what decides the newer of two reports."""
    moved = dupes = 0
    for path in waiting_files():
        rel = path.relative_to(INPUT_DIR)
        if is_grn(path) or is_ticket(path) or is_payment(path) or is_dispatch(path):
            home, name = INPUT_DIR, path.name
        elif is_report(path):
            period = report_period(path)
            home = REPORT_DIR
            name = f"{period[0]:%Y-%m-%d} to {period[1]:%Y-%m-%d}.xlsx" if period else path.name
        else:
            print(f"  {rel}  not an AJIO report - left where it is")
            continue
        stem, number = Path(name).stem, 2
        dest, duplicate = home / name, False
        while True:
            if dest.exists() and dest.resolve() == path.resolve():
                dest = None
                break
            if not dest.exists():
                break
            if filecmp.cmp(dest, path, shallow=False):
                duplicate = True
                break
            dest, number = home / f"{stem} ({number}).xlsx", number + 1
        if dest is None:
            continue
        if duplicate:
            dupes += 1
            print(f"  {rel}  exact duplicate of {dest.relative_to(INPUT_DIR)} - deleted")
            path.unlink()
            continue
        moved += 1
        print(f"  {rel}  ->  {dest.relative_to(INPUT_DIR)}")
        home.mkdir(parents=True, exist_ok=True)
        path.rename(dest)
    if moved or dupes:
        print(f"  {moved} file(s) moved / renamed, {dupes} exact duplicate(s) deleted")
    else:
        print("  every file is already in its folder under its period name")


def read_input_log():
    """The INPUT LOG of the current master, as tuples in INPUT_LOG_HEADERS order."""
    if not SOURCE_MASTER.exists():
        return []
    workbook = load_workbook(SOURCE_MASTER, read_only=True, data_only=True)
    try:
        if INPUT_LOG_SHEET not in workbook.sheetnames:
            return []
        stream = workbook[INPUT_LOG_SHEET].iter_rows(values_only=True)
        next(stream, None)
        log = []
        for values in stream:
            values = (list(values) + [None] * len(INPUT_LOG_HEADERS))[: len(INPUT_LOG_HEADERS)]
            for position in (1, 2, 4, 5, 7):
                values[position] = parse_date(values[position])
            if values[0] and values[1] and values[2]:
                log.append(tuple(values))
        return log
    finally:
        workbook.close()


def input_log(files):
    """The previous log plus one line per account of every report read now."""
    seen = {(entry[0], entry[6]): entry for entry in read_input_log()}
    today = date.today()
    for path in files:
        period = report_period(path) or (None, None)
        for account, (first, last, rows) in FILE_STATS.get(path, {}).items():
            seen[(account, path.name)] = (
                account, first, last, rows, period[0], period[1], path.name, today,
            )
    return sorted(seen.values(), key=lambda entry: (entry[0], entry[1], entry[6]))


def gaps(periods):
    """Sorted (from, to) pairs -> the day ranges none of them cover, as text."""
    out = []
    reach = periods[0][1]
    for start, end in periods[1:]:
        if start > reach + timedelta(days=1):
            a, b = reach + timedelta(days=1), start - timedelta(days=1)
            out.append(f"{a:%d-%m-%Y}" if a == b else f"{a:%d-%m-%Y} to {b:%d-%m-%Y}")
        reach = max(reach, end)
    return ", ".join(out)


def input_coverage(log):
    """Per account: (first return, last return, files, missing days). Missing days
    are the ones inside that span which no report's period covers."""
    periods = sorted({(e[4], e[5]) for e in log if e[4] and e[5]})
    cov = []
    for account in sorted({entry[0] for entry in log}):
        mine = [entry for entry in log if entry[0] == account]
        first = min(entry[1] for entry in mine)
        last = max(entry[2] for entry in mine)
        inside = [(max(a, first), min(b, last)) for a, b in periods if b >= first and a <= last]
        missing = gaps(inside) if inside else ""
        cov.append((account, first, last, len({entry[6] for entry in mine}), missing or "NONE"))
    return cov


def write_input_log(workbook, log):
    sheet = workbook.create_sheet(INPUT_LOG_SHEET)
    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor=SUMMARY_HEADER_FILL)
    for position, header in enumerate(INPUT_LOG_HEADERS, start=1):
        cell = sheet.cell(1, position, header)
        cell.font, cell.fill = bold, fill
        sheet.column_dimensions[get_column_letter(position)].width = (
            16, 12, 12, 8, 12, 12, 40, 14)[position - 1]
    sheet.freeze_panes = "A2"
    for row, entry in enumerate(log, start=2):
        for position, value in enumerate(entry, start=1):
            cell = sheet.cell(row, position, value)
            if isinstance(value, date):
                cell.number_format = DATE_FORMAT


def write_coverage(workbook, row, log):
    """Account-wise date range of every report read, below the SUMMARY table."""
    sheet = workbook[SUMMARY_SHEET]
    bold = Font(name="Calibri", size=10, bold=True, color="FF000000")
    yellow = PatternFill("solid", fgColor=SUMMARY_HEADER_FILL)
    cell = sheet.cell(row, 1, "INPUT DATA COVERAGE (every report read - see INPUT LOG)")
    cell.font = bold
    row += 1
    for position, header in enumerate(("ACCOUNT", "FROM", "TO", "FILES", "MISSING DATES"), 1):
        cell = sheet.cell(row, position, header)
        cell.font, cell.fill = bold, yellow
    for account, first, last, files, missing in input_coverage(log):
        row += 1
        for position, value in enumerate((account, first, last, files, missing), 1):
            cell = sheet.cell(row, position, value)
            if isinstance(value, date):
                cell.number_format = DATE_FORMAT


def cleanup_inputs(files):
    """Delete the returns reports read this run - the master now holds their rows."""
    for path in files:
        if path.exists():
            path.unlink()
            print(f"  {path.relative_to(INPUT_DIR)}  used - deleted")
    print(f"  {len(files)} report(s) deleted; tickets, payment, GRN and dispatch stay")


def read_payments():
    """G4 and IC amounts from every payment ledger export in input/.

    Returns ({(master column, key): amount}, files read). A document seen in
    more than one export counts once; different documents for the same key
    (a posting and its reversal) are added together.
    """
    paths = [path for path in waiting_files() if is_payment(path)]
    amounts = {}
    seen = set()
    for path in paths:
        workbook = load_workbook(path, read_only=True, data_only=True)
        records = sheet_records(workbook, workbook.sheetnames[0]) or []
        workbook.close()
        if not records:
            continue
        wanted = [PAYMENT_TYPE_FIELD, PAYMENT_AMOUNT_FIELD, PAYMENT_DOC_FIELD] + [
            field for _, _, field, *_ in PAYMENT_LINKS
        ]
        missing = [field for field in wanted if field_name(field) not in records[0]]
        if missing:
            raise SystemExit(f"{path.name}: payment columns missing: {missing}")

        for record in records:
            doc_type = text(record.get(field_name(PAYMENT_TYPE_FIELD)))
            amount = as_number(record.get(field_name(PAYMENT_AMOUNT_FIELD)))
            if amount is None:
                continue
            for column, wanted_type, field, *_ in PAYMENT_LINKS:
                key = text(record.get(field_name(field)))
                if doc_type != wanted_type or not key:
                    continue
                document = (doc_type, text(record.get(field_name(PAYMENT_DOC_FIELD))), key)
                if document in seen:
                    continue
                seen.add(document)
                signed = -amount if column in PAYMENT_SIGN_FLIPPED else amount
                amounts[(column, key)] = round(amounts.get((column, key), 0) + signed, 2)

    return amounts, paths


def share_out(amount, weights):
    """Split amount across rows in proportion to weights, to the paisa.

    The rounding leftover goes to the last row, so the parts always add up to
    the ledger amount. Rows with no usable weight split the amount evenly.
    """
    if len(weights) == 1:
        return [amount]
    if sum(weights) <= 0:
        weights = [1] * len(weights)
    total = sum(weights)
    parts = [round(amount * weight / total, 2) for weight in weights[:-1]]
    parts.append(round(amount - sum(parts), 2))
    return parts


def field_name(value):
    """A header reduced to letters and digits: "sub_Issue Type" -> "subissuetype"."""
    return "".join(char for char in str(value).casefold() if char.isalnum())


def sheet_records(workbook, name):
    """Rows of the named sheet as {field_name(header): value}, or None if the
    workbook has no such sheet. Blank cells come back as None."""
    sheets = {field_name(sheet): sheet for sheet in workbook.sheetnames}
    if field_name(name) not in sheets:
        return None
    stream = workbook[sheets[field_name(name)]].iter_rows(values_only=True)
    headers = [
        field_name(header) if header not in (None, "") else None
        for header in next(stream, ())
    ]
    records = []
    for values in stream:
        record = {
            header: (None if value in (None, "") else value)
            for header, value in zip(headers, values)
            if header
        }
        if any(value is not None for value in record.values()):
            records.append(record)
    return records


def as_timestamp(value):
    """A portal time such as "2026-09-02T20:05:34.447" as a datetime."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip().replace(" ", "T")[:26])
    except ValueError:
        return None


def split_comment(entry, seller, body, author, when):
    """File one comment under our side or seller support's side."""
    if seller:
        entry.update(zip(SELLER_COMMENT_HEADERS, (body, when)))
    else:
        entry.update(zip(SUPPORT_REPLY_HEADERS, (body, author, when)))


def latest_comments(comments):
    """{ticket id: comment columns} - the newest comment from each side."""
    newest = {}
    for record in comments:
        ticket_id = text(record.get(field_name(COMMENT_TICKET_FIELD)))
        body = record.get(field_name(COMMENT_TEXT_FIELD))
        if not ticket_id or body is None:
            continue
        seller = text(record.get(field_name(COMMENT_SIDE_FIELD))) == SELLER_SIDE
        when = as_timestamp(record.get(field_name(COMMENT_TIME_FIELD)))
        # Comments posted in the same second are told apart by their sequence.
        rank = (when or datetime.min, as_number(record.get(field_name(COMMENT_SEQ_FIELD))) or 0)
        slot = (ticket_id, seller)
        if slot not in newest or rank >= newest[slot][0]:
            author = record.get(field_name(COMMENT_AUTHOR_FIELD))
            newest[slot] = (rank, body, author, when)

    conversation = {}
    for (ticket_id, seller), (_, body, author, when) in newest.items():
        entry = conversation.setdefault(ticket_id, dict.fromkeys(COMMENT_HEADERS))
        split_comment(entry, seller, body, author, when)
    return conversation


def read_tickets():
    """The newest ticket per invoice + SKU, from every ticket export in input/.

    Returns ({(invoice, sku): {master column: value}}, files read).
    """
    paths = [path for path in waiting_files() if is_ticket(path)]
    latest = {}
    for path in paths:
        workbook = load_workbook(path, read_only=True, data_only=True)
        tickets = sheet_records(workbook, TICKET_SHEET)
        if tickets is None:
            tickets = sheet_records(workbook, workbook.sheetnames[0])
        comments = sheet_records(workbook, COMMENTS_SHEET)
        workbook.close()
        if not tickets:
            continue

        wanted = [field for _, field in TICKET_FIELDS] + [
            TICKET_INVOICE_FIELD,
            TICKET_SKU_FIELD,
            TICKET_CREATED_FIELD,
        ]
        if comments is None:
            wanted += LAST_COMMENT_FIELDS
        missing = [field for field in wanted if field_name(field) not in tickets[0]]
        if missing:
            raise SystemExit(f"{path.name}: ticket columns missing: {missing}")
        conversation = latest_comments(comments) if comments is not None else {}

        for record in tickets:
            def get(field):
                return record.get(field_name(field))

            ticket_id = text(get("ticketId"))
            invoice = text(get(TICKET_INVOICE_FIELD))
            sku = text(get(TICKET_SKU_FIELD))
            # General tickets (TDS, advertising) name no return line.
            if not ticket_id or not invoice or not sku:
                continue
            entry = {master: get(field) for master, field in TICKET_FIELDS}
            # Stored as a number, like the ticket ids typed in before.
            entry["TICKET ID"] = int(ticket_id) if ticket_id.isdigit() else ticket_id
            if comments is not None:
                entry.update(conversation.get(ticket_id, dict.fromkeys(COMMENT_HEADERS)))
            else:
                entry.update(dict.fromkeys(COMMENT_HEADERS))
                kind, author, when, body = (get(field) for field in LAST_COMMENT_FIELDS)
                if body is not None:
                    split_comment(
                        entry, text(kind) == SELLER_SIDE, body, author, as_timestamp(when)
                    )
            # ISO timestamps, so text order is time order.
            rank = (str(get(TICKET_CREATED_FIELD) or ""), ticket_id)
            current = latest.get((invoice, sku))
            if current is None or rank >= current[0]:
                latest[(invoice, sku)] = (rank, entry)

    return {key: entry for key, (_, entry) in latest.items()}, paths


def grn_files():
    return [path for path in waiting_files() if is_grn(path)]


def progress_of(row, column_of):
    """How far along a return line is: lifecycle stage, then latest milestone."""
    status = column_of.get("Return Status")
    stage = RETURN_STATUS_RANK.get(text(row[status]), 0) if status is not None else 0
    latest = max(
        (
            row[column_of[header]]
            for header in PROGRESS_DATE_HEADERS
            if header in column_of and isinstance(row[column_of[header]], date)
        ),
        default=date.min,
    )
    return stage, latest


def freshness_of(row, column_of):
    """Which of two lines for one invoice + SKU is the latest: newest return
    first, then the further lifecycle stage, then the latest milestone."""
    created = parse_date(row[column_of["Return Created Date"]]) or date.min
    return (created,) + progress_of(row, column_of)


def parcel_of(row, column_of):
    """(FWD AWB, SKU) and its (created, stage) - what collapse_by_awb ranks on."""
    awb = text(row[column_of["FWD AWB"]]) if "FWD AWB" in column_of else ""
    sku = text(row[column_of["SELLER SKU"]])
    created = parse_date(row[column_of["Return Created Date"]]) or date.min
    return (awb, sku), (created, progress_of(row, column_of)[0])


def ingest_input(headers, rows):
    """Fold every report waiting in input/ into the master rows.

    A report can be read more than once (a re-download of a month already
    read), so this must come out the same each time:

    * Across all files, each activity (invoice + SKU) is read once - its
      latest line.
    * A later return for an activity the master holds replaces that row.
    * An activity the master has never seen is appended, unless the master
      already holds a fresher return for the same parcel and SKU (the AWB
      collapse would drop it again, only for the next run to re-add it).
    * An activity the master knows is refreshed where the report disagrees,
      but never when the report is behind what the master already holds, and
      never from a blank report cell.

    Only added rows are stamped, so their OMS and register columns are left
    for the warehouse to fill in. Refreshed rows keep their received status.

    Returns (added, updated, unchanged, older than master, superseded, files).
    """
    files = input_files()
    if not files:
        return 0, 0, 0, 0, 0, 0, []
    # Oldest export first, so on a tie the newest file's line is the one kept.
    files.sort(key=lambda path: path.stat().st_mtime)

    column_of = {header: position for position, header in enumerate(headers)}
    activity_key = make_activity_key(column_of)
    seen = {}
    parcels = {}
    for row in rows:
        seen.setdefault(activity_key(row), row)
        parcel, freshness = parcel_of(row, column_of)
        if parcel[0]:
            parcels[parcel] = max(parcels.get(parcel, freshness), freshness)

    def stamp_row(row, stamp):
        """Mark the row as report-sourced and clear the hand-kept statuses."""
        row[column_of[IMPORTED_HEADER]] = stamp
        for header in BLANK_ON_IMPORT:
            column = column_of.get(header)
            if column is not None:
                row[column] = None

    # Pass 1: the best line per activity across every file in input/.
    candidates = {}
    for path in files:
        source = load_workbook(path, data_only=True)
        source_sheet = source[source.sheetnames[0]]
        stream = source_sheet.iter_rows(values_only=True)
        source_headers = [header for header in next(stream) if header is not None]
        unknown = [header for header in source_headers if header not in column_of]
        if unknown:
            raise SystemExit(f"{path.name}: columns not in the master: {unknown}")
        targets = [column_of[header] for header in source_headers]
        date_targets = {
            column_of[header]
            for header in source_headers
            if "date" in str(header).casefold()
        }

        for values in stream:
            values = list(values)[: len(source_headers)]
            if all(value in (None, "") for value in values):
                continue
            fresh = [None] * len(headers)
            for target, value in zip(targets, values):
                if target in date_targets:
                    value = parse_date(value) or value
                fresh[target] = value

            when = parse_date(fresh[column_of["Return Created Date"]])
            if when is not None:
                entry = FILE_STATS.setdefault(path, {}).setdefault(
                    text(fresh[column_of[SUMMARY_ACCOUNT_HEADER]]), [when, when, 0])
                entry[0], entry[1], entry[2] = min(entry[0], when), max(entry[1], when), entry[2] + 1

            key = activity_key(fresh)
            best = candidates.get(key)
            if best is None or freshness_of(fresh, column_of) >= freshness_of(
                best[0], column_of
            ):
                candidates[key] = (fresh, targets, date_targets)
        source.close()

    # Pass 2: merge those lines into the master.
    stamp = date.today()
    added = updated = replaced = unchanged = older = superseded = 0
    for key, (fresh, targets, date_targets) in candidates.items():
        current = seen.get(key)
        if current is None:
            parcel, freshness = parcel_of(fresh, column_of)
            if parcel[0] and parcel in parcels and parcels[parcel] >= freshness:
                superseded += 1
                continue
            stamp_row(fresh, stamp)
            rows.append(fresh)
            seen[key] = fresh
            if parcel[0]:
                parcels[parcel] = freshness
            added += 1
            continue

        if freshness_of(fresh, column_of) < freshness_of(current, column_of):
            older += 1
            continue

        order_column = column_of[RETURN_ORDER_HEADER]
        if text(fresh[order_column]) != text(current[order_column]):
            # A later return for the same invoice + SKU takes the row over
            # whole. It is a different parcel, so what OMS Guru or the
            # register said about the earlier one no longer applies; the
            # hand-typed ticket columns are not in the report and stay put.
            gaps = {column_of[header] for header in EXPORT_GAP_HEADERS if header in column_of}
            for target in targets:
                if target in gaps and fresh[target] in (None, ""):
                    continue
                current[target] = fresh[target]
            stamp_row(current, stamp)
            replaced += 1
            continue

        # A blank in the report is not news: the monthly exports leave FWD AWB
        # and FWD Carrier Name empty, and must not wipe what the master holds.
        changes = [
            target
            for target in targets
            if fresh[target] not in (None, "")
            and not same_value(fresh[target], current[target], target in date_targets)
        ]
        if not changes:
            unchanged += 1
            continue
        for target in changes:
            current[target] = fresh[target]
        # The OMS, register and GRN columns are left alone: a parcel that has
        # been received stays received however the report's status moves on.
        updated += 1

    return added, updated, replaced, unchanged, older, superseded, files


def grn_order_id(value):
    """The bare order id inside a GRN reference like "NAN-FN0172809725-1"."""
    if not value.startswith(GRN_ORDER_PREFIX):
        return value
    trimmed = value[len(GRN_ORDER_PREFIX):]
    head, _, tail = trimmed.rpartition("-")
    return head if head and tail.isdigit() else trimmed


def read_grn():
    """Parcels the ERP has booked in, keyed both ways.

    Returns ({order id: booked on}, {tracking id: booked on}, files read).
    """
    paths = grn_files()
    if not paths:
        return {}, {}, []

    by_order = {}
    by_tracking = {}
    for path in paths:
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook[workbook.sheetnames[0]]
        stream = sheet.iter_rows(values_only=True)
        headers = list(next(stream))
        index = {header: position for position, header in enumerate(headers) if header}
        order_columns = [index[header] for header in GRN_ORDER_COLUMNS if header in index]
        missing = [
            header for header in ("Order Tracking Id", "Voucher Date") if header not in index
        ]
        if not order_columns:
            missing.append(" or ".join(GRN_ORDER_COLUMNS))
        if missing:
            raise SystemExit(f"{path.name}: GRN columns missing: {missing}")

        for values in stream:
            if all(value in (None, "") for value in values):
                continue
            tracking = text(values[index["Order Tracking Id"]])
            order = ""
            for column in order_columns:
                candidate = text(values[column])
                if candidate.startswith(GRN_ORDER_PREFIX):
                    order = grn_order_id(candidate)
                    break
            if tracking == "-":
                tracking = ""
            if not order and not tracking:
                continue
            booked = parse_date(values[index["Voucher Date"]]) or date.min
            for key, table in ((order, by_order), (tracking, by_tracking)):
                if key:
                    table[key] = max(table.get(key, date.min), booked)
        workbook.close()

    return by_order, by_tracking, paths


def read_dispatch():
    """When each parcel left the warehouse, keyed by forward tracking id.

    Returns ({tracking id: (dispatched on, voucher no, SKU)}, files read).
    Only the register's AJIO lines are read. One tracking id can carry
    several articles, and they all left on the same voucher, so the latest
    voucher for an id is the one kept.
    """
    paths = [path for path in waiting_files() if is_dispatch(path)]
    if not paths:
        return {}, []

    by_tracking = {}
    for path in paths:
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook[workbook.sheetnames[0]]
        stream = sheet.iter_rows(values_only=True)
        headers = list(next(stream))
        index = {header: position for position, header in enumerate(headers) if header}
        missing = [
            field
            for field in (
                DISPATCH_PORTAL_FIELD,
                DISPATCH_TRACKING_FIELD,
                DISPATCH_SKU_FIELD,
                DISPATCH_DATE_FIELD,
                DISPATCH_VOUCHER_FIELD,
            )
            if field not in index
        ]
        if missing:
            raise SystemExit(f"{path.name}: dispatch columns missing: {missing}")

        for values in stream:
            if all(value in (None, "") for value in values):
                continue
            if DISPATCH_PORTAL not in text(values[index[DISPATCH_PORTAL_FIELD]]):
                continue
            tracking = text(values[index[DISPATCH_TRACKING_FIELD]])
            if not tracking:
                continue
            entry = (
                parse_date(values[index[DISPATCH_DATE_FIELD]]) or date.min,
                values[index[DISPATCH_VOUCHER_FIELD]],
                text(values[index[DISPATCH_SKU_FIELD]]),
            )
            current = by_tracking.get(tracking)
            if current is None or entry[0] >= current[0]:
                by_tracking[tracking] = entry
        workbook.close()

    return by_tracking, paths


def read_master():
    """Report rows, with every *date* column parsed into a real date."""
    workbook = load_workbook(SOURCE_MASTER, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    stream = sheet.iter_rows(values_only=True)
    headers = [header for header in next(stream) if header is not None]
    date_columns = [
        position
        for position, header in enumerate(headers)
        if "date" in str(header).casefold()
    ]

    rows = []
    for values in stream:
        row = list(values[: len(headers)])
        if all(value in (None, "") for value in row):
            continue
        for position in date_columns:
            parsed = parse_date(row[position])
            if parsed is not None:
                row[position] = parsed
        rows.append(row)
    workbook.close()
    return headers, rows


def read_oms():
    """Received parcels from OMS Guru, indexed by every identifier it carries.

    Every OMS Guru sheet found is read; a log line seen in more than one copy
    counts once.
    """
    paths = lookup_sources(SOURCE_OMS, OMS_FILENAME_HINT)
    strong = {}
    weak = {}
    seen = set()
    for path in paths:
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook["Sheet1"]
        stream = sheet.iter_rows(values_only=True)
        headers = list(next(stream))
        index = {
            str(header).strip(): position
            for position, header in enumerate(headers)
            if header
        }
        for values in stream:
            row = list(values)
            if all(value in (None, "") for value in row):
                continue
            status = row[index["Order Status"]]
            if text(status) not in OMS_RECEIVED_STATUSES:
                continue  # a handful of rows have shifted columns; skip them
            entry = (
                parse_date(row[index["Received On"]]) or parse_date(row[index["DATE"]]) or date.min,
                str(status).strip(),
                text(row[index["Item SkuCode"]]),
            )
            line = (text(row[index["Return Log ID"]]), entry[2], entry[1])
            if line[0] and line in seen:
                continue
            seen.add(line)
            for _, column in OMS_STRONG_LINKS:
                key = text(row[index[column]])
                if key and key != "#N/A":
                    strong.setdefault(key, []).append(entry)
            for _, column in OMS_WEAK_LINKS:
                key = text(row[index[column]])
                if key and key != "#N/A":
                    weak.setdefault(key, []).append(entry)
        workbook.close()
    return strong, weak, paths


def read_manual():
    """Received register keys, newest entry winning per (ref id, SKU).

    Every manual register found is read; the newest-wins rule below also
    settles a line that appears in more than one copy.
    """
    paths = lookup_sources(SOURCE_MANUAL, MANUAL_FILENAME_HINT, skip_hint=OMS_FILENAME_HINT)
    entries = []
    for path in paths:
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook["Sheet1"]
        for values in sheet.iter_rows(min_row=2, max_col=7, values_only=True):
            values = (tuple(values) + (None,) * 7)[:7]
            received_on, ref_id, sku, _, status, wrong_id, ticket = values
            if all(value in (None, "") for value in (ref_id, sku, status)):
                continue
            entries.append(
                {
                    "date": parse_date(received_on) or date.min,
                    "refs": [text(value) for value in (ref_id, wrong_id, ticket) if text(value)],
                    "key": (text(ref_id), text(sku)),
                    "received": is_received(status),
                }
            )
        workbook.close()

    # Same parcel logged twice -> the newest line decides, and a later
    # "received" outranks an earlier "not received".
    latest = {}
    for entry in entries:
        current = latest.get(entry["key"])
        if current is None or (entry["received"], entry["date"]) >= (
            current["received"],
            current["date"],
        ):
            latest[entry["key"]] = entry

    keys = {
        ref
        for entry in latest.values()
        if entry["received"]
        for ref in entry["refs"]
    }
    return keys, len(entries), paths


def collapse_by_awb(rows, index):
    """One forward parcel, one article, one row.

    The same SKU can be booked back several times against a single forward
    AWB - a re-attempted return, or the same line re-exported by the report -
    and only the most recent of those is live. Rows whose SKU differs are
    separate articles in a multi-item parcel, so every one of them survives.

    Returns (rows kept, rows dropped, hand-typed notes moved to a survivor).
    """
    awb_column = index.get("FWD AWB")
    sku_column = index.get("SELLER SKU")
    created_column = index.get("Return Created Date")
    status_column = index.get("Return Status")
    hand_columns = [index[header] for header in HAND_KEPT_HEADERS if header in index]
    if awb_column is None or sku_column is None:
        return rows, 0, 0

    groups = {}
    order = []
    for position, row in enumerate(rows):
        awb = text(row[awb_column])
        # No AWB means nothing to collapse against, so the row stands alone.
        key = (awb, text(row[sku_column])) if awb else ("", position)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((position, row))

    def freshness(entry):
        position, row = entry
        created = parse_date(row[created_column]) if created_column is not None else None
        stage = (
            RETURN_STATUS_RANK.get(text(row[status_column]), 0)
            if status_column is not None
            else 0
        )
        # Newest return wins; a tie goes to the further-along, later-seen row.
        return created or date.min, stage, position

    kept = []
    removed = 0
    rescued = 0
    for key in order:
        entries = groups[key]
        winner = max(entries, key=freshness)[1]
        if len(entries) > 1:
            for column in hand_columns:
                if text(winner[column]) not in HAND_KEPT_EMPTY:
                    continue
                for _, row in entries:
                    if row is not winner and text(row[column]) not in HAND_KEPT_EMPTY:
                        winner[column] = row[column]
                        rescued += 1
                        break
        kept.append(winner)
        removed += len(entries) - 1
    return kept, removed, rescued


def main():
    print("INPUT FILES")
    organize_inputs()
    headers, master_rows = read_master()
    if IMPORTED_HEADER not in headers:
        headers.append(IMPORTED_HEADER)
    width = len(headers)
    for row in master_rows:
        if len(row) < width:
            row.extend([None] * (width - len(row)))

    (
        imported,
        refreshed,
        replaced_rows,
        unchanged_rows,
        older_rows,
        superseded_rows,
        ingested_files,
    ) = ingest_input(headers, master_rows)
    numbers_fixed = normalize_numbers(headers, master_rows)

    index = {header: position for position, header in enumerate(headers)}
    identifier_columns = [index[header] for header in IDENTIFIER_HEADERS if header in index]
    activity_key = make_activity_key(index)
    imported_column = index.get(IMPORTED_HEADER)

    def is_imported(row):
        """Did this row arrive through input/ rather than the original master?"""
        if imported_column is None or imported_column >= len(row):
            return False
        return row[imported_column] not in (None, "")

    oms_strong, oms_weak, oms_paths = read_oms()
    if SOURCE_OMS[0] not in oms_paths:
        print("WARNING: shared drive unreachable, OMS Guru read from input copies only")
    manual_keys, manual_rows_read, manual_paths = read_manual()
    grn_by_order, grn_by_tracking, grn_paths = read_grn()
    tickets, ticket_paths = read_tickets()
    payments, payment_paths = read_payments()
    dispatch, dispatch_paths = read_dispatch()

    def oms_lookup(row, since=None):
        """(status, received on, how it matched) for one report row.

        With `since`, a receipt dated before that day belongs to an earlier
        return of the same order and is ignored.
        """

        def usable(entries):
            if since is None:
                return list(entries)
            return [entry for entry in entries if entry[0] == date.min or entry[0] >= since]

        candidates = []
        tier = "AWB"
        for report_header, _ in OMS_STRONG_LINKS:
            key = text(row[index[report_header]])
            if key:
                candidates += usable(oms_strong.get(key, []))
        if not candidates:
            tier = "ORDER/INVOICE"
            for report_header, _ in OMS_WEAK_LINKS:
                key = text(row[index[report_header]])
                if key:
                    candidates += usable(oms_weak.get(key, []))
        if not candidates:
            return None, None, None

        wanted = text(row[index["SELLER SKU"]])
        confirmed = [entry for entry in candidates if same_sku(wanted, entry[2])]
        tier = f"{tier} + SKU" if confirmed else tier
        received_on, status, _ = max(confirmed or candidates, key=lambda entry: entry[0])
        return status, (received_on if received_on != date.min else None), tier

    def rank(entry):
        position, row = entry
        stage = RETURN_STATUS_RANK.get(text(row[index["Return Status"]]), 0)
        if is_imported(row):
            oms = 0
        else:
            status, _, _ = oms_lookup(row)
            if status is None:
                stored = index.get("OMSGURU STATUS")
                status = row[stored] if stored is not None else None
            oms = OMS_STATUS_RANK.get(text(status), 0)
        latest = max(
            (
                row[index[header]]
                for header in PROGRESS_DATE_HEADERS
                if header in index and isinstance(row[index[header]], date)
            ),
            default=date.min,
        )
        filled = sum(1 for value in row if value not in (None, ""))
        created = parse_date(row[index["Return Created Date"]]) or date.min
        # Latest return first; same return, same completeness: the row
        # imported last is the newest.
        return created, stage, oms, latest, filled, position

    groups = {}
    order = []
    for entry in enumerate(master_rows):
        key = activity_key(entry[1])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(entry)

    hand_columns = [index[header] for header in HAND_KEPT_HEADERS if header in index]
    kept = []
    duplicates_removed = 0
    for key in order:
        entries = sorted(groups[key], key=rank)
        winner = entries[-1][1]
        # Ticket and payment columns are typed in, not in the report, so a
        # losing duplicate hands them to the row that stays.
        for column in hand_columns:
            if text(winner[column]) not in HAND_KEPT_EMPTY:
                continue
            for _, row in reversed(entries[:-1]):
                if text(row[column]) not in HAND_KEPT_EMPTY:
                    winner[column] = row[column]
                    break
        kept.append(winner)
        duplicates_removed += len(entries) - 1

    kept, awb_duplicates_removed, notes_rescued = collapse_by_awb(kept, index)

    # The master already carries most of the derived block, so only the
    # genuinely missing headers get appended - otherwise the output ends up
    # with two columns of the same name and the stale one wins the eye.
    final_headers = list(headers) + [
        header
        for header in DERIVED_HEADERS + TICKET_HEADERS + DISPATCH_HEADERS
        if header not in headers
    ]
    out_index = {header: position for position, header in enumerate(final_headers)}

    order_column = index.get(GRN_ORDER_HEADER)
    tracking_columns = [index[header] for header in GRN_TRACKING_HEADERS if header in index]

    def order_of(row):
        return text(row[order_column]) if order_column is not None else ""

    def grn_booked(row):
        """When the GRN booked this row in by its own order or tracking id."""
        order = order_of(row)
        hits = [grn_by_order[order]] if order in grn_by_order else []
        for column in tracking_columns:
            tracking = text(row[column])
            if tracking in grn_by_tracking:
                hits.append(grn_by_tracking[tracking])
        return max(hits) if hits else None

    # Orders the ERP confirmed: some row of the order matched the GRN by
    # order id or tracking id. Rows without an order id stand alone.
    grn_confirmed = {}
    for position, row in enumerate(kept):
        when = grn_booked(row)
        if when is None:
            continue
        key = order_of(row) or ("", position)
        grn_confirmed[key] = max(grn_confirmed.get(key, date.min), when)

    records = []
    oms_matched = 0
    confirmed_by_sku = 0
    oms_carried = 0
    manual_matched = 0
    imported_checked = 0
    grn_matched = 0
    for position, row in enumerate(kept):
        record = list(row) + [None] * (len(final_headers) - len(row))

        key = order_of(row) or ("", position)
        if key in grn_confirmed:
            # The ERP has this order on the shelf. That settles it for every
            # item in the order, whatever OMS Guru says and however new the
            # row is.
            grn_matched += 1
            when = grn_confirmed[key]
            record[out_index["OMSGURU STATUS"]] = GRN_RECEIVED
            record[out_index["OMS RECEIVED DATE"]] = None if when == date.min else when
            record[out_index["MANUAL RETURN STATUS"]] = None
            records.append(record)
            continue

        since = None
        if is_imported(row):
            # Came in through input/. OMS Guru and the manual sheets in
            # input/Manual Sheet/ are checked for it too, but only a receipt
            # on or after the return was created counts, and a miss leaves
            # whatever was typed in by hand.
            imported_checked += 1
            since = parse_date(row[index["Return Created Date"]])

        status, received_on, tier = oms_lookup(row, since)
        if status is not None:
            oms_matched += 1
            confirmed_by_sku += "SKU" in tier
            record[out_index["OMSGURU STATUS"]] = status
            record[out_index["OMS RECEIVED DATE"]] = received_on
        elif record[out_index["OMSGURU STATUS"]] not in (None, ""):
            # Nothing in today's OMS export, so the status the master
            # already holds is left exactly as it is.
            oms_carried += 1

        if any(text(row[column]) in manual_keys for column in identifier_columns if text(row[column])):
            manual_matched += 1
            record[out_index["MANUAL RETURN STATUS"]] = RECEIVED

        records.append(record)

    # The ticket export is the portal's own record, so where it knows a line
    # it overwrites whatever was typed in; lines it does not know keep theirs.
    tickets_matched = 0
    for record in records:
        key = (
            text(record[out_index[INVOICE_HEADER]]),
            text(record[out_index["SELLER SKU"]]),
        )
        ticket = tickets.get(key)
        if ticket is None:
            continue
        tickets_matched += 1
        for header in TICKET_HEADERS:
            record[out_index[header]] = ticket[header]

    # The dispatch register says when the parcel left us, matched on the
    # forward AWB. A row whose FWD AWB the report left blank has nothing to
    # match on and keeps whatever the master already holds.
    dispatch_matched = 0
    dispatch_other_sku = 0
    if DISPATCH_TRACKING_HEADER in out_index:
        for record in records:
            entry = dispatch.get(text(record[out_index[DISPATCH_TRACKING_HEADER]]))
            if entry is None:
                continue
            dispatched, voucher, sku = entry
            dispatch_matched += 1
            dispatch_other_sku += not same_sku(
                text(record[out_index["SELLER SKU"]]), sku
            )
            record[out_index[DISPATCH_DATE_HEADER]] = (
                None if dispatched == date.min else dispatched
            )
            record[out_index[DISPATCH_VOUCHER_HEADER]] = voucher

    # The ledger is the source of G4 and IC. When one invoice or challan
    # covers several rows, each row gets its share rather than the whole
    # amount, so the SUMMARY totals match the ledger. A line the ledger has
    # no document for keeps whatever the master already holds.
    payments_filled = Counter()
    payments_shared = Counter()
    for column, _, _, master_header, weight_headers in PAYMENT_LINKS:
        groups = {}
        for record in records:
            key = text(record[out_index[master_header]])
            if (column, key) in payments:
                groups.setdefault(key, []).append(record)

        def weight(record):
            for header in weight_headers:
                value = as_number(record[out_index[header]])
                if value:
                    return abs(value)
            return 0

        for key, group in groups.items():
            shares = share_out(payments[(column, key)], [weight(record) for record in group])
            for record, share in zip(group, shares):
                record[out_index[column]] = share
            payments_filled[column] += len(group)
            payments_shared[column] += len(group) > 1

    # read before the master is rewritten
    log = input_log(ingested_files)
    write(final_headers, records, out_index, log)
    print("INPUT CLEANUP")
    cleanup_inputs(ingested_files)

    print(f"INPUT_FILES_READ={len(ingested_files)}")
    for path in ingested_files:
        print(f"  {path.name}")
    print(f"ROWS_IMPORTED={imported}")
    print(f"ROWS_UPDATED={refreshed}")
    print(f"ROWS_REPLACED_BY_LATER_RETURN={replaced_rows}")
    print(f"ROWS_UNCHANGED={unchanged_rows}")
    print(f"ROWS_OLDER_THAN_MASTER_SKIPPED={older_rows}")
    print(f"ROWS_SUPERSEDED_BY_SAME_PARCEL={superseded_rows}")
    print(f"TEXT_VALUES_MADE_NUMERIC={numbers_fixed}")
    print(f"IMPORTED_ROWS_CHECKED={imported_checked}")
    print(f"MASTER={SOURCE_MASTER.resolve()}")
    print(f"OMS_SOURCES={len(oms_paths)}")
    for path in oms_paths:
        print(f"  {path}")
    print(f"MANUAL_SOURCES={len(manual_paths)}")
    for path in manual_paths:
        print(f"  {path}")
    print(f"REPORT_ROWS_IN={len(master_rows)}")
    print(f"DUPLICATE_ACTIVITIES_REMOVED={duplicates_removed}")
    print(f"AWB_SKU_DUPLICATES_REMOVED={awb_duplicates_removed}")
    print(f"HAND_NOTES_CARRIED_TO_SURVIVOR={notes_rescued}")
    print(f"OMS_MATCHED={oms_matched}")
    print(f"OMS_MATCHED_SKU_CONFIRMED={confirmed_by_sku}")
    print(f"OMS_CARRIED_FORWARD={oms_carried}")
    print(f"OMS_NOT_FOUND={len(kept) - oms_matched - oms_carried}")
    print(f"MANUAL_ROWS_CHECKED={manual_rows_read}")
    print(f"MANUAL_MATCHED={manual_matched}")
    print(f"GRN_FILES={len(grn_paths)}")
    for path in grn_paths:
        print(f"  {path.name}")
    print(f"GRN_KEYS={len(grn_by_order)} order / {len(grn_by_tracking)} tracking")
    print(f"GRN_ORDERS_CONFIRMED={len(grn_confirmed)}")
    print(f"GRN_ROWS_MARKED={grn_matched}")
    print(f"TICKET_FILES={len(ticket_paths)}")
    for path in ticket_paths:
        print(f"  {path.name}")
    print(f"TICKET_LINES={len(tickets)}")
    print(f"TICKET_ROWS_MATCHED={tickets_matched}")
    print(f"DISPATCH_FILES={len(dispatch_paths)}")
    for path in dispatch_paths:
        print(f"  {path.name}")
    print(f"DISPATCH_KEYS={len(dispatch)}")
    print(f"DISPATCH_ROWS_MATCHED={dispatch_matched}")
    print(f"DISPATCH_MATCHED_OTHER_SKU={dispatch_other_sku}")
    print(f"PAYMENT_FILES={len(payment_paths)}")
    for path in payment_paths:
        print(f"  {path.name}")
    for column, *_ in PAYMENT_LINKS:
        label = column.replace(" ", "_")
        print(f"PAYMENT_ROWS_{label}={payments_filled[column]}")
        print(f"PAYMENT_SPLIT_KEYS_{label}={payments_shared[column]}")
    print(f"TOTAL_ROWS={len(records)}")


def read_layout():
    """The look Sheet1 has been given in Excel, so a rewrite keeps it.

    Column order, header colours and fonts, column widths, the number format
    of each column and the frozen pane are all taken from the master as it is
    now. Returns None when there is no master to copy from.
    """
    if not SOURCE_MASTER.exists():
        return None
    workbook = load_workbook(SOURCE_MASTER)
    name = SHEET if SHEET in workbook.sheetnames else workbook.sheetnames[0]
    sheet = workbook[name]
    layout = {"order": [], "header": {}, "body": {}, "width": {}, "freeze": "A2"}
    # A column's number format is whatever its filled cells mostly use. Row 2
    # alone is not enough: it is often blank, and a sparse column such as
    # TICKET ID would lose its "0" format the run after it was set.
    used_formats = [Counter() for _ in range(sheet.max_column)]
    for line in sheet.iter_rows(min_row=2):
        for cell in line:
            if cell.value not in (None, ""):
                used_formats[cell.column - 1][cell.number_format] += 1
    for column in range(1, sheet.max_column + 1):
        head = sheet.cell(1, column)
        if head.value in (None, ""):
            continue
        body = sheet.cell(2, column)
        letter = get_column_letter(column)
        layout["order"].append(head.value)
        layout["header"][head.value] = (
            copy(head.font),
            copy(head.fill),
            copy(head.alignment),
            copy(head.border),
        )
        if used_formats[column - 1]:
            number_format = used_formats[column - 1].most_common(1)[0][0]
        else:
            dimension = sheet.column_dimensions[letter] if letter in sheet.column_dimensions else None
            column_format = dimension.number_format if dimension is not None else "General"
            number_format = column_format if column_format != "General" else body.number_format
        layout["body"][head.value] = (copy(body.font), number_format)
        if letter in sheet.column_dimensions and sheet.column_dimensions[letter].width:
            layout["width"][head.value] = sheet.column_dimensions[letter].width
    # freeze_panes reports the scrolled-to cell, not the split, so read the
    # split itself: one frozen header row shows up as ySplit=1.
    pane = sheet.sheet_view.pane
    if pane is not None and pane.state in ("frozen", "frozenSplit"):
        layout["freeze"] = (
            f"{get_column_letter(int(pane.xSplit or 0) + 1)}{int(pane.ySplit or 0) + 1}"
        )
    elif pane is None:
        layout["freeze"] = None
    workbook.close()
    return layout


def output_headers(final_headers, layout):
    """Sheet1's columns: the master's own order, plus what it must gain.

    A column the master no longer has was taken out in Excel and stays out,
    unless a formula or the summary cannot work without it. The ticket
    columns are new, so they are slotted in beside the ticket block.
    """
    if layout is None:
        return [header for header in final_headers if header not in RETIRED_HEADERS]
    headers = [
        header
        for header in layout["order"]
        if header in final_headers and header not in RETIRED_HEADERS
    ]
    # The comment columns are offered once. Any of them deleted in Excel
    # after that stays deleted.
    offered = TICKET_HEADERS
    if any(header in layout["order"] for header in COMMENT_HEADERS):
        offered = tuple(master for master, _ in TICKET_FIELDS)
    for header in offered:
        if header in headers:
            continue
        block = [position for position, name in enumerate(headers) if name in TICKET_HEADERS]
        headers.insert(block[-1] + 1 if block else len(headers), header)
    # The dispatch columns are offered once too, beside the forward AWB they
    # are matched on. Deleted in Excel afterwards, they stay deleted.
    if not any(header in layout["order"] for header in DISPATCH_HEADERS):
        anchor = (
            headers.index(DISPATCH_TRACKING_HEADER) + 1
            if DISPATCH_TRACKING_HEADER in headers
            else len(headers)
        )
        for offset, header in enumerate(DISPATCH_HEADERS):
            if header in final_headers:
                headers.insert(anchor + offset, header)
    headers += [
        header
        for header in REQUIRED_HEADERS
        if header in final_headers and header not in headers
    ]
    return headers


def write(final_headers, records, out_index, log):
    layout = read_layout()
    if SOURCE_MASTER.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(SOURCE_MASTER, BACKUP_DIR / f"AJIO MASTER - {stamp}.xlsx")

    headers = output_headers(final_headers, layout)
    column_of = {header: position for position, header in enumerate(headers)}
    sources = [out_index[header] for header in headers]

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET

    def styled_like(header):
        """The layout entry a column copies: its own, else its block's."""
        if layout is None:
            return None
        if header in layout["header"]:
            return header
        if header in TICKET_HEADERS:
            return next(
                (name for name in reversed(TICKET_HEADERS) if name in layout["header"]),
                None,
            )
        return None

    header_font = Font(bold=True)
    source_fill = PatternFill("solid", fgColor=SOURCE_FILL)
    derived_fill = PatternFill("solid", fgColor=DERIVED_FILL)
    body_formats = []
    body_fonts = []
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(1, column, header)
        model = styled_like(header)
        if model is None:
            cell.font = header_font
            is_ours = (
                header in DERIVED_HEADERS
                or header in TICKET_HEADERS
                or header in DISPATCH_HEADERS
            )
            cell.fill = derived_fill if is_ours else source_fill
            cell.alignment = Alignment(horizontal="left")
            body_formats.append("General")
            body_fonts.append(None)
        else:
            font, fill, alignment, border = layout["header"][model]
            cell.font, cell.fill = copy(font), copy(fill)
            cell.alignment, cell.border = copy(alignment), copy(border)
            font, number_format = layout["body"][model]
            body_formats.append(number_format)
            plain = font.name == "Calibri" and font.sz == 11 and not font.b and not font.i
            body_fonts.append(None if plain else font)

    for position, record in enumerate(records, start=2):
        for column, source in enumerate(sources, start=1):
            value = record[source]
            if value in (None, ""):
                continue
            cell = sheet.cell(position, column, value)
            number_format = body_formats[column - 1]
            if isinstance(value, (datetime, date)):
                if number_format == "General":
                    # Comment times carry the hour; report dates are midnight.
                    timed = isinstance(value, datetime) and value.time() != datetime.min.time()
                    number_format = DATETIME_FORMAT if timed else DATE_FORMAT
                cell.number_format = number_format
            elif number_format != "General":
                cell.number_format = number_format
            if body_fonts[column - 1] is not None:
                cell.font = copy(body_fonts[column - 1])

    for row in range(2, len(records) + 2):
        for header, formula in computed_formulas(column_of, row).items():
            column = column_of[header] + 1
            cell = sheet.cell(row, column, formula)
            number_format = body_formats[column - 1]
            if number_format != "General":
                cell.number_format = number_format
            elif header == "AGE (DAYS)":
                cell.number_format = "0"
            if body_fonts[column - 1] is not None:
                cell.font = copy(body_fonts[column - 1])

    for column, header in enumerate(headers, start=1):
        model = styled_like(header)
        width = layout["width"].get(model) if model is not None else None
        if (
            header in TICKET_HEADERS + DISPATCH_HEADERS
            and layout is not None
            and header not in layout["width"]
        ):
            width = max(len(header) + 6, 18)
        sheet.column_dimensions[get_column_letter(column)].width = width or min(
            max(len(str(header)) + 4, 12), 30
        )
        # Also on the column itself, so a value typed into a blank cell in
        # Excel picks the format up.
        if body_formats[column - 1] != "General":
            sheet.column_dimensions[get_column_letter(column)].number_format = body_formats[
                column - 1
            ]

    freeze = layout["freeze"] if layout is not None else "A2"
    if freeze:
        sheet.freeze_panes = freeze
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(records) + 1}"
    last = write_summary(workbook, column_of, records, out_index)
    write_coverage(workbook, last + 3, log)
    write_input_log(workbook, log)
    try:
        workbook.calculation.fullCalcOnLoad = True
    except AttributeError:
        pass
    workbook.save(SOURCE_MASTER)


def write_summary(workbook, column_of, records, record_index):
    """Add the SUMMARY sheet in the layout of "summary example.xlsx".

    One row per account and return month, a TOTAL row per account, a blank
    row between accounts and a GRAND TOTAL at the bottom. Every figure is a
    COUNTIFS/SUMIFS over Sheet1 rather than a number worked out here, so it
    follows any edit made in Excel - a ticket typed in, a status changed -
    and the day-dependent claim columns as they age.
    """
    sheet = workbook.create_sheet(SUMMARY_SHEET)

    def whole(header):
        letter = get_column_letter(column_of[header] + 1)
        return f"{SHEET}!${letter}:${letter}"

    def quoted(value):
        return f'"{value}"'

    account = whole(SUMMARY_ACCOUNT_HEADER)
    created = whole("Return Created Date")
    final = whole("FINAL RETURN STATUS")
    claim_status = whole("CLAIM STATUS")
    claim_type = whole("CLAIM TYPE")
    ticket = whole("TICKET ID")
    ticket_status = whole("STATUS")
    invoice = whole(SUMMARY_AMOUNT_HEADER)

    received = (final, quoted(RECEIVED))
    payment_done = (final, quoted(PAYMENT_DONE))
    # Still open: neither back on the shelf nor settled by payment.
    not_received = ((final, quoted("<>" + RECEIVED)), (final, quoted("<>" + PAYMENT_DONE)))

    def criteria(row, pairs):
        # Account in column A, month as "yyyy-mm" text in column B.
        month_start = f"DATE(LEFT($B{row},4),RIGHT($B{row},2),1)"
        base = (
            (account, f"$A{row}"),
            (created, f'">="&{month_start}'),
            (created, f'"<"&EDATE({month_start},1)'),
        )
        return ",".join(f"{rng},{crit}" for rng, crit in base + tuple(pairs))

    def count(*pairs):
        return lambda row: f"COUNTIFS({criteria(row, pairs)})"

    def total(*pairs, amount=invoice):
        # G4 PAYMENT and IC REVERSE hold #N/A errors from a stale lookup, and
        # one error in the summed rows would turn the whole SUMIFS into #N/A.
        # Asking for a number in the summed column itself skips them.
        numbers_only = (amount, quoted(">=-1E+307"))
        return lambda row: f"SUMIFS({amount},{criteria(row, pairs + (numbers_only,))})"

    def no_ticket(build, *pairs):
        # A failed lookup leaves "#N/A" behind; that is no ticket too.
        return lambda row: "+".join(
            build(*pairs, (ticket, quoted(empty)))(row) for empty in NO_TICKET
        )

    def has_ticket(build):
        return lambda row: (
            f"{build((ticket, quoted('<>')))(row)}"
            f"-{build((ticket, quoted('#N/A')))(row)}"
        )

    letter_of = {}

    def same_row(template):
        """A formula over other summary columns in the same row."""
        return lambda row: template.format(row=row, **letter_of)

    eligible = (claim_status, quoted("ELIGIBLE"))
    upcoming = (claim_status, quoted("UPCOMING CLAIM"))
    missed = (claim_status, quoted("NON ELIGIBLE"))

    # Same order and number formats as the example; the AJIO-only measures
    # follow its columns. (header, number format, formula, SUM on totals?)
    columns = [
        ("TOTAL RETURNS", COUNT_FORMAT, count(), True),
        ("RETURN RECEIVED", COUNT_FORMAT, count(received), True),
        ("PAYMENT DONE", COUNT_FORMAT, count(payment_done), True),
        ("PENDING", COUNT_FORMAT, count(*not_received), True),
        ("PENDING AMT", AMOUNT_FORMAT, total(*not_received), True),
        ("ELIGIBLE CLAIMS", COUNT_FORMAT, no_ticket(count, *not_received, eligible), True),
        ("PENDING CLAIM AMT", AMOUNT_FORMAT, no_ticket(total, *not_received, eligible), True),
        ("UPCOMING CLAIMS", COUNT_FORMAT, count(*not_received, upcoming), True),
        ("UPCOMING CLAIM AMT", AMOUNT_FORMAT, total(*not_received, upcoming), True),
        ("CLAIM DONE", COUNT_FORMAT, has_ticket(count), True),
        ("CLAIM DONE AMT", AMOUNT_FORMAT, has_ticket(total), True),
        ("CLAIM RESOLVED", COUNT_FORMAT, count((ticket_status, quoted("RESOLVED"))), True),
        ("CLAIM REJECTED", COUNT_FORMAT, count((ticket_status, quoted("REJECTED"))), True),
        ("CLAIM OPEN", COUNT_FORMAT, same_row("{DONE}{row}-{RESOLVED}{row}-{REJECTED}{row}"), False),
        ("MISSED CLAIMS", COUNT_FORMAT, no_ticket(count, *not_received, missed), True),
        ("MISSED CLAIM AMT", AMOUNT_FORMAT, no_ticket(total, *not_received, missed), True),
        ("IN TRANSIT", COUNT_FORMAT, count((claim_type, quoted("RETURN IN TRANSIT"))), True),
        ("RETURN CANCELLED", COUNT_FORMAT, count((claim_type, quoted("RETURN CANCELLED"))), True),
        ("DELIVERED NOT RECEIVED", COUNT_FORMAT, count((claim_type, quoted("RETURN NOT RECEIVED"))), True),
        ("CREDIT NOTE VALUE", AMOUNT_FORMAT, total(amount=whole("Credit Note Value")), True),
        ("G4 PAYMENT", AMOUNT_FORMAT, total(amount=whole("G4 PAYMENT")), True),
        ("IC REVERSE", AMOUNT_FORMAT, total(amount=whole("IC REVERSE")), True),
        # G4 is paid to us (positive), IC takes it back (positive).
        ("NET", AMOUNT_FORMAT, same_row("{G4}{row}-{IC}{row}"), False),
    ]
    first_data_column = 3
    positions = {header: first_data_column + i for i, (header, *_) in enumerate(columns)}
    for alias, header in (
        ("DONE", "CLAIM DONE"),
        ("RESOLVED", "CLAIM RESOLVED"),
        ("REJECTED", "CLAIM REJECTED"),
        ("G4", "G4 PAYMENT"),
        ("IC", "IC REVERSE"),
    ):
        letter_of[alias] = get_column_letter(positions[header])

    headers = ["ACCOUNT", "RETURN MONTH"] + [header for header, *_ in columns]
    width = len(headers)
    left = Alignment(horizontal="left")
    highlight_font = Font(name="Calibri", size=10, bold=True, color="FF000000")
    yellow = PatternFill("solid", fgColor=SUMMARY_HEADER_FILL)
    blue = PatternFill("solid", fgColor=SUMMARY_TOTAL_FILL)

    def paint(row, fill):
        for position in range(1, width + 1):
            cell = sheet.cell(row, position)
            cell.font = highlight_font
            cell.fill = fill

    for position, header in enumerate(headers, start=1):
        sheet.cell(1, position, header)
    paint(1, yellow)

    # column_of places Sheet1's columns; the records keep the script's order.
    created_column = record_index["Return Created Date"]
    account_column = record_index[SUMMARY_ACCOUNT_HEADER]
    months = [
        parsed.replace(day=1)
        for parsed in (parse_date(record[created_column]) for record in records)
        if parsed is not None
    ]
    today = date.today().replace(day=1)
    cursor = min(months, default=today)
    end = max(months + [today])
    month_labels = []
    while cursor <= end:
        month_labels.append(f"{cursor:%Y-%m}")
        cursor = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
    accounts = sorted(
        {text(record[account_column]) for record in records if text(record[account_column])}
    )

    def fill_row(row, is_total, block=None):
        for header, number_format, build, summed in columns:
            position = positions[header]
            letter = get_column_letter(position)
            if not is_total or not summed:
                formula = build(row)
            elif block is None:
                formula = f"SUM({letter}{row - len(month_labels)}:{letter}{row - 1})"
            else:
                formula = "+".join(f"{letter}{total_row}" for total_row in block)
            cell = sheet.cell(row, position, f"={formula}")
            cell.number_format = number_format

    row = 2
    total_rows = []
    for name in accounts:
        for label in month_labels:
            sheet.cell(row, 1, name)
            sheet.cell(row, 2, label)
            fill_row(row, is_total=False)
            row += 1
        sheet.cell(row, 1, name)
        sheet.cell(row, 2, "TOTAL")
        fill_row(row, is_total=True)
        paint(row, blue)
        total_rows.append(row)
        row += 2

    sheet.cell(row, 1, "ALL")
    sheet.cell(row, 2, "GRAND TOTAL")
    fill_row(row, is_total=True, block=total_rows)
    paint(row, yellow)

    body_font = Font(name="Aptos Narrow", size=11)
    for line in sheet.iter_rows(min_row=1, max_row=row, max_col=width):
        for cell in line:
            cell.alignment = left
            if cell.fill.fill_type is None:
                cell.font = body_font
    for position, header in enumerate(headers, start=1):
        sheet.column_dimensions[get_column_letter(position)].width = max(len(header) + 2, 9)
    return row


if __name__ == "__main__":
    main()
