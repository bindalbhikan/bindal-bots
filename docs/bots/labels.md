# Label check — `python run.py labels`

**When**: 6 PM IST daily (`labels.yml`, GitHub schedule; the Cloudflare cron's 5 free triggers are
all used). By hand: run the workflow.

**Result**: one Telegram message, nothing written.

* ERP report 62 (Shipping Label Print), every portal, prints of the last `LABEL_ERP_DAYS` (5) days;
  an AWB's first print counts. Its "Order ID" is the AWB (Myntra: the Myntra tracking number,
  which OMSGuru shows as the AWB too). 20 Oct 2026: AJIO, Nykaa, Meesho, Amazon 100% found in
  OMSGuru, Myntra 99%, Flipkart 90%.
* OMSGuru order list of the last `LABEL_ORDER_DAYS` (12) order days (`order_lines`), every channel.
* Checked, for prints since yesterday 00:00:
  * 🛑 printed for an order OMSGuru shows cancelled (Cancel Init / Cancelled ...): check it, hold it if
    not handed over yet
  * ⏳ printed but still New / Pending / Processing / Packed in OMSGuru
* ❓ ready to ship in OMSGuru, ordered in the last `LABEL_RTS_DAYS` (3) days, with no ERP print in
  the window: packed outside the ERP, so its stock was not taken off there.
* 👷 label prints per packer (ERP "Created By") since yesterday, with the cancelled ones.
* 25 orders listed per section at most. 20 Oct: 1,689 prints since 19 Oct, 9 for cancelled
  orders (Flipkart 8, Meesho 1), 0 not ready to ship, 40 ready to ship without an ERP label.
