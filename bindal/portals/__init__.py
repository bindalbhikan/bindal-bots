"""Clients for the outside systems, shared by every bot (one module per system):

    myntra          Myntra partner portal: saved sessions per seller account, report jobs
    flipkart        Flipkart Seller Hub: login, FBF metrics, report centre (sales / returns)
    ajio            AJIO Seller Central: login, dropship order + RTV reports, SPF tickets
    srm             Reliance SRM: statement of accounts (AJIO vendor ledger)
    erp             DigiBizz ERP: GRN, dispatch / packing reports
    omsguru         OMSGuru: login, order lists and pages
    omsguru_returns OMSGuru return lookups (is a return logged as received?)

See docs/PORTALS.md for each system's API, limits and the lessons learned.
"""
