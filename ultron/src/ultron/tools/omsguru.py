"""Built-in tools "omsguru": orders, sale and SLA from the seller's OMSGuru account (the login is
OMSGURU_EMAIL / OMSGURU_PASSWORD / OMSGURU_OTP). Read-only. Channels are named as the seller's
[channels] in agent.toml say.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from ..core.config import channel_line
from ..core.llm import tool

_client = None


def _oms():
    global _client
    if _client is None:
        from ..portals.omsguru import OmsGuruClient
        _client = OmsGuruClient.from_env()
        _client.login()
    return _client


@tool
def find_order(number: str) -> str:
    """Look an order up in OMSGuru by its channel order number, AWB or invoice number: status,
    dates, SLA, courier, AWB, SKUs, status history. No buyer details.

    Args:
        number: the order number, AWB or invoice number as the owner gave it.
    """
    c = _oms()
    ids = []
    for wh in list(c.warehouses()) or [None]:
        if wh:
            c.set_warehouse(wh)
        for kind in ("order", "AWB", "invoice"):
            ids += [i for i in c.search(number.strip(), kind) if i not in ids]
        if ids:
            break
    if not ids:
        return f"No OMSGuru order found for {number!r} (searched order number, AWB and invoice)."
    out = []
    for oid in ids[:3]:
        orders, hist = c.fetch_order(oid)
        if not orders:
            continue
        o = orders[0]
        keep = ["Channel", "Channel Order ID", "Input Identifier", "Order Date", "Invoice Date", "SLA Date",
                "Dispatch Log", "AWB / Shipment Tracker", "Shipping Company", "Warehouse", "Payment Mode"]
        lines = [f"{k}: {o.get(k)}" for k in keep if o.get(k)]
        lines.append("Items: " + "; ".join(f"{i.get('SKU Code')} x{i.get('Qty')} ({i.get('Item Status')})" for i in orders))
        lines.append("History: " + "; ".join(f"{h['Status']} {h['Timestamp']}" for h in hist[:8]))
        out.append(f"OMSGuru order {oid}\n" + "\n".join(lines))
    return "\n\n".join(out) or f"Order pages of {ids} could not be read."


@tool
def sale_by_channel(day: str = "yesterday") -> str:
    """Units ordered per channel on one day (every channel, by order date), with cancelled units.

    Args:
        day: "today", "yesterday" or a date YYYY-MM-DD.
    """
    from ..portals.omsguru_orders import units_by_channel
    d = {"today": date.today(), "yesterday": date.today() - timedelta(days=1)}.get(day.strip().lower())
    d = d or date.fromisoformat(day.strip())
    units, cancel, names = units_by_channel(_oms(), [d])
    per, canc = Counter(), Counter()
    for (_, ch), q in units.items():
        per[channel_line(names.get(ch, ch))] += q
        canc[channel_line(names.get(ch, ch))] += cancel[(d, ch)]
    rows = [f"{c}: {n} units ({canc[c]} cancelled)" for c, n in per.most_common()]
    return f"Orders of {d:%a %d %b %Y}" + (" so far" if d == date.today() else "") + ":\n" + "\n".join(
        rows + [f"Total: {sum(per.values())} units ({sum(canc.values())} cancelled)"])


@tool
def due_today() -> str:
    """Orders whose dispatch SLA is today or passed and are still not ready to ship, per channel,
    with up to 20 order numbers."""
    from ..portals.omsguru_orders import at_risk, order_lines
    now = datetime.now()
    lines, names = order_lines(_oms(), [now.date() - timedelta(days=i) for i in range(8)])
    risk = at_risk(lines, now)
    per = Counter(channel_line(names.get(r["channel"], r["channel"])) for r in risk)
    head = f"{len(risk)} orders due today or overdue, not ready to ship ({sum(1 for r in risk if r['sla'] < now)} overdue)"
    return "\n".join([head, *(f"{c}: {n}" for c, n in per.most_common()),
                      *(f"{r['order']} {r['status']} SLA {r['sla']:%d %b %H:%M}" for r in risk[:20])])


TOOLS = [find_order, sale_by_channel, due_today]
