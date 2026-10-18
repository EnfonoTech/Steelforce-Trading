# sf_trading/landed_cost_with_invoice.py
"""A Landed Cost Voucher travels with the Purchase Invoice it costs.

Until now an import was booked, approved and submitted first, and its freight and customs were put
into the stock afterwards by a Landed Cost Voucher raised by hand -- one to twenty-five days later on
steelforce, and for two bills of 15 October never at all. Now the voucher is drawn up while the
invoice is still a draft, goes for approval with it, and is submitted by the same approval:

* **Which invoices.** An *import purchase* is a Purchase Invoice that updates stock and is either in
  a foreign currency or carries a Bayan (customs) number. Such an invoice needs a voucher before it can
  go for approval (SF Trading Settings > Landed Costs switches that on and off). A bill with no landed
  cost ticks "Import Without Landed Cost" and says why. Any other stock invoice may carry a voucher too;
  it is simply not required.
* **Core says no, so one rule is relaxed.** ERPNext refuses a voucher against a document that is not
  yet submitted. `overrides/landed_cost_voucher_class.py` relaxes exactly that, for a voucher that is
  itself a draft, names one draft stock invoice and nothing else; every other case goes to core.
* **Gate to approval.** The save that moves an import out of Draft / Rejected into an approval step
  refuses unless a draft voucher is there and everything its own submit would refuse has been checked
  (`preflight`, nothing written). `before_submit` repeats the check for a document submitted without a
  workflow. A refusal there stops, it never changes the voucher behind anyone's back.
* **Submit.** The invoice's own `on_submit` runs first and posts its stock and ledger entries; then
  `submit_vouchers` submits every draft voucher of the invoice, in the same transaction. If one cannot
  be submitted the whole approval is rolled back with that voucher's own message.
* **The invoice changes while it is a draft or in approval** (approvers may edit it). `follow_invoice`
  compares each draft voucher with the invoice -- rows, quantities, base amounts, company -- and
  rebuilds the voucher's items to match, keeping its charges. It says so on screen, on the voucher's
  timeline and to the voucher's owner. A voucher distributed by hand, or one that cannot follow, stops
  the invoice's save and names the voucher.
* **Locked.** Once the invoice leaves Draft / Rejected the voucher's charges cannot be edited or
  deleted: they are part of what is being approved. An approver who wants a change rejects the invoice.
* **Cancel.** Cancelling the invoice first cancels its submitted (single-invoice) vouchers, so core's
  back-link check finds nothing and one cancellation is enough. A draft invoice that is deleted takes
  its draft vouchers with it. Cancelling a voucher on its own stays allowed -- that is how a voucher is
  corrected -- but says the invoice now carries no landed cost.

A voucher built this way names ONE invoice. Vouchers spread over several documents keep working as they
did, against submitted documents only.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, cstr, flt, get_link_to_form

INVOICE = "Purchase Invoice"
VOUCHER = "Landed Cost Voucher"
VOUCHER_RECEIPT = "Landed Cost Purchase Receipt"
SETTINGS = "SF Trading Settings"
SWITCH = "landed_cost_require_with_import"
ESCAPE_FIELD = "custom_import_without_lcv"
REASON_FIELD = "custom_import_without_lcv_reason"
#: workflow states in which the invoice is still its author's to change
OPEN_STATES = ("", "Draft", "Rejected")
TOLERANCE = 0.0005
#: set while the system itself rebuilds, submits, cancels or deletes a voucher on the invoice's behalf
SYSTEM_FLAG = "sf_lcv_system"
DELETING_FLAG = "sf_lcv_deleting_with_invoice"


def ensure_custom_fields():
	"""after_migrate: the opt-out on the invoice, and the reason it must carry."""
	on = "eval:doc.update_stock && doc.%s" % ESCAPE_FIELD
	create_custom_fields(
		{
			INVOICE: [
				{
					"fieldname": ESCAPE_FIELD,
					"label": "Import Without Landed Cost",
					"fieldtype": "Check",
					"insert_after": "update_stock",
					"depends_on": "eval:doc.update_stock",
					"no_copy": 1,
					"description": "Tick when this import carries no landed cost (the supplier delivers to the door, say). "
					"It then goes for approval without a Landed Cost Voucher.",
				},
				{
					"fieldname": REASON_FIELD,
					"label": "Why No Landed Cost",
					"fieldtype": "Small Text",
					"insert_after": ESCAPE_FIELD,
					"depends_on": on,
					"mandatory_depends_on": on,
					"no_copy": 1,
				},
			]
		},
		ignore_validate=True,
		update=True,
	)


# ─── what is an import, and what it needs ──────────────────────────────────────────────────────


def require_with_import() -> bool:
	value = frappe.db.get_single_value(SETTINGS, SWITCH)
	return True if value in (None, "") else bool(cint(value))


def company_currency(company):
	return frappe.get_cached_value("Company", company, "default_currency")


def is_import(doc) -> bool:
	"""A stock invoice (not a return) billed in a foreign currency or declared at customs."""
	if doc.get("doctype") != INVOICE or not cint(doc.get("update_stock")) or cint(doc.get("is_return")):
		return False
	currency, company = doc.get("currency"), doc.get("company")
	foreign = bool(currency and company and currency != company_currency(company))
	return foreign or bool(cstr(doc.get("custom_bayan_value")).strip())


def must_have_voucher(doc) -> bool:
	return require_with_import() and is_import(doc) and not cint(doc.get(ESCAPE_FIELD))


def has_asset_rows(doc) -> bool:
	return any(cint(row.get("is_fixed_asset")) for row in doc.get("items") or [])


def state_of(doc) -> str:
	return cstr(doc.get("workflow_state")).strip()


def is_locked(state) -> bool:
	return cstr(state).strip() not in OPEN_STATES


def _is_new(doc) -> bool:
	getter = getattr(doc, "is_new", None)
	return bool(getter()) if callable(getter) else not doc.get("name")


def _link(doctype, name):
	return get_link_to_form(doctype, name)


# ─── which vouchers belong to an invoice ──────────────────────────────────────────────────────


def vouchers_of(invoice, docstatus=None) -> list:
	"""The Landed Cost Vouchers naming this invoice: live ones (draft or submitted) unless a docstatus is given."""
	if not invoice:
		return []
	parents = frappe.get_all(
		VOUCHER_RECEIPT,
		filters={"parenttype": VOUCHER, "receipt_document_type": INVOICE, "receipt_document": invoice},
		pluck="parent",
	)
	if not parents:
		return []
	filters = {"name": ["in", list(set(parents))]}
	filters["docstatus"] = docstatus if docstatus is not None else ["<", 2]
	return frappe.get_all(
		VOUCHER,
		filters=filters,
		fields=["name", "docstatus", "owner", "total_taxes_and_charges", "distribute_charges_based_on"],
		order_by="creation",
	)


def names_one_document(voucher_name) -> bool:
	return frappe.db.count(VOUCHER_RECEIPT, {"parent": voucher_name, "parenttype": VOUCHER}) == 1


def invoice_of(voucher):
	"""The one Purchase Invoice a voucher names, when it names exactly one document and that is an invoice."""
	rows = voucher.get("purchase_receipts") or []
	if len(rows) == 1 and rows[0].get("receipt_document_type") == INVOICE and rows[0].get("receipt_document"):
		return rows[0].receipt_document
	return None


def invoice_info(name):
	return frappe.db.get_value(INVOICE, name, ["docstatus", "workflow_state", "update_stock", "company"], as_dict=True)


# ─── does the voucher still match the invoice? ────────────────────────────────────────────────


def invoice_rows(invoice) -> list:
	"""The invoice rows a landed cost can be spread over: stock and fixed-asset items, as core reads them."""
	rows = []
	for row in invoice.get("items") or []:
		if not row.get("item_code"):
			continue
		stock, asset = frappe.get_cached_value("Item", row.item_code, ["is_stock_item", "is_fixed_asset"]) or (0, 0)
		if cint(stock) or cint(asset):
			rows.append(row)
	return rows


def differences(voucher, invoice) -> list:
	"""What differs between a draft voucher and the invoice it was drawn from (empty when they agree)."""
	out = []
	if voucher.get("company") != invoice.get("company"):
		out.append(_("the company is %s, the invoice's is %s") % (voucher.get("company"), invoice.get("company")))
	rows = invoice_rows(invoice)
	if any(not row.get("name") for row in rows):
		out.append(_("the invoice has rows that are not saved yet"))
	named = {row.name: row for row in rows if row.get("name")}
	have = {item.get("purchase_receipt_item"): item for item in voucher.get("items") or []}
	for key, row in named.items():
		item = have.get(key)
		if not item:
			out.append(_("row %s (%s) is not on the voucher") % (row.idx, row.item_code))
		elif flt(item.get("qty")) != flt(row.qty) or abs(flt(item.get("amount")) - flt(row.base_amount)) > TOLERANCE:
			out.append(
				_("row %s (%s) is %s for %s on the voucher, %s for %s on the invoice")
				% (row.idx, row.item_code, flt(item.get("qty")), flt(item.get("amount")), flt(row.qty), flt(row.base_amount))
			)
	for key, item in have.items():
		if key not in named:
			out.append(_("%s is no longer on the invoice") % item.get("item_code"))
	return out


def preflight(voucher_name, invoice) -> list:
	"""Everything the voucher's own submit would refuse, found now and without writing anything."""
	voucher = frappe.get_doc(VOUCHER, voucher_name)
	diff = differences(voucher, invoice)
	if diff:
		return [_("it does not match this invoice yet: %s") % "; ".join(diff)]
	voucher.flags[SYSTEM_FLAG] = True
	try:
		voucher.run_method("validate")
		voucher.run_method("before_submit")
		voucher.validate_applicable_charges_for_item()
	except frappe.ValidationError as e:
		return [frappe.utils.strip_html(cstr(e)).strip()]
	if flt(voucher.total_taxes_and_charges) <= 0:
		return [_("it carries no charges")]
	return []


def _refuse_voucher(voucher_name, problems):
	frappe.throw(
		_("Landed Cost Voucher %s cannot be submitted with this invoice: %s. Fix the voucher, then try again.")
		% (_link(VOUCHER, voucher_name), "; ".join(problems)),
		title=_("Landed Cost Voucher Not Ready"),
	)


def _require_voucher_message():
	return _(
		"This import purchase needs a Landed Cost Voucher before it can go for approval. Use Create > Landed Cost "
		"Voucher on the invoice, enter the charges and save it. If no landed cost applies, tick Import Without "
		"Landed Cost and say why."
	)


# ─── Purchase Invoice hooks ───────────────────────────────────────────────────────────────────


def validate_invoice(doc, method=None):
	"""validate: the opt-out is explained; a draft voucher is not orphaned; the gate to approval."""
	if doc.doctype != INVOICE or cint(doc.docstatus) != 0:
		return
	if cint(doc.get(ESCAPE_FIELD)) and not cstr(doc.get(REASON_FIELD)).strip():
		frappe.throw(_("Say why this import carries no landed cost."), title=_("Reason Required"))
	if _is_new(doc):
		return
	before = doc.get_doc_before_save()
	was_stock = cint(before.get("update_stock")) if before else 0
	if not (was_stock or cint(doc.get("update_stock"))):
		return
	drafts = vouchers_of(doc.name, 0)
	if drafts:
		names = ", ".join(v.name for v in drafts)
		if not cint(doc.get("update_stock")):
			frappe.throw(
				_(
					"Landed Cost Voucher %s is drawn up against this invoice, which would no longer update stock. A landed "
					"cost goes into stock: delete the voucher, or raise it against the Purchase Receipt instead."
				)
				% names,
				title=_("Landed Cost Voucher Attached"),
			)
		if has_asset_rows(doc):
			frappe.throw(
				_(
					"Landed Cost Voucher %s cannot follow an invoice that carries fixed-asset rows: core needs the asset "
					"made first. Delete the voucher and raise it after the invoice is submitted."
				)
				% names,
				title=_("Fixed Assets"),
			)
		if cint(doc.get(ESCAPE_FIELD)):
			frappe.throw(
				_(
					"Landed Cost Voucher %s is drawn up against this invoice, which is marked as carrying no landed cost. "
					"Delete the voucher or untick Import Without Landed Cost."
				)
				% names,
				title=_("Landed Cost Voucher Attached"),
			)
	old_state = cstr(before.get("workflow_state")).strip() if before else ""
	if is_locked(state_of(doc)) and not is_locked(old_state):
		# the save that sends it for approval
		if must_have_voucher(doc) and not drafts:
			frappe.throw(_require_voucher_message(), title=_("Landed Cost Voucher Required"))
		for voucher in drafts:
			problems = preflight(voucher.name, doc)
			if problems:
				_refuse_voucher(voucher.name, problems)


def follow_invoice(doc, method=None):
	"""on_update: the invoice was changed while a draft -- bring each draft voucher in line with it."""
	if doc.doctype != INVOICE or cint(doc.docstatus) != 0 or not cint(doc.get("update_stock")):
		return
	for row in vouchers_of(doc.name, 0):
		voucher = frappe.get_doc(VOUCHER, row.name)
		diff = differences(voucher, doc)
		if diff:
			refresh_voucher(voucher, doc, diff)


def refresh_voucher(voucher, invoice, diff=None):
	"""Rebuild the voucher's items from the invoice, keep its charges, and say so."""
	voucher.flags[SYSTEM_FLAG] = True
	voucher.flags.ignore_permissions = True
	manual = voucher.distribute_charges_based_on == "Distribute Manually"
	kept = {i.purchase_receipt_item: flt(i.applicable_charges) for i in voucher.get("items") or []}
	voucher.company = invoice.company
	voucher.posting_date = invoice.posting_date
	for row in voucher.get("purchase_receipts") or []:
		row.supplier = invoice.supplier
		row.posting_date = invoice.posting_date
		row.grand_total = invoice.base_grand_total
	voucher.get_items_from_purchase_receipts()
	if manual:
		if {i.purchase_receipt_item for i in voucher.get("items")} != set(kept):
			frappe.throw(
				_(
					"Landed Cost Voucher %s spreads its charges by hand, and the invoice's rows have changed, so it cannot "
					"follow on its own. Open the voucher, redistribute the charges and save it, then save the invoice again."
				)
				% _link(VOUCHER, voucher.name),
				title=_("Landed Cost Voucher Out of Step"),
			)
		for item in voucher.get("items"):
			item.applicable_charges = kept[item.purchase_receipt_item]
	try:
		voucher.save()
	except frappe.ValidationError as e:
		frappe.throw(
			_("Landed Cost Voucher %s could not follow this change: %s")
			% (_link(VOUCHER, voucher.name), frappe.utils.strip_html(cstr(e)).strip()),
			title=_("Landed Cost Voucher Out of Step"),
		)
	_tell(voucher, invoice, diff)


def _tell(voucher, invoice, diff):
	text = _(
		"Purchase Invoice %s changed (%s), so Landed Cost Voucher %s was brought in line with it. Check its charges."
	) % (invoice.name, "; ".join(diff or [_("rows changed")]), voucher.name)
	voucher.add_comment("Info", text)
	frappe.msgprint(text, title=_("Landed Cost Voucher Updated"), indicator="orange")
	owner = voucher.owner
	if owner and owner not in (frappe.session.user, "Administrator"):
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": _("Landed Cost Voucher %s was updated with its invoice") % voucher.name,
				"for_user": owner,
				"document_type": VOUCHER,
				"document_name": voucher.name,
				"type": "Alert",
				"from_user": frappe.session.user,
				"email_content": text,
			}
		).insert(ignore_permissions=True)


def require_voucher_at_submit(doc, method=None):
	"""before_submit: the same check for an invoice submitted without a workflow, or whose voucher was lost."""
	if doc.doctype != INVOICE or cint(doc.get("is_return")):
		return
	drafts = vouchers_of(doc.name, 0)
	if must_have_voucher(doc) and not drafts:
		frappe.throw(_require_voucher_message(), title=_("Landed Cost Voucher Required"))
	for voucher in drafts:
		problems = preflight(voucher.name, doc)
		if problems:
			_refuse_voucher(voucher.name, problems)


def submit_vouchers(doc, method=None):
	"""on_submit: the invoice's stock and ledger entries are posted; now its draft vouchers are submitted."""
	if doc.doctype != INVOICE:
		return
	drafts = vouchers_of(doc.name, 0)
	if not drafts:
		return
	# core reads the invoice's docstatus through the document cache, which still holds the draft
	frappe.clear_document_cache(INVOICE, doc.name)
	done = []
	for row in drafts:
		voucher = frappe.get_doc(VOUCHER, row.name)
		voucher.flags.ignore_permissions = True
		voucher.flags[SYSTEM_FLAG] = True
		voucher.submit()
		done.append(row.name)
	frappe.msgprint(
		_("Landed Cost Voucher %s was submitted with this invoice.") % ", ".join(done),
		alert=True,
		indicator="green",
	)


def cancel_vouchers(doc, method=None):
	"""before_cancel: cancel the invoice's submitted vouchers first, so one cancellation is enough."""
	if doc.doctype != INVOICE:
		return
	done = []
	for row in vouchers_of(doc.name, 1):
		if not names_one_document(row.name):
			continue  # a voucher shared with other documents is core's to refuse
		voucher = frappe.get_doc(VOUCHER, row.name)
		voucher.flags.ignore_permissions = True
		voucher.flags[SYSTEM_FLAG] = True
		voucher.cancel()
		done.append(row.name)
	if done:
		frappe.msgprint(
			_(
				"Landed Cost Voucher %s was cancelled with this invoice. Amend the invoice and raise the voucher again if "
				"the purchase is to be redone."
			)
			% ", ".join(done),
			indicator="orange",
		)


def delete_vouchers(doc, method=None):
	"""on_trash: a draft invoice that is deleted takes its draft vouchers with it."""
	if doc.doctype != INVOICE:
		return
	for row in vouchers_of(doc.name, 0):
		frappe.flags[DELETING_FLAG] = True
		try:
			frappe.delete_doc(VOUCHER, row.name, force=True, ignore_permissions=True)
		finally:
			frappe.flags.pop(DELETING_FLAG, None)


# ─── Landed Cost Voucher hooks ────────────────────────────────────────────────────────────────


def _draft_invoices(voucher) -> list:
	out = []
	for row in voucher.get("purchase_receipts") or []:
		if row.get("receipt_document_type") == INVOICE and row.get("receipt_document"):
			if cint(frappe.db.get_value(INVOICE, row.receipt_document, "docstatus")) == 0:
				out.append(row.receipt_document)
	return out


def validate_voucher(doc, method=None):
	"""validate: a voucher drawn up against a draft invoice names that invoice alone, and is locked once the
	invoice is out for approval."""
	if doc.doctype != VOUCHER or cint(doc.docstatus) != 0:
		return
	drafts = _draft_invoices(doc)
	if not drafts:
		return
	if len(doc.get("purchase_receipts") or []) != 1:
		frappe.throw(
			_(
				"A Landed Cost Voucher drawn up against a draft Purchase Invoice names that invoice alone. A voucher "
				"that spreads over several documents can only be made against submitted ones."
			),
			title=_("One Invoice Only"),
		)
	if doc.flags.get(SYSTEM_FLAG):
		return
	info = invoice_info(drafts[0])
	if info and is_locked(info.workflow_state):
		frappe.throw(
			_(
				"Purchase Invoice %s is out for approval (%s), so its landed cost is part of what is being approved and "
				"cannot be changed now. Reject the invoice to change it."
			)
			% (_link(INVOICE, drafts[0]), info.workflow_state),
			title=_("Locked For Approval"),
		)


def guard_voucher_trash(doc, method=None):
	"""on_trash: a voucher of an invoice that is out for approval is not deleted from under it."""
	if doc.doctype != VOUCHER or cint(doc.docstatus) != 0 or frappe.flags.get(DELETING_FLAG):
		return
	name = invoice_of(doc)
	info = invoice_info(name) if name else None
	if info and cint(info.docstatus) == 0 and is_locked(info.workflow_state):
		frappe.throw(
			_("Purchase Invoice %s is out for approval (%s) and needs this voucher. Reject the invoice first.")
			% (_link(INVOICE, name), info.workflow_state),
			title=_("Locked For Approval"),
		)


def remind_after_cancel(doc, method=None):
	"""on_cancel: a voucher cancelled on its own leaves the invoice without that landed cost -- say so."""
	if doc.doctype != VOUCHER or doc.flags.get(SYSTEM_FLAG):
		return
	name = invoice_of(doc)
	info = invoice_info(name) if name else None
	if not info or cint(info.docstatus) != 1:
		return
	frappe.msgprint(
		_(
			"Purchase Invoice %s now carries no landed cost from %s. Amend this voucher, or raise a new one against the "
			"invoice, so the stock is valued at its landed cost."
		)
		% (_link(INVOICE, name), doc.name),
		title=_("Landed Cost Removed"),
		indicator="orange",
	)


# ─── what the forms ask ───────────────────────────────────────────────────────────────────────


@frappe.whitelist()
def make_landed_cost_voucher(source_name: str, target_doc=None):
	"""Create > Landed Cost Voucher on a draft stock invoice: an unsaved voucher naming it, with the charges
	of the voucher it replaces when the invoice is an amendment."""
	invoice = frappe.get_doc(INVOICE, source_name)
	invoice.check_permission("read")
	if cint(invoice.docstatus) != 0:
		frappe.throw(_("A voucher is drawn up against a draft invoice. For a submitted one, use a Landed Cost Voucher directly."))
	if cint(invoice.is_return):
		frappe.throw(_("A return carries no landed cost."))
	if not cint(invoice.update_stock):
		frappe.throw(
			_(
				"This invoice does not update stock, so a landed cost cannot be put into it. Tick Update Stock, or raise "
				"the voucher against the Purchase Receipt."
			)
		)
	if has_asset_rows(invoice):
		frappe.throw(
			_(
				"This invoice carries fixed-asset rows: core needs the asset made first, so raise the voucher after the "
				"invoice is submitted."
			)
		)
	if is_locked(state_of(invoice)):
		frappe.throw(_("This invoice is out for approval (%s). Reject it to change its landed cost.") % state_of(invoice))
	voucher = frappe.new_doc(VOUCHER)
	voucher.company = invoice.company
	voucher.posting_date = invoice.posting_date
	voucher.distribute_charges_based_on = "Qty"
	voucher.append(
		"purchase_receipts",
		{
			"receipt_document_type": INVOICE,
			"receipt_document": invoice.name,
			"supplier": invoice.supplier,
			"posting_date": invoice.posting_date,
			"grand_total": invoice.base_grand_total,
		},
	)
	voucher.get_items_from_purchase_receipts()
	if invoice.get("amended_from"):
		earlier = vouchers_of(invoice.amended_from, 2)
		if earlier:
			old = frappe.get_doc(VOUCHER, earlier[-1].name)
			voucher.distribute_charges_based_on = old.distribute_charges_based_on
			for charge in old.get("taxes") or []:
				voucher.append(
					"taxes",
					{
						"description": charge.description,
						"expense_account": charge.expense_account,
						"amount": charge.amount,
						"custom_expense_doctype": charge.get("custom_expense_doctype"),
						"custom_expense_entry": charge.get("custom_expense_entry"),
					},
				)
	return voucher


@frappe.whitelist()
def invoice_status(invoice: str) -> dict:
	"""For the invoice form: is a voucher required, which exist, and do they still match."""
	frappe.has_permission(INVOICE, "read", doc=invoice, throw=True)
	doc = frappe.get_doc(INVOICE, invoice)
	vouchers = vouchers_of(invoice)
	stale = {}
	if cint(doc.docstatus) == 0:
		for row in vouchers:
			if row.docstatus == 0:
				diff = differences(frappe.get_doc(VOUCHER, row.name), doc)
				if diff:
					stale[row.name] = diff
	return {
		"required": must_have_voucher(doc),
		"is_import": is_import(doc),
		"locked": is_locked(state_of(doc)),
		"vouchers": [{"name": v.name, "docstatus": v.docstatus, "total": flt(v.total_taxes_and_charges)} for v in vouchers],
		"stale": stale,
	}


@frappe.whitelist()
def voucher_status(voucher: str) -> dict:
	"""For the voucher form: the draft invoice it waits for, and whether it still matches it."""
	frappe.has_permission(VOUCHER, "read", doc=voucher, throw=True)
	doc = frappe.get_doc(VOUCHER, voucher)
	name = invoice_of(doc)
	if not name:
		return {}
	info = invoice_info(name)
	out = {
		"invoice": name,
		"invoice_docstatus": cint(info.docstatus) if info else None,
		"state": cstr(info.workflow_state) if info else "",
		"locked": bool(info and is_locked(info.workflow_state)),
		"stale": [],
	}
	if info and cint(info.docstatus) == 0 and cint(doc.docstatus) == 0:
		out["stale"] = differences(doc, frappe.get_doc(INVOICE, name))
	return out


@frappe.whitelist()
def refresh_from_invoice(voucher: str) -> list:
	"""Button on a draft voucher: rebuild its items from the invoice it waits for."""
	doc = frappe.get_doc(VOUCHER, voucher)
	doc.check_permission("write")
	name = invoice_of(doc)
	if not name or cint(doc.docstatus) != 0:
		frappe.throw(_("Only a draft voucher drawn up against one invoice can be refreshed."))
	invoice = frappe.get_doc(INVOICE, name)
	if cint(invoice.docstatus) != 0:
		frappe.throw(_("The invoice is already submitted."))
	if is_locked(state_of(invoice)):
		frappe.throw(_("The invoice is out for approval. Reject it to change its landed cost."))
	diff = differences(doc, invoice)
	if diff:
		refresh_voucher(doc, invoice, diff)
	return diff
