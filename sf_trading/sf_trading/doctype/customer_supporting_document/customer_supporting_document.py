# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CustomerSupportingDocument(Document):
	"""One supporting document held for a Customer or Supplier -- its type, number, and
	expiry, tracked alongside (not instead of) the party's own file attachments."""

	pass
