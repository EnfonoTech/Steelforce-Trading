# sf_trading/draft_print.py
"""No print of a draft Sales Invoice; a draft Sales Return still prints.

A sale printed before it is submitted hands the customer a bill that may yet change or never post.
A return, on the other hand, waits in draft for approval (sales_return.py), and its draft copy is
what goes to the customer and to the approver -- so it must stay printable.

Print Settings' "Allow Print for Draft" cannot draw that line: it is one switch for every doctype.
So the setting stays on, and Sales Invoice refuses its own draft sales here, in `before_print`,
which the print view runs for every route that renders the document -- the form's Print, the PDF
download, bulk print from the list, and an email with the print attached. The form hides its
Print entries for the same drafts (public/js/sales_invoice_draft_print.js), so nobody is offered
what this would refuse.

The Delivery Note print (Print DN: the invoice in the company's Delivery Note print format) is
not the bill and is left exactly as it was -- it still prints from a draft.
"""

import frappe
from frappe import _


def is_blocked(doc) -> bool:
	return doc.docstatus == 0 and not doc.get("is_return")


def delivery_note_formats() -> set:
	"""Every company's Delivery Note print format (Company custom_delivery_note_print_format). The
	Print DN button prints the invoice in that format, and a draft's DN prints as it always did."""
	return set(
		frappe.get_all(
			"Company",
			filters={"custom_delivery_note_print_format": ["is", "set"]},
			pluck="custom_delivery_note_print_format",
		)
	)


def block_draft_sale_print(doc, _method=None, *args, **kwargs):
	"""Sales Invoice before_print."""
	# the format asked for, as the print view and the PDF download both receive it
	if frappe.form_dict.get("format") in delivery_note_formats():
		return
	if is_blocked(doc):
		frappe.throw(
			_("Draft Sales Invoice {0} cannot be printed. Submit it first.").format(frappe.bold(doc.name)),
			frappe.PermissionError,
			title=_("Print Not Allowed"),
		)
