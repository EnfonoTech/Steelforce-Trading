"""
Currency-based Purchase Tax Template selection.

Applies to: Supplier Quotation, Purchase Order, Purchase Receipt,
Purchase Invoice (before_validate hook in hooks.py).

Rule:
  document currency == company currency → the company's default Purchase
    Taxes and Charges Template (is_default = 1)
  otherwise → the company's template flagged "For Foreign Currency"
    (custom_for_foreign_currency checkbox on the template)

Templates are selected purely by those two flags — no name matching, no
auto-creation. If the matching template doesn't exist for a company, the
document is left untouched. Flag exactly one template per company as
For Foreign Currency.

Being server-side, this works for UI entry, mapped documents
(PO → PR → PI), Data Import and API inserts alike. A manually chosen
template outside the managed pair (default / foreign) is respected.

get_expected_template() is whitelisted and also called from
public/js/purchase_tax_template.js for live feedback on the form.
"""

import frappe


def set_template_by_currency(doc, method=None):
	"""before_validate: pick the tax template matching the document currency."""
	if not doc.get("company") or not doc.get("currency"):
		return

	expected = get_expected_template(doc.company, doc.currency)
	if not expected:
		return

	current = doc.get("taxes_and_charges")
	if current == expected:
		# Template already right — just fill the rows if the table is empty
		# (e.g. API inserts that pass only taxes_and_charges).
		if not doc.get("taxes"):
			_apply_template(doc, expected)
		return

	# Respect a deliberately chosen template outside the managed pair.
	if current and current not in _managed_templates(doc.company):
		return

	_apply_template(doc, expected)


@frappe.whitelist()
def get_expected_template(company, currency):
	"""Template that should apply for this company + currency combination."""
	if not company or not currency:
		return None

	company_currency = frappe.get_cached_value("Company", company, "default_currency")
	if currency == company_currency:
		return _get_default_template(company)
	return _get_foreign_template(company)


def _get_default_template(company):
	return frappe.db.get_value(
		"Purchase Taxes and Charges Template",
		{"company": company, "is_default": 1, "disabled": 0},
		"name",
	)


def _get_foreign_template(company):
	return frappe.db.get_value(
		"Purchase Taxes and Charges Template",
		{"company": company, "custom_for_foreign_currency": 1, "disabled": 0},
		"name",
	)


def _managed_templates(company):
	"""The two templates this module is allowed to switch between."""
	return {_get_default_template(company), _get_foreign_template(company)} - {None}


def _apply_template(doc, template):
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	doc.taxes_and_charges = template
	doc.set("taxes", [])
	for tax in get_taxes_and_charges("Purchase Taxes and Charges Template", template) or []:
		doc.append("taxes", tax)
