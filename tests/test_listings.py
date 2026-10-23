from collections import Counter
from datetime import datetime

from bindal.core import telegram
from bindal.listings import pipeline as p

CSV = b"""Channel ID,Channel Name,Channel Listing SKU Code,Channel Listing Id,Product SkuCode,Channel Listed Qty,Channel Max Listed Qty,Failure Reason
1,BINDAL EXPORTS LIMITED - MYNTRA,L1,L1,a-1-m,0,-1,
1,BINDAL EXPORTS LIMITED - MYNTRA,L2,L2,A-1-L,9,-1,
1,BINDAL EXPORTS LIMITED - MYNTRA,L3,L3,A-1-L,9,-1,
1,BINDAL EXPORTS LIMITED - MYNTRA,L4,L4,A-1-S,0,0,
1,BINDAL EXPORTS LIMITED - MYNTRA,L5,L5,A-1-XL,2,-1,"October 19, 2026 05:56:26 PM:No Response from channel"
1,BINDAL EXPORTS LIMITED - MYNTRA,L6,L6,A-1-XS,2,-1,"May 07, 2026 06:52:42 PM:No Response from channel"
9,BINDAL EXPORTS LIMITED - Ajio,L7,L7,A-1-M,0,-1,
"""


def test_check_and_message():
    listings = p.table_of(CSV)
    stock = Counter({"A-1-M": 5, "A-1-L": 8, "A-1-S": 3, "A-1-XL": 4, "A-1-XS": 4})
    res = p.check(listings, stock, active={"1"}, since=datetime(2026, 10, 13))
    assert res["listings"] == 6                                     # the inactive channel 9 left out
    assert list(res["not_listed"]["sku"]) == ["A-1-M"]
    assert list(res["over"]["sku"]) == ["A-1-L", "A-1-L"]
    assert list(res["capped"]["sku"]) == ["A-1-S"]
    assert list(res["failed"]["sku"]) == ["A-1-XL"]                 # the May failure is old
    text = telegram.render(p.message(res, [], 1398, datetime(2026, 10, 20), 7))
    assert "In stock, 0 on the channel: 1</b> (Myntra MAIN 1)" in text
    assert "• Myntra MAIN · A-1-L · listed 9, stock 8 · 2 listings" in text
    assert "Sync failures in the last 7 days: 1" in text and "• No Response from channel: 1" in text
    assert "UnManaged): 1,398" in text
