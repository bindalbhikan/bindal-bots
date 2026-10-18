"""AJIO ads / TOT gap working.

The hand-run tool AJIO/Ajio Ads Gap Working/main.py, unchanged except that its folder can be set
with AJIO_ADS_DIR; the bot (bindal/ajio_ads/pipeline.py) fetches the statement from SRM.

Reads the latest statement of account from ./input and writes a single report:
  output/AJIO_Simple_Monthly_G4_Charges.xlsx  (monthly G4 vs TOT marketing charge)

Usage:
  python main.py
"""
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

# The bot points this at a work folder holding input/ (AJIO_ADS_DIR)
import os  # noqa: E402
BASE = Path(os.environ.get('AJIO_ADS_DIR') or Path(__file__).resolve().parent)
INPUT = BASE / 'input'
OUTPUT = BASE / 'output'
TARGET = OUTPUT / 'AJIO_Simple_Monthly_G4_Charges.xlsx'
STATEMENT_GLOB = 'StatementOfAccount*.xlsx'

SERVICE_GST = Decimal('1.18')  # assumed 18% GST on AJIO service charges
ROMAN = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7,
         'VIII': 8, 'IX': 9, 'X': 10, 'XI': 11, 'XII': 12}
# Narrations look like "TOT Mkt Contrn_ VII 26-NJ" or "TOT Mkt Contrn_II26-NJ".
SERVICE_MONTH_RE = re.compile(r"([IVX]+)\s*'?\s*(\d{2})(?!\d)")

HEADER_FILL = PatternFill('solid', fgColor='24476A')
BAND_FILL = PatternFill('solid', fgColor='EEF3F8')
HEADER_FONT = Font(color='FFFFFF', bold=True)


def latest_statement():
    files = sorted(INPUT.glob(STATEMENT_GLOB), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f'No file matching {STATEMENT_GLOB!r} in {INPUT}')
    return files[-1]


def read_sheet(path):
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    rows = ws.values
    header = next(rows)
    return [dict(zip(header, r)) for r in rows]


def D(v):
    return Decimal(str(v or 0))


def round2(v):
    return v.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def doc_month(v):
    return datetime.strptime(v, '%d.%m.%Y').strftime('%Y-%m')


def service_month(text):
    m = SERVICE_MONTH_RE.search(text)
    if not m or m.group(1) not in ROMAN:
        raise ValueError(f'Cannot read service month from narration: {text!r}')
    return f'20{m.group(2)}-{ROMAN[m.group(1)]:02d}'


def build_report(ledger):
    vendors = sorted({r['VENDORCODE'] for r in ledger if r['VENDORCODE']})
    statement_end = max(datetime.strptime(r['DOCDATE'], '%d.%m.%Y') for r in ledger if r['DOCDATE'])

    g4, ads = defaultdict(Decimal), defaultdict(Decimal)
    for r in ledger:
        if r['DT'] == 'G4':
            g4[doc_month(r['DOCDATE'])] += -D(r['AMOUNT'])
        text = r['TEXT'] or ''
        if 'TOT Mkt' in text:
            ads[service_month(text)] += D(r['AMOUNT'])

    wb = openpyxl.Workbook()
    s = wb.active
    s.title = 'Monthly Simple Report'
    s.append(['AJIO - Monthly actual G4 vs TOT marketing / ads charge'])
    s.merge_cells('A1:G1')
    s.append(['Month', 'Actual G4 amount (statement)', 'Marketing / Ads without GST*', 'GST @18%*',
              'Actual Marketing / Ads total', 'Charge / G4 %', 'Status'])
    for m in sorted(g4):
        actual = ads.get(m)
        if actual is None:
            s.append([datetime.strptime(m, '%Y-%m'), float(g4[m]), None, None, None, None,
                      'No charge entry found'])
            continue
        base = round2(actual / SERVICE_GST)
        s.append([datetime.strptime(m, '%Y-%m'), float(g4[m]), float(base), float(actual - base),
                  float(actual), float(actual / g4[m]) if g4[m] else None, 'Posted charge found'])
    last = s.max_row
    tot_base = sum((round2(v / SERVICE_GST) for v in ads.values()), Decimal(0))
    tot_ads = sum(ads.values(), Decimal(0))
    tot_g4 = sum(g4.values(), Decimal(0))
    s.append(['TOTAL', float(tot_g4), float(tot_base), float(tot_ads - tot_base), float(tot_ads),
              float(tot_ads / tot_g4) if tot_g4 else None, 'Includes all shown G4 months'])
    s.append([])
    for note in [
        'G4 month = statement document date. Marketing month = service month in the TOT Mkt narration (charge is posted in the following month).',
        '*18% GST assumed: no tax invoice supplied. Without GST = actual charge / 1.18; GST = actual minus without GST.',
        'G4 = actual forward statement amount, shown positive; IC returns are not deducted. Do not read G4 as sales before commission.',
        'Charge / G4 % is only a ratio of actual amounts; it does not directly verify the agreed 3% marketing rate.',
        'Marketing / Ads here is the TOT marketing contribution; separate commission, cancellation and campaign spend are not included.',
        f'Vendor {", ".join(vendors)} only. Statement through {statement_end:%d-%b-%Y}; the last month may be partial. A blank charge does not mean zero liability.',
    ]:
        s.append([note])
        s.merge_cells(start_row=s.max_row, start_column=1, end_row=s.max_row, end_column=7)
        s.cell(s.max_row, 1).alignment = Alignment(wrap_text=True, vertical='center')
        s.row_dimensions[s.max_row].height = 31
    for row in [1, 2, last + 1]:
        for c in s[row]:
            c.fill, c.font = HEADER_FILL, HEADER_FONT
            c.alignment = Alignment(wrap_text=True, vertical='center')
    s.row_dimensions[1].height = 29
    s.row_dimensions[2].height = 42
    for row in s.iter_rows(min_row=3, max_row=last + 1):
        row[0].number_format = 'mmm-yyyy'
        for c in row[1:5]:
            c.number_format = '#,##0.00'
        row[5].number_format = '0.00%'
        if row[0].row % 2 and row[0].row <= last:
            for c in row:
                c.fill = BAND_FILL
    for col, width in zip('ABCDEFG', [16, 25, 25, 19, 27, 18, 30]):
        s.column_dimensions[col].width = width
    s.freeze_panes = 'B3'
    s.auto_filter.ref = f'A2:G{last}'
    s.sheet_properties.pageSetUpPr.fitToPage = True
    s.page_setup.orientation = 'landscape'
    s.page_setup.paperSize = s.PAPERSIZE_A4
    s.page_setup.fitToWidth = s.page_setup.fitToHeight = 1
    s.print_options.horizontalCentered = True
    s.print_area = s.dimensions

    OUTPUT.mkdir(exist_ok=True)
    wb.save(TARGET)
    check = openpyxl.load_workbook(TARGET, read_only=True, data_only=True)
    assert len(check.sheetnames) == 1
    assert abs(sum(check.active.cell(i, 5).value or 0 for i in range(3, last + 1))
               - sum(float(v) for m, v in ads.items() if m in g4)) < .01
    unshown = sorted(set(ads) - set(g4))
    if unshown:
        print('WARNING: marketing charges for months without G4 rows are not shown:', unshown)
    print('Saved:', TARGET)
    print(f'  G4 total {tot_g4:,.2f} | marketing ex GST {tot_base:,.2f} | GST {tot_ads - tot_base:,.2f} | total {tot_ads:,.2f}')


def main():
    statement = latest_statement()
    print(f'Reading {statement.name} ...')
    build_report(read_sheet(statement))


if __name__ == '__main__':
    main()
