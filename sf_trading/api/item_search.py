import re

import frappe
from frappe.query_builder import Criterion
from frappe.query_builder.functions import IfNull
from frappe.utils import nowdate

# a multi-word search is resolved to item codes first, then handed to ERPNext as a name IN (...)
# filter; capped so a broad search ("ms 1") cannot build a list sqlparse refuses
WORD_SEARCH_LIMIT = 1000


def _natural_key(s):
	"""Sort key that treats embedded decimal numbers as floats.

	Plain alphabetical sort puts '2.6MM' before '2MM' because '.' < 'M' in ASCII.
	This key compares the numeric parts as floats so 2 < 2.6 gives the right order.
	"""
	parts = re.split(r"(\d+(?:\.\d+)?|\.\d+)", s or "")
	result = []
	for part in parts:
		try:
			result.append(float(part))
		except ValueError:
			result.append(part.lower())
	return result


def _like(word):
	return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def items_matching_every_word(txt, allowed=None):
	"""Item codes in which EVERY word of txt appears, in any order and in any of the fields
	ERPNext's own item search reads (code, the Item search fields, barcode).

	Only items ERPNext's own search would offer are considered (not disabled, not a template, not
	past end of life) and, when `allowed` is given, only those codes -- both BEFORE deciding which
	of the two stages below answers, so a name match the caller could never use cannot hide the
	fallback stage's items.

	ERPNext matches the whole text as one string -- "ms 1.2" must appear exactly so in one field,
	which "MS TUBE 20X20X6 1.2MM" does not. Users type the words they remember, in whatever order:
	"ms 1.2", "1.2 ms tube", "tube 20x20 ms". None for a single-word search, which ERPNext already
	handles.

	Items carrying every word in their own name or code come first and, when there are any, alone:
	otherwise "ms 1.2" also brings back "CHEQUERED PLATE 1.22X2.44", whose "ms" is only somewhere
	in a description or group ("items"). The other fields are the fallback when no name fits."""
	words = (txt or "").split()
	if len(words) < 2:
		return None

	item = frappe.qb.DocType("Item")
	barcode = frappe.qb.DocType("Item Barcode")
	meta = frappe.get_meta("Item")
	fields = ["name", "item_name"] + [
		f for f in meta.get_search_fields() if f not in ("name", "item_name") and meta.has_field(f)
	]

	def every_word(word_condition):
		return Criterion.all([word_condition(_like(word)) for word in words])

	today = nowdate()
	offered = (
		(item.disabled == 0)
		& (item.has_variants == 0)
		& ((item.end_of_life > today) | (IfNull(item.end_of_life, "0000-00-00") == "0000-00-00"))
	)

	def run(condition):
		codes = (
			frappe.qb.from_(item)
			.select(item.name)
			.where(offered & condition)
			.orderby(item.item_name)
			.limit(WORD_SEARCH_LIMIT)
			.run(pluck=True)
		)
		# the company's own item set is far too long for an IN list (sqlparse refuses past 10,000
		# tokens), so it is applied here; the cap above is on candidates, as before
		return [code for code in codes if allowed is None or code in allowed]

	in_name = run(
		every_word(lambda like: item.item_name.like(like) | item.name.like(like) | item.item_code.like(like))
	)
	if in_name:
		return in_name

	return run(
		every_word(
			lambda like: Criterion.any(
				[item[f].like(like) for f in fields]
				+ [item.name.isin(frappe.qb.from_(barcode).select(barcode.parent).where(barcode.barcode.like(like)))]
			)
		)
	)


def _apply_word_search(txt, filters):
	"""For a multi-word txt: narrow filters["name"] to the items matching every word, and return
	the txt ERPNext should then search with (blank). Returns (txt, filters, no_match)."""
	if len((txt or "").split()) < 2:
		return txt, filters, False

	# ERPNext replaces filters["name"] outright with the party's own "Item" rule, which would throw
	# our narrowing (and the typed text we blank) away and list that party's whole item set. Leave
	# such a search to ERPNext's own behaviour.
	party = filters.get("customer") or filters.get("supplier")
	if party and frappe.db.exists("Party Specific Item", {"party": party, "restrict_based_on": "Item"}):
		return txt, filters, False

	existing = filters.get("name")
	allowed_set = None
	if existing:
		# only an IN list can be intersected with; any other name condition is left to ERPNext
		if not (isinstance(existing, (list, tuple)) and existing and existing[0] == "in"):
			return txt, filters, False
		allowed_set = set(existing[1])

	codes = items_matching_every_word(txt, allowed_set)
	if codes is None:
		return txt, filters, False
	if not codes:
		return txt, filters, True

	filters["name"] = ["in", codes]
	return "", filters, False


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_items_with_stock_and_rate(doctype, txt, searchfield, start, page_len, filters, as_dict=False, **kwargs):
	"""
	Global item Link-field search — used by ALL item_code fields across all doctypes.

	Wraps ``erpnext.controllers.queries.item_query`` (standard ERPNext behaviour
	preserved) and appends two extra columns:

		- Stock: <total qty across the user's permitted warehouses>
		- Rate: <selling rate from the form's price list>

	Frappe's autocomplete joins everything after the first column with ", ",
	so the dropdown ends up looking like:

		ITEM-001
		Item Name, Item Group, Brand, Stock: 12, Rate: SAR 100

	Custom kwargs consumed by us (popped before delegating):
		- company   : used for stock + company-default filtering; falls back to user default
		- price_list: used for selling rate lookup
	"""
	from erpnext.controllers.queries import item_query
	from frappe.core.doctype.user_permission.user_permission import get_permitted_documents

	from sf_trading.company_items import item_codes_for_companies

	if not isinstance(filters, dict):
		filters = {}

	# Pop our custom filters before passing to ERPNext's item_query (it would
	# otherwise try to apply them as Item field filters and either fail or
	# return zero rows).
	company = filters.pop("company", None)
	price_list = filters.pop("price_list", None)

	# Pre-filter to this company's items BEFORE calling item_query, so ERPNext's
	# pagination operates on the already-restricted item set. Doing this
	# post-pagination caused items to disappear: item_query returns the first N
	# items alphabetically, and the company-specific items may not be in that
	# first page at all.
	#
	# "This company's items" means an Item Default row for the company, or — for
	# fixed assets, which have no Item Defaults table at all — an Asset Category
	# carrying an account row for it. See sf_trading.company_items.
	if company:
		allowed_codes = item_codes_for_companies([company])
		if not allowed_codes:
			return []
		filters["name"] = ["in", allowed_codes]

	# "ms 1.2", "1.2 ms tube": every word, any order -- see items_matching_every_word
	txt, filters, no_match = _apply_word_search(txt, filters)
	if no_match:
		return []

	# Delegate to ERPNext's standard item search. We pass as_dict=False so we
	# get tuples in the exact format Frappe's autosuggest expects.
	base_rows = item_query(
		doctype, txt, searchfield, start, page_len, filters, as_dict=False
	) or []

	if not base_rows:
		return []

	# Collect item codes (always the first column of each ERPNext row)
	item_codes = [row[0] for row in base_rows if row and row[0]]
	if not item_codes:
		return list(base_rows)

	# Natural sort by item_name: numeric parts compared as floats so
	# "2MM" (2.0) sorts before "2.6MM" (2.6), not after it.
	base_rows = sorted(base_rows, key=lambda r: _natural_key(r[1] or r[0] or ""))

	# --- Resolve "the logged user's warehouse(s)" ---
	# Priority:
	#   1. User Permission on Warehouse (matches Quick Entry's behaviour).
	#   2. The user's default warehouse (User Defaults).
	# If neither is set we show nothing rather than leaking stock for every
	# warehouse — that was the source of the "showing all warehouses" issue
	# when the logged-in user has no warehouse-specific configuration.
	user_warehouses = list(get_permitted_documents("Warehouse") or [])
	if not user_warehouses:
		default_wh = (
			frappe.defaults.get_user_default("Warehouse")
			or frappe.defaults.get_user_default("warehouse")
		)
		if default_wh:
			user_warehouses = [default_wh]

	stock_map = {}  # item_code -> [(warehouse_label, qty), ...] sorted by qty desc
	stock_unknown = not user_warehouses  # flag: cannot determine user's warehouse
	if company and user_warehouses:
		warehouses = frappe.get_all(
			"Warehouse",
			filters={
				"company": company,
				"is_group": 0,
				"disabled": 0,
				"name": ["in", user_warehouses],
			},
			fields=["name", "warehouse_name"],
		)

		if warehouses:
			warehouse_names = [w.name for w in warehouses]
			wh_label = {w.name: (w.warehouse_name or w.name) for w in warehouses}

			bin_rows = frappe.db.sql(
				"""
				SELECT item_code, warehouse, actual_qty
				FROM `tabBin`
				WHERE item_code IN %(items)s
				  AND warehouse IN %(warehouses)s
				  AND actual_qty > 0
				ORDER BY actual_qty DESC
				""",
				{"items": item_codes, "warehouses": warehouse_names},
				as_dict=True,
			)
			for row in bin_rows:
				stock_map.setdefault(row.item_code, []).append(
					(wh_label.get(row.warehouse, row.warehouse), row.actual_qty or 0)
				)

	# --- Rate: from Item Price for the form's selling price list ---
	price_map = {}
	if price_list:
		prices = frappe.get_all(
			"Item Price",
			filters={
				"item_code": ["in", item_codes],
				"price_list": price_list,
				"selling": 1,
			},
			fields=["item_code", "price_list_rate", "currency"],
		)
		price_map = {p.item_code: (p.price_list_rate, p.currency) for p in prices}

	def _fmt_qty(q):
		return ("{0:.2f}".format(float(q))).rstrip("0").rstrip(".") or "0"

	# --- Append the two extra columns to each row ---
	results = []
	for row in base_rows:
		new_row = list(row)
		item_code = new_row[0]

		stock_list = stock_map.get(item_code) or []
		if stock_unknown:
			# No User Permission on Warehouse and no user default — we cannot
			# resolve "the user's warehouse" so we don't display a stock figure.
			stock_str = "Stock: -"
		elif not stock_list:
			stock_str = "Stock: 0"
		elif len(stock_list) == 1:
			# One warehouse for this user — keep it compact.
			wh_label, qty = stock_list[0]
			stock_str = "Stock: {0} ({1})".format(_fmt_qty(qty), wh_label)
		else:
			# Multiple warehouses — show all so transfers are visible.
			parts = ["{0}: {1}".format(label, _fmt_qty(qty)) for label, qty in stock_list]
			stock_str = "Stock: " + ", ".join(parts)
		new_row.append(stock_str)

		rate_info = price_map.get(item_code)
		if rate_info:
			rate, currency = rate_info
			new_row.append("Rate: {0} {1}".format(currency or "", _fmt_qty(rate)).strip())
		else:
			new_row.append("Rate: -")

		results.append(tuple(new_row))

	return results


def redirect_item_query_before_request():
	"""before_request hook: redirect every erpnext item_query call to our rich version.

	search_link POSTs query= directly, bypassing override_whitelisted_methods, so
	swapping here is the only reliable server-side interception point.

	We also inject company from user defaults when the caller hasn't supplied one so
	that all item link fields (not just Sales Invoice) get the company-filtered,
	stock/rate-enriched dropdown.
	"""
	if frappe.form_dict.get("query") != "erpnext.controllers.queries.item_query":
		return

	frappe.form_dict["query"] = "sf_trading.api.item_search.search_items_with_stock_and_rate"



@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def item_query(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
	"""Global override of erpnext.controllers.queries.item_query.
	Delegates entirely to ERPNext's standard search, then re-sorts results
	by item_name so all item Link fields show items in alphabetical name order.
	"""
	from erpnext.controllers.queries import item_query as _erpnext_item_query

	if not isinstance(filters, dict):
		filters = {}
	txt, filters, no_match = _apply_word_search(txt, filters)
	if no_match:
		return []

	rows = _erpnext_item_query(doctype, txt, searchfield, start, page_len, filters, as_dict=as_dict) or []
	if as_dict:
		return sorted(rows, key=lambda r: _natural_key(r.get("item_name") or r.get("name") or ""))
	return sorted(rows, key=lambda r: _natural_key(r[1] if len(r) > 1 else r[0] or ""))
