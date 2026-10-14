"""DigiBizz ERP reports (GRN and dispatch / packing), read through the report-preview API.

Used by the AJIO SPF bot (GRN + dispatch registers) and the Myntra VMS bot (packed AWBs).

A report's SQL (sqlQueryData) names each output column "column_<a>_<b>" after
the view field it comes from, e.g. ISNULL("VOUCHER_NO",'-') AS column_51_1702.
That mapping is read on every run, so a report edited in the ERP keeps working
as long as the view fields stay.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import date, datetime

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

log = logging.getLogger(__name__)

API = "https://digicorptech.net/DigiBizzErpBindalApi/api"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
COMPANY = [{"key": "companyId", "value": 1}, {"key": "companyYearId", "value": 50}, {"key": "officeBranchId", "value": 1}]
PAGE = 10000

GRN_REPORT = 58          # "GR GRN Report"
DISPATCH_REPORT = 62     # "Shipping Label Print (Packing Report)"

# view field -> header the SPF master script expects
GRN_HEADERS = {
    "VOUCHER_NO": "Voucher No.", "TRANS_DATE": "Voucher Date", "STOCK_TYPE": "Stock Type",
    "BARCODE": "Barcode", "SKU_CODE": "SKU Code", "BRAND_NAME": "Brand Name",
    "QTY_PIECES": "Qty (Pcs.)", "TRANS_REMARK": "Trans. Remarks",
    "ORDER_PORTAL_NAME": "Order Portal Name", "ORDER_TRACKING_ID": "Order Tracking Id",
    "ORDER_FIRM_NAME": "Order Firm Name", "ORDER_ITEM_ID": "Order Item Id",
}
DISPATCH_HEADERS = {
    "VOUCHER_NO": "Voucher No.", "TRANS_DATE": "Voucher Date", "ORDER_ID": "Order ID",
    "CREATED_BY": "Created By", "CREATED_DATE": "Created On", "SKU_CODE": "SKU Code",
    "ORDER_PORTAL_NAME": "Order Portal Name", "BARCODE": "Barcode",
}
ALIAS = re.compile(
    r'(?:ISNULL\(\s*"?(\w+)"?\s*,[^)]*\)|convert\(\s*date\s*,\s*"?(\w+)"?\s*\)|sum\(\s*"?(\w+)"?\s*\)|"?(\w+)"?)'
    r"\s+AS\s+(column_\w+)", re.I)
# The web app AES-encrypts the password before Login/Login (AES-128-CBC, PKCS7,
# key = IV = this fixed string, base64 output), so the bot does the same.
WEB_KEY = b"8080808080808080"
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class ErpError(RuntimeError):
    pass


def web_encrypt(password: str) -> str:
    padder = padding.PKCS7(128).padder()
    data = padder.update(password.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(WEB_KEY), modes.CBC(WEB_KEY)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


class Erp:
    def __init__(self, username: str, password: str, user_token: str, timeout: int = 300):
        self.body = {"Username": username, "Password": web_encrypt(password), "UserToken": user_token}
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.user_id = None

    def login(self) -> None:
        j = self.s.post(f"{API}/Login/Login", json=self.body, timeout=60).json()
        if not j.get("IsAuthSuccessful"):
            raise ErpError(f"ERP login failed: {j.get('ErrorMessage')}")
        if j.get("isReqOtpVerification"):
            raise ErpError("ERP login now asks for an OTP - the bot cannot answer it")
        self.user_id = j["userId"]
        self.s.headers["Authorization"] = f"Bearer {j['Token']}"
        t = self.s.post(f"{API}/Login/RefreshToken", json=COMPANY, timeout=60).json()
        self.s.headers.update({"Authorization": f"Bearer {t['Token']}",
                               "UserToken": j.get("UserToken") or self.body["UserToken"],
                               "X-Requested-With": "XMLHttpRequest"})
        log.info("Logged in to DigiBizz ERP")

    def _fields(self, report_id: int) -> dict[str, str]:
        v = self.s.get(f"{API}/TblRptReports/GetSingleReportPreviewBE", params={"reportId": report_id},
                       timeout=60).json()
        sql = (v.get("objBE") or {}).get("sqlQueryData") or ""
        inner = sql[sql.find("FROM (") + 6:] if "FROM (" in sql else sql
        found = {}
        for m in ALIAS.finditer(inner):
            field = next(g for g in m.groups()[:4] if g)
            found[m.group(5)] = field.upper()
        if not found:
            raise ErpError(f"ERP report {report_id}: could not read its column list")
        return found

    @classmethod
    def from_env(cls) -> "Erp":
        from .config import env
        client = cls(env("ERP_USERNAME"), env("ERP_PASSWORD"), env("ERP_USER_TOKEN"))
        if not env("ERP_USERNAME") or not env("ERP_PASSWORD"):
            raise ErpError("ERP_USERNAME / ERP_PASSWORD not set")
        return client

    def report(self, report_id: int, headers: dict[str, str], where: list | None = None) -> list[dict]:
        """Every row of a report, keyed by the master's header names.
        where: [(view field, operator, value), ...] all of which must hold (server-side filter)."""
        alias = self._fields(report_id)
        missing = [f for f in headers if f not in alias.values()]
        if missing:
            raise ErpError(f"ERP report {report_id} no longer has fields {missing}")
        rows, skip, total = [], 0, None
        while total is None or skip < total:
            p = {"reportId": report_id, "userId": self.user_id, "runtimeParamJSON": "[]",
                 "skip": skip, "take": PAGE, "requireTotalCount": "true"}
            if where:
                column = {f: c for c, f in alias.items()}
                parts = [[column[f], op, v] for f, op, v in where]
                p["filter"] = json.dumps(parts[0] if len(parts) == 1 else
                                         [x for part in parts for x in ("and", part)][1:])
            v = self.s.get(f"{API}/TblRptReports/GetReportPreviewDataSource", params=p, timeout=self.timeout).json()
            total = v.get("totalCount") or 0
            data = v.get("data") or []
            if not data:
                break
            for rec in data:
                row = {}
                for col, field in alias.items():
                    if field in headers:
                        val = rec.get(col)
                        if isinstance(val, str) and ISO.match(val):
                            val = datetime.fromisoformat(val[:19])
                        row[headers[field]] = val
                rows.append(row)
            skip += PAGE
        log.info("ERP report %s: %d rows", report_id, len(rows))
        return rows

    def grn(self) -> list[dict]:
        return self.report(GRN_REPORT, GRN_HEADERS)

    def dispatch(self) -> list[dict]:
        return self.report(DISPATCH_REPORT, DISPATCH_HEADERS)

    def packed(self, portal: str, since: date) -> list[dict]:
        """Packing-report lines of one portal (e.g. MYNTRA) from `since`. For Myntra the
        "Order ID" is the Myntra tracking number (MYSP..., MYSC...)."""
        return self.report(DISPATCH_REPORT, DISPATCH_HEADERS,
                           where=[("TRANS_DATE", ">=", f"{since:%Y-%m-%d}T00:00:00"),
                                  ("ORDER_PORTAL_NAME", "=", portal.upper())])
