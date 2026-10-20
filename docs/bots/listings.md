# Listing health — `python run.py listings`

**When**: 11:30 AM IST daily (`listings.yml`, GitHub schedule). By hand: run the workflow, or ask
the agent on Telegram ("listings" action).

**Result**: one Telegram message, nothing written. Channels with inventory sync on only.

* Listings: OMSGuru Channel Listings > **Download Mapped Channel Listings**
  (`OmsGuruClient.export_listings`: queued, the channel_listing_mapping.csv link appears in the
  notifications, ~30 s; 160,743 listings on 20 Oct 2026, 140,674 on active channels). The listing
  pages show the current warehouse's listings, so it runs on the first warehouse.
* Stock: the inventory export (as in the stock-out alert).
* 📭 in stock but 0 listed on the channel and not capped: the stock is not reaching the channel.
* ⚠️ listed more than in stock: the channel can sell what is not there.
* ❌ sync failures ("Failure Reason") of the last `LISTING_FAIL_DAYS` (7) days, by reason.
* 🚫 the Failed tab (`failed_listings`), 🔒 listings capped at 0 (Max Listed Qty 0) while in
  stock, 🔗 the UnManaged count (not mapped to an OMSGuru SKU).
* One line per channel + SKU, 15 per problem. 20 Oct: 0 / 33 / 16 / 29 / 1,433 / 1,398.
