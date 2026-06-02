import frappe
from frappe.utils import flt


def validate(doc, method=None):
	"""Block overbilling at draft/save stage by checking all non-cancelled PIs."""
	_check_overbilling(doc)


def on_save(doc, method=None):
	"""Update Purchase Receipt billing approval status after every save."""
	_update_pr_approval_status(doc)


def _check_overbilling(doc):
	"""Prevent total billed qty (drafts + submitted) from exceeding PO qty."""
	for item in doc.items:
		if not item.get("po_detail"):
			continue

		po_item = frappe.db.get_value(
			"Purchase Order Item",
			item.po_detail,
			["qty", "item_code"],
			as_dict=True,
		)
		if not po_item:
			continue

		# Sum qty across ALL non-cancelled PIs for this PO line (excluding current doc)
		existing_billed_qty = frappe.db.sql(
			"""
			SELECT IFNULL(SUM(pi_item.qty), 0)
			FROM `tabPurchase Invoice Item` pi_item
			INNER JOIN `tabPurchase Invoice` pi ON pi.name = pi_item.parent
			WHERE pi_item.po_detail = %s
			  AND pi.docstatus != 2
			  AND pi.name != %s
		""",
			(item.po_detail, doc.name or ""),
		)[0][0]

		total_qty = flt(existing_billed_qty) + flt(item.qty)

		if total_qty > flt(po_item.qty):
			over_by = total_qty - flt(po_item.qty)
			frappe.throw(
				"Overbilling blocked for item <b>{0}</b>:<br>"
				"PO qty: <b>{1}</b> | Already billed (incl. drafts): <b>{2}</b> "
				"| This PI qty: <b>{3}</b><br>"
				"Over by <b>{4}</b>. Cancel or reduce qty in another draft PI first.".format(
					po_item.item_code,
					flt(po_item.qty),
					flt(existing_billed_qty),
					flt(item.qty),
					over_by,
				)
			)


def _update_pr_approval_status(doc):
	"""Set custom_billing_approval_status on linked Purchase Receipts."""
	linked_prs = list(
		set([item.purchase_receipt for item in doc.items if item.get("purchase_receipt")])
	)

	for pr_name in linked_prs:
		# Check if any OTHER non-cancelled PI for this PR is in Pending Approval
		pending_elsewhere = frappe.db.sql(
			"""
			SELECT COUNT(pi.name)
			FROM `tabPurchase Invoice` pi
			INNER JOIN `tabPurchase Invoice Item` pi_item ON pi_item.parent = pi.name
			WHERE pi_item.purchase_receipt = %s
			  AND pi.docstatus != 2
			  AND pi.workflow_state = 'Pending Approval'
			  AND pi.name != %s
		""",
			(pr_name, doc.name or ""),
		)[0][0]

		current_is_pending = doc.workflow_state == "Pending Approval"

		new_status = "Pending Approval" if (pending_elsewhere or current_is_pending) else ""

		frappe.db.set_value(
			"Purchase Receipt",
			pr_name,
			"custom_billing_approval_status",
			new_status,
			update_modified=False,
		)
