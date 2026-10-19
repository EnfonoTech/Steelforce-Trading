# sf_trading/sf_trading/doctype/sf_report_number/sf_report_number.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class SFReportNumber(Document):
	"""The number a Report was given, kept apart from the report itself (sf_trading/report_numbering.py): a
	standard report is deleted and inserted again when its JSON changes, and its number must outlive that."""

	pass
