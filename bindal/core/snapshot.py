"""Picture of one workbook sheet for Telegram.

The workbook is written by openpyxl, so its formula cells carry no cached results.
LibreOffice (headless, with a profile that forces "recalculate on load") renders the
sheet to a one-page PDF and pdftoppm turns that page into a PNG.
Needs `soffice` and `pdftoppm` (packages libreoffice-calc, poppler-utils).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from openpyxl import load_workbook

# OOXMLRecalcMode 0 = always recalculate .xlsx formulas when the file is loaded.
RECALC_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""
# Each sheet becomes a single PDF page sized to its content (no page breaks).
PDF_FILTER = "pdf:calc_pdf_Export:" + json.dumps({"SinglePageSheets": {"type": "boolean", "value": "true"}})


def sheet_png(xlsx: Path, sheet_title: str, out_dir: Path, dpi: int = 110) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice or not shutil.which("pdftoppm"):
        raise RuntimeError("LibreOffice (soffice) and pdftoppm are needed for the sheet picture")
    work = Path(tempfile.mkdtemp(prefix="snapshot_"))

    # A copy holding only this sheet, so the PDF is exactly one page.
    wb = load_workbook(xlsx)
    for ws in list(wb.worksheets):
        if ws.title != sheet_title:
            wb.remove(ws)
    wb[sheet_title].sheet_state = "visible"
    wb.active = 0
    single = work / "sheet.xlsx"
    wb.save(single)

    profile = work / "profile"
    (profile / "user").mkdir(parents=True)
    (profile / "user" / "registrymodifications.xcu").write_text(RECALC_PROFILE, encoding="utf-8")
    subprocess.run([soffice, "--headless", f"-env:UserInstallation={profile.as_uri()}",
                    "--convert-to", PDF_FILTER, "--outdir", str(work), str(single)],
                   check=True, timeout=240, capture_output=True)
    pdf = work / "sheet.pdf"
    if not pdf.exists():
        raise RuntimeError("LibreOffice did not produce a PDF")

    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / re.sub(r"[^A-Za-z0-9_-]+", "_", sheet_title)
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", "1", "-l", "1", "-singlefile", str(pdf), str(base)],
                   check=True, timeout=120, capture_output=True)
    return base.with_suffix(".png")
