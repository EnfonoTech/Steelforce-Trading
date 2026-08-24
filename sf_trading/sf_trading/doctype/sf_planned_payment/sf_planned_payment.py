# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class SFPlannedPayment(Document):
	"""How a refund is going to be paid, agreed before the return is approved.

	ERPNext refuses a Payment Entry that references a document which is not submitted
	(payment_entry.py validate_reference_documents), so the refund cannot exist even as a draft
	while the return is waiting for approval. This row is the promise instead; the Payment Entry
	is created and submitted the moment the return is.
	"""

	pass
