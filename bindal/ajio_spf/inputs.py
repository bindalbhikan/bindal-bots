"""Write the fetched data as the input files the SPF master script reads.

The master script (master_engine.py, the user's SPF/AJIO/main.py) works on a
folder: AJIO MASTER.xlsx next to an input/ directory holding the returns
report, GRN, dispatch, payment and ticket exports, plus OMS Guru / manual
register sheets under input/Manual Sheet/. These helpers recreate exactly
that layout from the API data, so the script itself stays unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook

OMS_COLUMNS = [
    "Return Log ID", "Channel Name", "Invoice Number", "Invoice Date", "Order Number", "Sub Order ID",
    "Selling Price Per Item", "SKU MRP", "Shipped Qty", "Invoice", "Item SkuCode", "Good Qty", "Bad Qty",
    "Payment Type", "Return Reason", "Return Sub Reason", "Return Comments", "Return Log Comments",
    "Received On", "Received By", "Return Action", "FWD Courier", "FWD Tracker", "Return Tracker",
    "Extra Details", "State Name", "Extra Key", "Order Status", "Channel Return Init Date",
    "Channel Return Delivered Date", "Brand", "Return Status", "Payment Weight", " Payment Weight Type", "DATE",
]
TICKET_COLUMNS = [
    "ticketId", "createdDate", "status", "ticketType", "issueType", "subIssueType", "issueTitle", "storeName",
    "orderId", "lastUpdatedOn", "resolution", "resolvedDate", "returnOrderId", "returnAwbNumber",
    "returnShipmentId", "returnCarrier", "returnType", "returnCreationDate", "customerOrderId",
    "forwardPONumber", "forwardInvoiceNumber", "forwardInvoiceAmount", "forwardAwbNumber", "skuId", "jioCode",
    "commentsTotal", "lastCommentBy", "lastCommentType", "lastCommentAt", "lastCommentText",
]
COMMENT_COLUMNS = ["ticketId", "status", "seq", "side", "authorType", "authorName", "createdAt", "content"]
TAGS = re.compile(r"<[^>]+>")


def write_xlsx(path: Path, sheets: dict[str, tuple[list[str], list[dict]]]) -> Path:
    """{sheet name: (headers, rows as dicts)} -> one workbook, written in streaming mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook(write_only=True)
    for name, (headers, rows) in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h) for h in headers])
    wb.save(path)
    return path


def plain(text) -> str:
    """A portal comment as plain text (the portal stores some as HTML)."""
    t = TAGS.sub(" ", str(text or "")).replace("&nbsp;", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


def is_seller(comment: dict, seller_id: str) -> bool:
    return (str(comment.get("authorType") or "").upper() == "SELLER"
            or str(comment.get("authorId") or "") == seller_id)


def sorted_comments(ticket: dict) -> list[dict]:
    return sorted(ticket.get("comments") or [], key=lambda c: str(c.get("createdAt") or ""))


def ticket_sheets(details: list[dict], seller_id: str) -> dict:
    tickets, comments = [], []
    for t in details:
        od = t.get("orderDetails") or {}
        cs = sorted_comments(t)
        last = cs[-1] if cs else {}
        tickets.append({
            "ticketId": str(t.get("ticketId") or ""), "createdDate": t.get("createdDate"),
            "status": t.get("status"), "ticketType": t.get("ticketType"),
            "issueType": t.get("issueType") or t.get("category"),
            "subIssueType": t.get("subIssueType") or t.get("subCategory"),
            "issueTitle": t.get("issueTitle") or t.get("subject"), "storeName": t.get("storeName"),
            "orderId": t.get("orderId"), "lastUpdatedOn": t.get("lastUpdatedOn"),
            "resolution": t.get("resolution"), "resolvedDate": t.get("resolvedDate"),
            **{k: od.get(k) for k in ("returnOrderId", "returnAwbNumber", "returnShipmentId", "returnCarrier",
                                      "returnType", "returnCreationDate", "customerOrderId", "forwardPONumber",
                                      "forwardInvoiceNumber", "forwardInvoiceAmount", "forwardAwbNumber",
                                      "skuId", "jioCode")},
            "commentsTotal": len(cs),
            "lastCommentBy": last.get("authorName") or last.get("authorType"),
            "lastCommentType": "SELLER" if cs and is_seller(last, seller_id) else (last.get("authorType") if cs else None),
            "lastCommentAt": last.get("createdAt"), "lastCommentText": plain(last.get("content")) if cs else None,
        })
        for seq, c in enumerate(cs, start=1):
            comments.append({
                "ticketId": str(t.get("ticketId") or ""), "status": t.get("status"), "seq": seq,
                "side": "SELLER" if is_seller(c, seller_id) else "AJIO", "authorType": c.get("authorType"),
                "authorName": c.get("authorName"), "createdAt": c.get("createdAt"), "content": plain(c.get("content")),
            })
    return {"Tickets": (TICKET_COLUMNS, tickets), "Comments": (COMMENT_COLUMNS, comments)}
