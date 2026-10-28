# sf_trading/financial_statements_search_links.py
"""Account links on Trial Balance, Balance Sheet, P&L and Cash Flow stay clickable while a column is searched.

The fix itself is desk-side (public/js/financial_statements_search_links.js): the datatable's column search hands
ERPNext's formatter the search text, the formatter answers a row whose account name does not contain it with the bare
name, no link, and the datatable caches that HTML on the cell, so the row is drawn without its link once it shows.

`SF Trading Settings > Keep Account Links Clickable While Searching ...` turns it off; a switch nobody stored is ON
(switches.py). The desk reads it from the boot, which the settings clear on save (purchase_receipt_gate.clear_boot_cache).
"""

from sf_trading import switches

SWITCH = "keep_account_links_on_search"


def boot_session(bootinfo):
	"""boot_session: tell the desk whether to keep the links (public/js/financial_statements_search_links.js)."""
	bootinfo["sf_keep_report_links"] = 1 if switches.is_on(SWITCH) else 0
