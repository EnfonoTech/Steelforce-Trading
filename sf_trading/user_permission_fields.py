# sf_trading/user_permission_fields.py
"""A user permission should gate the dimension a document belongs to, not every field that happens
to name a Cost Center or a Warehouse.

Frappe builds its list filter as one AND-ed condition per **parent** Link field pointing at a
doctype the user is restricted on, and skips only a field carrying `ignore_user_permissions`
(v15, `frappe/model/db_query.py`: `if df.get("ignore_user_permissions"): continue`). So a field
the site never fills in deliberately still decides who may see the document -- and it is filled in
by nobody: a User Permission marked `is_default` becomes a **document default**, so whichever
branch the person raising the document belongs to lands in every otherwise-unset Cost Center or
Warehouse link on it.

That is how Sales Invoice `20010005897` -- `branch = SFSB`, `cost_center = SFSB - SFB`, every
figure right -- became invisible to every SFSB user: `write_off_cost_center` and
`loyalty_redemption_cost_center` both held `SFSS - SFB`, inherited from the SFSS salesman who
raised it, on a site that has no POS Profile and no Loyalty Program and therefore never reads
either field. Eight documents carried the mismatch and the accounting was untouched in all eight
(`write_off_amount` and `loyalty_amount` are zero on every one). The eight are repaired by
`sf_trading.patches.v0_1.align_secondary_cost_centres`; this module is what stops the ninth.

The rule is deliberately narrow:

* only `Cost Center` and `Warehouse` links are considered. Company, Branch, Customer, Supplier,
  Sales Person and every other restricted doctype keep enforcing exactly as configured.
* only **submittable** doctypes -- the transactions people list -- are touched, never masters.
  A master's own fields are chosen by hand, not inherited from whoever opened the form.
* the fields that genuinely say where a document belongs (`ENFORCING`) keep enforcing.
* a field already carrying the flag is left alone, whoever set it. The Property Setters this site
  ships for Stock Entry's warehouses were somebody's decision and are not re-made here.

It runs on `after_migrate`, so a field a later ERPNext version introduces cannot quietly re-open
the hole. The Property Setters are created in code rather than shipped as fixtures because the set
is discovered per site rather than enumerated -- a site with different apps installed has a
different list.
"""

import frappe

# The restricted doctypes whose *secondary* fields are the hazard. Both are dimensions that a user
# permission legitimately gates on one field of a document and accidentally on several others.
GATED_DOCTYPES = ("Cost Center", "Warehouse")

# The fields that really do carry the document's dimension. These keep enforcing: a document whose
# own cost centre or source warehouse belongs to another branch SHOULD be hidden.
ENFORCING = frozenset(
	{
		"cost_center",
		"warehouse",
		"set_warehouse",
		"set_target_warehouse",
	}
)

FLAG = "ignore_user_permissions"


def secondary_fields() -> list[frappe._dict]:
	"""Parent Link fields on submittable doctypes that gate on a dimension they do not own.

	Returns only the ones still enforcing -- a field already flagged, on the DocField itself or
	through a Property Setter, is nobody's problem and is not returned.
	"""
	doctype = frappe.qb.DocType("DocType")
	docfield = frappe.qb.DocType("DocField")
	custom = frappe.qb.DocType("Custom Field")

	# a transaction people list: submittable, a table of its own, not a Single
	listed = (doctype.is_submittable == 1) & (doctype.istable == 0) & (doctype.issingle == 0)

	def gating(table):
		return (
			(table.fieldtype == "Link")
			& table.options.isin(GATED_DOCTYPES)
			& (getattr(table, FLAG) == 0)
		)

	standard = (
		frappe.qb.from_(docfield)
		.join(doctype)
		.on(doctype.name == docfield.parent)
		.select(docfield.parent.as_("doctype"), docfield.fieldname, docfield.options)
		.where(gating(docfield) & listed)
	).run(as_dict=True)

	customised = (
		frappe.qb.from_(custom)
		.join(doctype)
		.on(doctype.name == custom.dt)
		.select(
			custom.dt.as_("doctype"),
			custom.fieldname,
			custom.options,
			custom.name.as_("custom_field"),
		)
		.where(gating(custom) & listed)
	).run(as_dict=True)

	rows = [row for row in standard + customised if row.fieldname not in ENFORCING]
	return [row for row in rows if not is_flagged(row.doctype, row.fieldname)]


def is_flagged(doctype: str, fieldname: str) -> bool:
	"""Whether a Property Setter already turns the flag on for this field."""
	return bool(
		frappe.db.exists(
			"Property Setter",
			{
				"doc_type": doctype,
				"field_name": fieldname,
				"property": FLAG,
				"value": ("in", ("1", 1)),
			},
		)
	)


def apply() -> dict:
	"""after_migrate: stop a secondary Cost Center / Warehouse field from deciding visibility."""
	marked = []
	for row in secondary_fields():
		if row.get("custom_field"):
			# a Custom Field owns the property itself; a Property Setter on top of one is a
			# second place to look when somebody later asks why the field behaves as it does
			frappe.db.set_value("Custom Field", row.custom_field, FLAG, 1)
		else:
			frappe.make_property_setter(
				{
					"doctype": row.doctype,
					"fieldname": row.fieldname,
					"property": FLAG,
					"value": 1,
					"property_type": "Check",
				},
				# a deliberate customisation of this site, not framework bookkeeping: it should
				# read as such in Customize Form and survive the same way the app's others do
				is_system_generated=False,
			)
		frappe.clear_cache(doctype=row.doctype)
		marked.append(f"{row.doctype}.{row.fieldname}")

	if marked:
		frappe.logger("sf_trading").info(f"{FLAG} set on: {', '.join(sorted(marked))}")
	return {"marked": sorted(marked)}
