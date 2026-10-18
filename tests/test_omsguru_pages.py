from bindal.portals.omsguru_returns import parse_order_page as parse_return_page
from bindal.portals.omsguru import parse_order_page

PAGE = """<html><body>
<div id="tab-details"><div class="row separateBlock"><dl>
  <dt>Invoice Number:</dt><dd>INV123</dd><dt>Order:</dt><dd>FN555</dd>
  <dt>Order Date:</dt><dd>October 10, 2026 09:15:00 AM</dd>
  <dt>Shipment Tracker:</dt><dd>1367065910666 ( XPRESSBEES )</dd>
</dl></div></div>
<div id="order-items"><table><tbody>
  <tr data-id="91"><td>1</td><td>LEA-SKU1 - Kurta</td><td>Qty: 1 Delivered Return Received Return Comments ok
     Return Log RT260459 ( 2026-10-05)</td></tr>
</tbody></table></div>
<table><tr><th>Status</th><th>Timestamp</th></tr><tr><td>Delivered</td><td>October 11, 2026 10:00:00 AM</td></tr></table>
</body></html>"""


def test_order_page_rows():
    items, hist = parse_order_page(PAGE, "800")
    assert items[0]["Input Identifier"] == "INV123"
    assert items[0]["SKU Code"] == "LEA-SKU1"
    assert items[0]["Shipping Company"] == "XPRESSBEES"
    assert hist == [{"Internal Order ID": "800", "Status": "Delivered", "Timestamp": "October 11, 2026 10:00:00 AM"}]


def test_return_page_reads_receipt():
    items, order = parse_return_page(PAGE)
    assert order["forward_awb"] == "1367065910666" and order["carrier"] == "XPRESSBEES"
    assert items[0]["log"] == "RT260459"
    assert items[0]["status"] == "Return Received"
