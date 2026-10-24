"""The OMSGuru client lives in ultron.portals.omsguru (the same for every seller); the bots
keep importing it from here."""
from ultron.portals.omsguru import *  # noqa: F401,F403
from ultron.portals.omsguru import BASE_URL, VIEW_ID, OmsGuruClient, OmsGuruError, item_status, parse_order_page, text_of  # noqa: F401
