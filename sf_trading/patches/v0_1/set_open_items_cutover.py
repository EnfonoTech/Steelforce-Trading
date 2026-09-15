# sf_trading/patches/v0_1/set_open_items_cutover.py
"""Date the open item reports from go-live, because nothing older is really open.

Steel Force went live on ERPNext in the week of 2026-07-09: the importer created 797 receipts on
the 9th and 137 on the 10th, and the first document typed by a human rather than a script is dated
2026-07-13. The accountant confirmed against ePromise that no receipt and no invoice was left
pending at the cutover, so everything the import left looking open is an artifact of the import --
it wrote the receipts, and the invoices that had already settled them in the old system arrived
carrying no link to them.

15 July is the date the business gave, and it is the one used.

Only fills the field when it is empty: a date somebody chose by hand outranks this.
"""

import frappe

CUTOVER = "2026-07-15"


def execute():
	settings = frappe.get_single("SF Trading Settings")
	if settings.get("open_items_cutover_date"):
		return

	settings.db_set("open_items_cutover_date", CUTOVER, update_modified=False)
	frappe.logger("sf_trading").info(f"open items cutover set to {CUTOVER}")
