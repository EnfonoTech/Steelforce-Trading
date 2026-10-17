# apps/sf_trading/sf_trading/report/items_without_price_list/items_without_price_list.py
"""Items Without Price List - which items a price list prices, which it does not, and where else they are priced.

An item is **priced** in a list when that list holds an Item Price for it that is

  * general -- not one made for a single customer (selling) or supplier (buying),
  * above zero,
  * valid on the "As On Date": inside its Valid From / Valid Upto, when it has them, and
  * for the branch chosen, when one is: an Item Price may name the branches it serves
    (Applicable Branches, sf_trading/item_price_branch.py); with no Branch filter any such price counts
    and the Applicable Branches column says whom it serves.

"Show" picks the side: Without Price (the default), With Price, or All Items. The Status column says
why an item is unpriced, because the fixes differ:

  Priced                   a valid price exists (what With Price and All Items add)
  No Item Price            the list has no row for the item at all
  Zero Rate                rows exist, every one of them at zero
  Expired / Not Yet Valid  a row above zero exists, but the date is outside its validity

The Status filter, when set, wins over "Show".

The Company filter keeps the company's items (an Item Default row for it, see company_items.py) and
reads stock from, and last sold / purchased rates and "Sold / Purchased Since" against, that company's
warehouses and invoices only.

The price list checked is, in order: the Price List filter; the branch's default list when a Branch
is chosen; the site default (Selling Settings / Buying Settings). The message line says which.

**Current Price Lists** is the other half of the ask: every enabled list that DOES price the item
today, with the rate -- so a gap in one list that another list fills reads at a glance, and an item
with nothing there is priced nowhere. "Not Priced in Any Price List" keeps only those.

The cost columns are there to price the gap with: Valuation Rate (moving average; the last known rate
while nothing is in stock), Last Purchase Rate, the minimum selling price the cost guard would hold
the item to (last purchase or valuation, whichever is higher, plus its Item Group's margin), and the
last rate it actually sold or was bought at.

Branch decides which prices count (above), the default list, and the stock figures, which then count
only that branch's warehouses (Branch Configuration). Items themselves are not branch-bound.

Every by-name lookup is fetched in slices of BATCH codes. One `IN` list of thousands of codes trips
the query parser's token cap ("Maximum number of tokens exceeded") on exactly the widest selections.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from sf_trading import branch_price_list, item_price_branch

# Most of the catalogue can be unpriced (16,748 items against 2,926 priced on the first run), so the
# cap is generous -- and the order puts what is in stock first, so a cut-off loses the dead items.
ROW_CAP = 10000
BATCH = 500

STATUS_PRICED = "Priced"
STATUS_NONE = "No Item Price"
STATUS_ZERO = "Zero Rate"
STATUS_EXPIRED = "Expired / Not Yet Valid"

# SQL fragments. Only literals are joined into a query; every value travels as a parameter.
GENERAL_PRICE = (
	"p.item_code = it.name and p.price_list = %(price_list)s "
	"and ifnull(p.customer, '') = '' and ifnull(p.supplier, '') = ''"
)
VALID_PRICE = (
	"p.price_list_rate > 0 and (p.valid_from is null or p.valid_from <= %(on_date)s) "
	"and (p.valid_upto is null or p.valid_upto >= %(on_date)s)"
)
# a price that names no branch serves every branch; one that names branches serves only those
BRANCH_SCOPE = (
	" and (not exists (select 1 from `tabItem Price Branch` pb where pb.parent = p.name and pb.parenttype = 'Item Price')"
	" or exists (select 1 from `tabItem Price Branch` pb where pb.parent = p.name and pb.parenttype = 'Item Price'"
	" and pb.branch = %(branch)s))"
)


def price_rows(branch) -> str:
	"""The Item Price rows of the checked list, as a branch sees them (every row when no branch is chosen)."""
	sql = "select 1 from `tabItem Price` p where " + GENERAL_PRICE
	if branch and item_price_branch.table_ready():
		sql += BRANCH_SCOPE
	return sql


def execute(filters=None):
	filters = frappe._dict(filters or {})
	kind = "buying" if filters.price_type == "Buying" else "selling"
	on_date = getdate(filters.as_on_date or nowdate())

	price_list = resolve_price_list(filters, kind)
	warehouses = scope_warehouses(filters.company, filters.branch)

	items = fetch_items(filters, kind, price_list, on_date, warehouses)
	truncated = len(items) > ROW_CAP
	items = items[:ROW_CAP]

	data = build_rows(items, kind, price_list, on_date, warehouses, filters.branch, filters.company)

	message = _("Checked against price list %s as on %s.") % (frappe.bold(price_list), on_date)
	if filters.company:
		message += " " + _("Company %s.") % frappe.bold(filters.company)
	if filters.branch:
		message += " " + _("Branch %s.") % frappe.bold(filters.branch)
	if truncated:
		message += " " + _("Showing the first %s items; narrow the filters to see the rest.") % ROW_CAP

	return get_columns(kind), data, message, None, get_summary(data)


# ─── Which list is being checked ─────────────────────────────────────────────

def resolve_price_list(filters, kind: str) -> str:
	"""The list the report checks, and a refusal when none can be found or it serves the other side."""
	chosen = filters.price_list
	if not chosen and filters.branch:
		chosen = branch_price_list.default_price_list(filters.branch, kind)
	if not chosen:
		if kind == "selling":
			chosen = frappe.db.get_single_value("Selling Settings", "selling_price_list")
		else:
			chosen = frappe.db.get_single_value("Buying Settings", "buying_price_list")
	if not chosen:
		frappe.throw(
			_("No price list to check against. Pick one in the Price List filter."),
			title=_("Price List Needed"),
		)

	flags = frappe.db.get_value("Price List", chosen, ["enabled", kind], as_dict=True)
	if not flags:
		frappe.throw(_("Price list %s does not exist.") % frappe.bold(chosen))
	if not flags[kind]:
		frappe.throw(
			_("%s is not a %s price list. Change Price Type, or pick another list.")
			% (frappe.bold(chosen), _(kind)),
			title=_("Wrong Price Type"),
		)
	return chosen


def branch_warehouses(branch):
	"""The branch's warehouses, groups opened up; None when no branch is chosen or none is configured."""
	if not branch:
		return None

	roots = frappe.get_all(
		"Branch Configuration Warehouse",
		filters={"parent": branch, "parenttype": "Branch Configuration"},
		pluck="warehouse",
		ignore_permissions=True,
	)
	names = set()
	for root in roots:
		bounds = frappe.db.get_value("Warehouse", root, ["lft", "rgt"], as_dict=True)
		if not bounds:
			continue
		names.update(
			frappe.get_all(
				"Warehouse",
				filters={"lft": [">=", bounds.lft], "rgt": ["<=", bounds.rgt]},
				pluck="name",
				ignore_permissions=True,
			)
		)
	return tuple(sorted(names)) or None


NO_WAREHOUSE = ("-",)


def scope_warehouses(company, branch):
	"""The warehouses stock is read from: the company's, narrowed to the branch's when a branch is chosen.

	None means no restriction. A company with no warehouse of its own is not "unrestricted" but "holds
	nothing", which NO_WAREHOUSE says (an empty IN list is not valid SQL).
	"""
	of_branch = branch_warehouses(branch)
	if not company:
		return of_branch

	of_company = set(
		frappe.get_all("Warehouse", filters={"company": company, "is_group": 0}, pluck="name", ignore_permissions=True)
	)
	if of_branch:
		of_company &= set(of_branch)
	return tuple(sorted(of_company)) or NO_WAREHOUSE


# ─── Finding the items ───────────────────────────────────────────────────────

def fetch_items(filters, kind: str, price_list: str, on_date, warehouses) -> list:
	values = {"price_list": price_list, "on_date": on_date, "cap": ROW_CAP + 1, "branch": filters.branch or ""}
	conditions = ["it.is_fixed_asset = 0", "it.has_variants = 0"]

	if not cint(filters.include_disabled):
		conditions.append("it.disabled = 0")
	conditions.append("it.is_sales_item = 1" if kind == "selling" else "it.is_purchase_item = 1")

	item_type = filters.item_type or "Stock Items"
	if item_type == "Stock Items":
		conditions.append("it.is_stock_item = 1")
	elif item_type == "Services / Non-Stock":
		conditions.append("it.is_stock_item = 0")

	if filters.company:
		conditions.append(
			"exists (select 1 from `tabItem Default` idf where idf.parent = it.name and idf.parenttype = 'Item' and idf.company = %(company)s)"
		)
		values["company"] = filters.company
	if filters.item_code:
		conditions.append("it.name = %(item_code)s")
		values["item_code"] = filters.item_code
	if filters.brand:
		conditions.append("it.brand = %(brand)s")
		values["brand"] = filters.brand
	if filters.item_group:
		bounds = frappe.db.get_value("Item Group", filters.item_group, ["lft", "rgt"], as_dict=True)
		if not bounds:
			frappe.throw(_("Item Group %s does not exist.") % frappe.bold(filters.item_group))
		conditions.append(
			"it.item_group in (select ig.name from `tabItem Group` ig where ig.lft >= %(lft)s and ig.rgt <= %(rgt)s)"
		)
		values.update({"lft": bounds.lft, "rgt": bounds.rgt})

	# priced or not, and which kind of gap. A Status, when chosen, decides; otherwise "Show" does.
	rows = price_rows(filters.branch)
	priced = "exists (" + rows + " and " + VALID_PRICE + ")"
	unpriced = "not exists (" + rows + " and " + VALID_PRICE + ")"
	if filters.status == STATUS_PRICED:
		conditions.append(priced)
	elif filters.status == STATUS_NONE:
		conditions.append(unpriced)
		conditions.append("not exists (" + rows + ")")
	elif filters.status == STATUS_ZERO:
		conditions.append(unpriced)
		conditions.append("exists (" + rows + ") and not exists (" + rows + " and p.price_list_rate > 0)")
	elif filters.status == STATUS_EXPIRED:
		conditions.append(unpriced)
		conditions.append("exists (" + rows + " and p.price_list_rate > 0)")
	elif filters.show == "With Price":
		conditions.append(priced)
	elif filters.show != "All Items":
		conditions.append(unpriced)

	if cint(filters.no_price_anywhere):
		conditions.append(
			"not exists (select 1 from `tabItem Price` p join `tabPrice List` pl on pl.name = p.price_list "
			"where p.item_code = it.name and pl.enabled = 1 and pl." + kind + " = 1 "
			"and ifnull(p.customer, '') = '' and ifnull(p.supplier, '') = '' and " + VALID_PRICE + ")"
		)

	if cint(filters.only_in_stock):
		bin_check = "exists (select 1 from `tabBin` b where b.item_code = it.name and b.actual_qty > 0"
		if warehouses:
			bin_check += " and b.warehouse in %(warehouses)s"
		conditions.append(bin_check + ")")

	if filters.moved_since:
		if kind == "selling":
			moved = (
				"exists (select 1 from `tabSales Invoice Item` mi join `tabSales Invoice` mh on mh.name = mi.parent "
				"where mi.item_code = it.name and mh.docstatus = 1 and mh.posting_date >= %(moved_since)s"
			)
		else:
			moved = (
				"exists (select 1 from `tabPurchase Invoice Item` mi join `tabPurchase Invoice` mh on mh.name = mi.parent "
				"where mi.item_code = it.name and mh.docstatus = 1 and mh.posting_date >= %(moved_since)s"
			)
		if filters.company:
			moved += " and mh.company = %(company)s"
		conditions.append(moved + ")")
		values["moved_since"] = getdate(filters.moved_since)

	# what is in stock first, then by group: a cut-off then loses the items nobody holds
	in_stock = "(select ifnull(sum(ob.actual_qty), 0) > 0 from `tabBin` ob where ob.item_code = it.name"
	if warehouses:
		in_stock += " and ob.warehouse in %(warehouses)s"
		values["warehouses"] = warehouses
	in_stock += ")"

	return frappe.db.sql(
		"select it.name as item_code, it.item_name, it.item_group, it.brand, it.stock_uom, it.disabled, "
		"it.last_purchase_rate, it.valuation_rate as item_valuation_rate "
		"from `tabItem` it where " + " and ".join(conditions)
		+ " order by " + in_stock + " desc, it.item_group, it.name limit %(cap)s",
		values,
		as_dict=True,
	)


# ─── Filling the rows in ─────────────────────────────────────────────────────

def _batches(seq: list):
	for start in range(0, len(seq), BATCH):
		yield seq[start : start + BATCH]


def is_valid(row, on_date) -> bool:
	"""A price above zero whose dates (when it has them) take in `on_date`."""
	if flt(row.price_list_rate) <= 0:
		return False
	if row.valid_from and getdate(row.valid_from) > on_date:
		return False
	if row.valid_upto and getdate(row.valid_upto) < on_date:
		return False
	return True


def status_for(rows: list, on_date) -> str:
	"""Where an item stands in the checked list, from its rows there."""
	if not rows:
		return STATUS_NONE
	if any(is_valid(row, on_date) for row in rows):
		return STATUS_PRICED
	if not any(flt(row.price_list_rate) > 0 for row in rows):
		return STATUS_ZERO
	return STATUS_EXPIRED


def chosen_price(rows: list, on_date):
	"""The row an item is priced from in this list (what ERPNext would use), else the one that lapsed last.

	`rows` arrive in the order ERPNext prefers them (newest valid-from first, the branch's own ahead of the
	general price when a branch was chosen), so the first valid row is the answer.
	"""
	for row in rows:
		if is_valid(row, on_date):
			return row
	lapsed = [row for row in rows if flt(row.price_list_rate) > 0]
	if not lapsed:
		return None
	return max(lapsed, key=lambda row: str(row.valid_upto or row.valid_from or ""))


def current_lists_text(entries: list) -> str:
	"""'Offer Price: 8.800; Wholesale: 7.500' -- each list once, in name order."""
	seen = {}
	for price_list, rate in entries:
		seen.setdefault(price_list, flt(rate))
	return "; ".join("%s: %s" % (name, ("%.3f" % rate)) for name, rate in sorted(seen.items()))


def _margin_for_group(item_group, memo: dict) -> float:
	"""The nearest Item Group margin up the tree (the same walk the selling-price guard does)."""
	start = item_group
	visited = []
	margin = 0.0
	while item_group and item_group not in visited:
		if item_group in memo:
			margin = memo[item_group]
			break
		visited.append(item_group)
		pct = flt(frappe.db.get_value("Item Group", item_group, "custom_min_margin_pct"))
		if pct:
			margin = pct
			break
		item_group = frappe.db.get_value("Item Group", item_group, "parent_item_group")
	for name in visited:
		memo[name] = margin
	memo[start] = margin
	return margin


def build_rows(items: list, kind: str, price_list: str, on_date, warehouses, branch=None, company=None) -> list:
	codes = [item.item_code for item in items]
	own_rows = _own_list_rows(codes, price_list, branch)
	current = _current_lists(codes, kind, on_date, branch)
	stock = _stock(codes, warehouses)
	last = _last_movement(codes, kind, company)
	margins = {}
	scopes = item_price_branch.branches_by_price(
		[row.get("name") for rows in own_rows.values() for row in rows if row.get("name")]
	)

	data = []
	for item in items:
		rows = own_rows.get(item.item_code, [])
		held = stock.get(item.item_code) or {}
		qty = flt(held.get("qty"))
		if qty > 0:
			valuation = flt(held.get("value")) / qty
		else:
			valuation = flt(held.get("rate")) or flt(item.item_valuation_rate)

		floor = 0.0
		if kind == "selling":
			cost = max(flt(item.last_purchase_rate), valuation)
			if cost:
				floor = cost * (1 + _margin_for_group(item.item_group, margins) / 100.0)

		if not branch and scopes:
			# no Branch filter: a document with no branch is priced from the general price, so that is the one
			# shown when there is one; a price limited to some branches is shown only when there is no other
			rows = sorted(rows, key=lambda row: bool(scopes.get(row.get("name"))))

		price = chosen_price(rows, on_date)
		status = status_for(rows, on_date)
		served = sorted(scopes.get(price.get("name"), ())) if price else []
		lists = current.get(item.item_code, [])
		moved = last.get(item.item_code) or {}
		data.append(
			{
				"item_code": item.item_code,
				"item_name": item.item_name,
				"item_group": item.item_group,
				"brand": item.brand,
				"stock_uom": item.stock_uom,
				"disabled": cint(item.disabled),
				"status": status,
				"price_in_list": flt(price.price_list_rate) if price else 0.0,
				"price_branches": (", ".join(served) if served else _("All Branches")) if price else "",
				"stock_qty": qty,
				"valuation_rate": valuation,
				"last_purchase_rate": flt(item.last_purchase_rate),
				"min_price": floor,
				"last_rate": flt(moved.get("rate")),
				"last_date": moved.get("date"),
				"current_price_lists": current_lists_text(lists),
				"price_list_count": len({name for name, _rate in lists}),
			}
		)
	return data


def _own_list_rows(codes: list, price_list: str, branch=None) -> dict:
	"""The general rows each item has in the checked list, in ERPNext's order (and as `branch` sees them)."""
	found = {}
	for chunk in _batches(codes):
		for row in frappe.db.sql(
			"select name, item_code, price_list_rate, valid_from, valid_upto from `tabItem Price` "
			"where price_list = %(price_list)s and item_code in %(codes)s "
			"and ifnull(customer, '') = '' and ifnull(supplier, '') = '' "
			"order by item_code, valid_from desc, creation desc",
			{"price_list": price_list, "codes": tuple(chunk)},
			as_dict=True,
		):
			found.setdefault(row.item_code, []).append(row)

	if branch and item_price_branch.table_ready():
		for code, rows in list(found.items()):
			found[code] = item_price_branch.visible(rows, branch, name_of=lambda row: row.name)
	return found


def _current_lists(codes: list, kind: str, on_date, branch=None) -> dict:
	found = {}
	for chunk in _batches(codes):
		rows = frappe.db.sql(
			"select p.name, p.item_code, p.price_list, p.price_list_rate from `tabItem Price` p "
			"join `tabPrice List` pl on pl.name = p.price_list "
			"where p.item_code in %(codes)s and pl.enabled = 1 and pl." + kind + " = 1 "
			"and ifnull(p.customer, '') = '' and ifnull(p.supplier, '') = '' "
			"and p.price_list_rate > 0 and (p.valid_from is null or p.valid_from <= %(on_date)s) "
			"and (p.valid_upto is null or p.valid_upto >= %(on_date)s) "
			"order by p.item_code, p.price_list",
			{"codes": tuple(chunk), "on_date": on_date},
			as_dict=True,
		)
		if branch and item_price_branch.table_ready():
			rows = item_price_branch.visible(rows, branch, name_of=lambda row: row.name)
		for row in rows:
			found.setdefault(row.item_code, []).append((row.price_list, row.price_list_rate))
	return found


def _stock(codes: list, warehouses) -> dict:
	found = {}
	query = (
		"select item_code, sum(actual_qty) as qty, sum(stock_value) as value, max(valuation_rate) as rate "
		"from `tabBin` where item_code in %(codes)s"
	)
	if warehouses:
		query += " and warehouse in %(warehouses)s"
	query += " group by item_code"

	for chunk in _batches(codes):
		values = {"codes": tuple(chunk)}
		if warehouses:
			values["warehouses"] = warehouses
		for row in frappe.db.sql(query, values, as_dict=True):
			found[row.item_code] = row
	return found


def _last_movement(codes: list, kind: str, company=None) -> dict:
	"""Latest date and rate each item sold (or was bought) at, in this company's invoices. Returns are left out."""
	if kind == "selling":
		line, head = "`tabSales Invoice Item`", "`tabSales Invoice`"
	else:
		line, head = "`tabPurchase Invoice Item`", "`tabPurchase Invoice`"

	query = (
		"select li.item_code, max(hd.posting_date) as last_date, "
		"substring_index(group_concat(li.base_net_rate order by hd.posting_date desc, hd.creation desc separator ','), ',', 1) as last_rate "
		"from " + line + " li join " + head + " hd on hd.name = li.parent "
		"where li.item_code in %(codes)s and hd.docstatus = 1 and hd.is_return = 0"
	)
	if company:
		query += " and hd.company = %(company)s"
	query += " group by li.item_code"

	found = {}
	for chunk in _batches(codes):
		values = {"codes": tuple(chunk)}
		if company:
			values["company"] = company
		for row in frappe.db.sql(query, values, as_dict=True):
			found[row.item_code] = {"date": row.last_date, "rate": flt(row.last_rate)}
	return found


# ─── Presentation ────────────────────────────────────────────────────────────

def get_columns(kind: str) -> list:
	moved = _("Last Sold") if kind == "selling" else _("Last Purchased")
	columns = [
		{"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 140},
		{"label": _("Brand"), "fieldname": "brand", "fieldtype": "Link", "options": "Brand", "width": 100},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Link", "options": "UOM", "width": 60},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 150},
		{"label": _("Price in List"), "fieldname": "price_in_list", "fieldtype": "Float", "precision": 3, "width": 95},
		{"label": _("Price Serves"), "fieldname": "price_branches", "fieldtype": "Data", "width": 130},
		{"label": _("Current Price Lists"), "fieldname": "current_price_lists", "fieldtype": "Data", "width": 300},
		{"label": _("No. of Lists"), "fieldname": "price_list_count", "fieldtype": "Int", "width": 70},
		{"label": _("Stock Qty"), "fieldname": "stock_qty", "fieldtype": "Float", "precision": 3, "width": 90},
		{"label": _("Valuation Rate"), "fieldname": "valuation_rate", "fieldtype": "Float", "precision": 3, "width": 95},
		{"label": _("Last Purchase Rate"), "fieldname": "last_purchase_rate", "fieldtype": "Float", "precision": 3, "width": 110},
	]
	if kind == "selling":
		columns.append(
			{"label": _("Min. Price (Cost + Margin)"), "fieldname": "min_price", "fieldtype": "Float", "precision": 3, "width": 130}
		)
	columns += [
		{"label": moved + " " + _("Rate"), "fieldname": "last_rate", "fieldtype": "Float", "precision": 3, "width": 100},
		{"label": moved + " " + _("On"), "fieldname": "last_date", "fieldtype": "Date", "width": 95},
	]
	return columns


def get_summary(data: list) -> list:
	by_status = {STATUS_PRICED: 0, STATUS_NONE: 0, STATUS_ZERO: 0, STATUS_EXPIRED: 0}
	elsewhere = 0
	nowhere = 0
	for row in data:
		by_status[row["status"]] = by_status.get(row["status"], 0) + 1
		if not row["price_list_count"]:
			nowhere += 1
		elif row["status"] != STATUS_PRICED:
			elsewhere += 1

	return [
		{"value": len(data), "label": _("Items Listed"), "datatype": "Int"},
		{"value": by_status[STATUS_PRICED], "label": _(STATUS_PRICED), "datatype": "Int", "indicator": "Green"},
		{"value": by_status[STATUS_NONE], "label": _(STATUS_NONE), "datatype": "Int", "indicator": "Red"},
		{"value": by_status[STATUS_ZERO], "label": _(STATUS_ZERO), "datatype": "Int", "indicator": "Orange"},
		{"value": by_status[STATUS_EXPIRED], "label": _(STATUS_EXPIRED), "datatype": "Int", "indicator": "Orange"},
		{"value": elsewhere, "label": _("Unpriced Here, Priced in Another List"), "datatype": "Int", "indicator": "Blue"},
		{"value": nowhere, "label": _("Priced Nowhere"), "datatype": "Int", "indicator": "Red"},
	]
