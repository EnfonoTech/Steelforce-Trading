# sf_trading/overrides/sales_invoice_advance.py
"""Allocate the order's advance on an invoice raised against a Sales Order.

The mirror of overrides/purchase_invoice.set_advance_allocation, and for the same reason: a
deposit taken on the order has to land on the invoice that bills it, or the customer shows
both an unallocated advance and a full receivable.

Turns on ERPNext's own two switches rather than allocating anything by hand.
`allocate_advances_automatically` makes `validate` call `set_advances()`, and
`only_include_allocated_payments` keeps that to advances actually allocated against the
orders on this invoice. Both are needed, and the second is the important one: without it
`get_advance_entries` also returns every Payment Entry for the customer with
`unallocated_amount > 0` and sweeps that on-account money into the invoice FIFO -- the exact
failure the purchase side hit with 61,792 BHD of supplier money.

Both fields also carry a DocField default of 1, shipped as a Property Setter. That is what
lets the advance table fill the moment the invoice is created from an order:
`sales_order.make_sales_invoice` calls `target.set_advances()` in its postprocess, but only
`if target.get("allocate_advances_automatically")` (erpnext/selling/doctype/sales_order/
sales_order.py:1175), and a hook running at before_validate is far too late for that -- it
fires on save. So the default does the creation-time work and this hook decides whether the
switches were right.

Which matters, because on an invoice that names no order they are wrong. `set_advances` on
such an invoice has no order to match against, so the reference filter falls away and every
submitted customer advance is a candidate. Turning the switches back off means that path is
never reached.

Three cases keep them off:
  * no item names a Sales Order -- nothing to allocate against
  * a POS invoice -- `accounts_controller.set_advances` is skipped when `is_pos` is set
    (accounts_controller.py:313), so claiming to have set something up would be a lie
  * a return -- a credit note reducing a receivable has no business consuming an advance

The one exception is an invoice that already carries advance rows. Nothing populates them on
load -- ERPNext's `fetch_advances` fires on a change event, not on render -- so rows being
present means somebody asked for them, and their choice stands. Only on a new invoice, so
anyone who unticks either box and saves again keeps that too.
"""

from frappe.utils import cint


def set_advance_allocation(doc, method=None):
	if not doc.is_new():
		return

	if cint(doc.get("is_return")):
		if not doc.get("advances"):
			doc.allocate_advances_automatically = 0
			doc.only_include_allocated_payments = 0
		return

	if not any(item.get("sales_order") for item in doc.get("items") or []):
		if not doc.get("advances"):
			doc.allocate_advances_automatically = 0
			doc.only_include_allocated_payments = 0
		return

	if cint(doc.get("is_pos")):
		doc.allocate_advances_automatically = 0
		return

	doc.allocate_advances_automatically = 1
	doc.only_include_allocated_payments = 1
