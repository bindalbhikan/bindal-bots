"""Reliance SRM statement of accounts (the AJIO vendor ledger: G4 payments, IC reversals).

Login is the old SAP BSP portal (retsrm.ril.com). Its hidden sap-login-XSRF
value is HTML-escaped ("&#x3d;") and must be unescaped, or SAP answers "Logon
cookie check failed". The dashboard's "Statement of Accounts" link posts the
page form with OnInputProcessing=SoaOpenSrcClick and lands on
srm-rrscm.ril.com/login?sap-params=..., whose value is the authText for
/srm/uaa/api/v1/authenticate. Every rrscm call must go through the same
session (WAF cookies) with Origin/Referer set.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import date
from urllib.parse import unquote, urljoin

import requests

log = logging.getLogger(__name__)

PORTAL = "https://retsrm.ril.com/sap/bc/bsp/sap/zsrm_portal/Dashboard.htm?ClearMsg=X"
RRSCM = "https://srm-rrscm.ril.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
COMPANY_CODE = "9008"   # Reliance Retail Limited

# PAYMENT.xlsx header -> SRM item field (the shape of the old manual ledger export)
PAYMENT_COLUMNS = {
    "DT": "DOC_TYPE", "REFERENCE": "REF_DOC_NO_LONG", "VENDORCODE": "VENDOR", "DOCDATE": "DOC_DATE",
    "DOCNO": "DOC_NO", "PONO": "EBELN", "FISCALYEAR": "FISC_YEAR", "AMOUNT": "amount",
    "DUEDATE": "due_date", "CLEARINGDOC": "CLR_DOC_NO", "CLEARINGDOCUMENTDATE": "CLEAR_DATE",
    "TEXT": "ITEM_TEXT", "TYPE": "type", "INVOICE REFERENCE NUMBER": "INV_REF",
}
INPUT = re.compile(r'<input[^>]*name="([^"]+)"[^>]*value\s*=\s*"([^"]*)"')


class SrmError(RuntimeError):
    pass


class Srm:
    def __init__(self, user: str, password: str, timeout: int = 300):
        self.user, self.password, self.timeout = user, password, timeout
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.token = ""

    def login(self) -> None:
        self.s.cookies.set("Active", "true", domain="retsrm.ril.com", path="/")
        r = self.s.get(PORTAL, timeout=60)
        form = re.search(r"<form[^>]*>.*?</form>", r.text, re.S | re.I)
        if not form:
            raise SrmError("SRM login page not found")
        fields = {k: html.unescape(v) for k, v in INPUT.findall(form.group(0))}
        fields.update({"sap-user": self.user, "sap-password": self.password, "sap-system-login": "onLogin",
                       "sap-system-login-oninputprocessing": "onLogin", "sap-language": "EN"})
        fields.setdefault("sap-client", "421")
        action = re.search(r'action="([^"]*)"', form.group(0))
        dash = self.s.post(urljoin(r.url, action.group(1)) if action else r.url, data=fields, timeout=60)
        if "Welcome" not in dash.text:
            msg = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<[^>]+>", " ", dash.text, flags=re.S))
            raise SrmError(f"SRM login failed: {msg[:200]}")

        page = re.search(r'<form id="myform".*?</form>', dash.text, re.S).group(0)
        data = {k: html.unescape(v) for k, v in INPUT.findall(page)}
        data.update(OnInputProcessing="SoaOpenSrcClick", ActionFlag="")
        soa = self.s.post(dash.url, data=data, timeout=60)
        link = re.search(r"""https://srm-rrscm\.ril\.com/[^"' ]*sap-params=([^&"' ]+)""",
                         soa.url + " " + soa.text)
        if not link:
            raise SrmError("SRM did not hand over to the statement-of-accounts portal")
        auth = self.s.post(f"{RRSCM}/srm/uaa/api/v1/authenticate",
                           json={"authText": unquote(html.unescape(link.group(1)))}, timeout=60,
                           headers=self._headers(referer=soa.url)).json()
        if not auth.get("token"):
            raise SrmError(f"SRM authenticate failed: {auth.get('message')}")
        self.token = auth["token"]
        log.info("Logged in to Reliance SRM (vendor %s)", auth.get("vendorId"))

    def _headers(self, referer: str = f"{RRSCM}/reports/statement-of-accounts") -> dict:
        h = {"Origin": RRSCM, "Referer": referer, "Accept": "application/json, text/plain, */*"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def statement(self, start: date, end: date) -> list[dict]:
        r = self.s.post(f"{RRSCM}/srm/rdl/api/v1/report/statement-off-acc/fetch", timeout=self.timeout,
                        json={"companyCode": COMPANY_CODE, "fromDate": f"{start:%Y%m%d}", "toDate": f"{end:%Y%m%d}"},
                        headers=self._headers())
        if r.status_code != 200:
            raise SrmError(f"SRM statement failed: HTTP {r.status_code} {r.text[:200]}")
        items = ((r.json() or {}).get("param") or {}).get("T_ITEMS") or []
        log.info("SRM statement %s..%s: %d ledger lines", start, end, len(items))
        return items


def payment_rows(items: list[dict]) -> list[dict]:
    """SRM items in the PAYMENT.xlsx layout the master script reads."""
    return [{col: item.get(field) for col, field in PAYMENT_COLUMNS.items()} for item in items]
