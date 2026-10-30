# sf_trading/cogs_breakdown.py
"""How a cost was reached: the engine behind COGS Cost Breakdown and Transfer Cost Breakdown.

Read only. Nothing here posts or changes an entry; both reports recompute from the Stock Ledger every
time they open, so a backdated entry shows its new cost on the next open with nothing flagged.

THE METHOD
----------
This site values every item at Moving Average (Stock Settings, and no item overrides it with FIFO;
there are no batch, serial or bundle movements), so this replays Moving Average: the outgoing rate of
a movement is the warehouse's balance value / balance qty just before it.

Moving Average blends everything in the warehouse into one rate, so "which documents is this cost
made of" can only be answered pro rata. The warehouse balance just before a movement is held as
buckets, one per document that brought stock in: an inbound entry adds to its document's bucket, an
outbound entry takes the same share out of every bucket, so the buckets always add up to the balance
qty and value. A movement of q units is then q x (bucket qty / balance qty) from each bucket, at that
bucket's own rate -- which adds up to the movement's own value.

"Adds up to" is checked, not assumed: `explain` compares the buckets with the ledger's own balance and
the rows with the line's own value, flags a line that does not match, and adds an explicit row for
whatever the replay cannot explain, so the rows under a line sum to the line to within rounding.

TRANSFERS THROUGH "In Transit"
------------------------------
About half of the transfers go through a plain warehouse called "In Transit": a Send moves stock into
it and an Accept moves it out, as two ordinary Material Transfers. Nothing links an Accept to its Send
(no outgoing_stock_entry, no custom field), and the transit bin is valued at Moving Average like any
other, so an Accept draws pro rata from everything that sat in transit at that moment, never from
"its" Send. The breakdown shows exactly that mix, and Expand Transfer Chain follows each Send back to
where it came from.

WHERE THE REPLAY STARTS
-----------------------
Not at the beginning of time. A Stock Reconciliation resets the balance and the rate (it is shown as
one source row), and a balance that falls to zero leaves nothing behind; the replay starts at the
latest of either before the movement. That keeps one breakdown to one item in one warehouse over a
short stretch of ledger, which matters with well over a hundred thousand entries. The ledger is read
once per item and warehouse and every line of it is answered from that one pass.

NEGATIVE STOCK
--------------
Stock Settings no longer allow it, but the history holds entries written while the migration ran, and
a replay reads history. Stock that arrives over a negative balance is worth only what is left above
zero, so the bucket is seeded from the ledger's own balance (qty after, stock value), not from the
quantity received. A line that left more than the bin held is flagged `NEGATIVE`, and the part that
came from no bin is shown as its own row.

Amounts are company currency (the Stock Ledger's own values); quantities are in stock UOM.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from sf_trading.query import IN_BATCH


def sle_fields(alias: str = "") -> str:
	"""The Stock Ledger Entry columns the breakdown reads; `alias` qualifies them inside a join."""
	a = f"{alias}." if alias else ""
	return (
		f"{a}name, {a}item_code, {a}warehouse, {a}voucher_type, {a}voucher_no, {a}voucher_detail_no, "
		f"{a}posting_date, coalesce({a}posting_datetime, timestamp({a}posting_date, {a}posting_time)) as posted, "
		f"{a}creation, {a}actual_qty, {a}qty_after_transaction, {a}stock_value, {a}stock_value_difference, "
		f"{a}valuation_rate, {a}company"
	)


#: a posting moment that is never NULL, for ordering ledger entries
POSTED = "coalesce(posting_datetime, timestamp(posting_date, posting_time))"
SLE_FIELDS = sle_fields()
RECONCILIATION = "Stock Reconciliation"
#: below this a bucket is rounding left over, not stock
EPSILON = 1e-9
#: ledger quantities carry nine decimals; a gap smaller than this is float noise, not a missing movement
QTY_TOLERANCE = 1e-6
#: a line always lists its largest sources, however small a share of it they are
MIN_SOURCES_SHOWN = 8
#: how many hops back a chain of transfers is followed when the user asks for it
MAX_CHAIN_DEPTH = 5
#: transfer hops one report run may look up before it stops following chains (each hop is a few queries)
MAX_CHAIN_LOOKUPS = 150
#: names per IN (...), the house batch size (query.py) that keeps a statement far below sqlparse's token cap
CHUNK = IN_BATCH

#: a gap under this share of a line is accumulated rounding (see tolerance)
REL_TOLERANCE = 0.001

#: how a line's own value compares with what the replay gives
OK = "ok"
NEGATIVE = "negative"
DIFFERS = "differs"
#: a return line against the sale it returns: stock restocked at what it was sold at, or not
RETURN_OK = "return_ok"
RETURN_DIFFERS = "return_differs"
#: a return with no original invoice (most of them here): ERPNext restocks it at the current valuation rate
RETURN_STANDALONE = "return_standalone"
#: a transfer line whose receiving leg is worth something other than what left the source
GAP = "gap"
#: flags that put a line on the "lines to review" list. A standalone return is routine, so it is not one.
REVIEW_FLAGS = (NEGATIVE, DIFFERS, RETURN_DIFFERS, GAP)
#: a return is restocked "at a different cost" when it is further than this share from the sale's
RETURN_RATE_TOLERANCE = 0.01
#: "Over 5x cost": a margin that high is a price typed wrong far more often than a bargain
HIGH_MARGIN_RATIO = 5

#: the item table that carries a stock voucher's rows, for its sales amount and cost centre
SALES_ITEM_DOCTYPE = {"Delivery Note": "Delivery Note Item", "Sales Invoice": "Sales Invoice Item"}
#: stock-receiving vouchers whose item rows split the cost into price, taxes and landed cost
RECEIPT_ITEM_DOCTYPE = {
	"Purchase Receipt": "Purchase Receipt Item",
	"Purchase Invoice": "Purchase Invoice Item",
}
#: where the GL books the cost of stock that left: Cost of Goods Sold, or -- where the company holds a
#: delivered-but-unbilled note's cost until it is invoiced (sdbnb.py) -- Stock Delivered But Not Billed
COST_ACCOUNT_TYPES = ("Cost of Goods Sold", "Stock Delivered But Not Billed")
#: User Permission doctypes that narrow what a stock voucher may show
SCOPED_DOCTYPES = ("Company", "Customer", "Branch", "Warehouse", "Cost Center")


def chunks(values, size: int = CHUNK):
	values = list(values)
	for i in range(0, len(values), size):
		yield values[i : i + size]


def tolerance(value: float = 0.0) -> float:
	"""The largest gap that is rounding rather than a missing movement.

	Never less than one and a half of the smallest currency unit. Against a line it is also a
	thousandth of the line: the ledger rounds every entry to the currency's decimals while the replay
	takes exact shares, so a bin with hundreds of movements since its last reconciliation drifts by a few
	fils (measured on the live ledger: 1.5-10 fils on lines of 4-520 BHD, 0.001%-0.04%)."""
	precision = cint(frappe.get_system_settings("currency_precision")) or 2
	return max(1.5 * 10**-precision, REL_TOLERANCE * abs(value))


def default_valuation_method() -> str:
	return frappe.db.get_single_value("Stock Settings", "valuation_method") or "FIFO"


def valuation_method(item_code: str) -> str:
	return frappe.get_cached_value("Item", item_code, "valuation_method") or default_valuation_method()


# ---------------------------------------------------------------------------
# Ledger reads
# ---------------------------------------------------------------------------
def voucher_entries(voucher_type: str, voucher_no: str) -> list:
	"""The live Stock Ledger Entries a voucher made, in posting order."""
	return entries_for_vouchers([(voucher_type, voucher_no)]).get((voucher_type, voucher_no), [])


def entries_for_vouchers(vouchers) -> dict:
	"""{(voucher_type, voucher_no): its live ledger entries in posting order}, a query per type and chunk."""
	by_type = defaultdict(list)
	for voucher_type, voucher_no in vouchers:
		by_type[voucher_type].append(voucher_no)

	out = defaultdict(list)
	for voucher_type, names in by_type.items():
		for chunk in chunks(names):
			rows = frappe.db.sql(
				f"""select {SLE_FIELDS} from `tabStock Ledger Entry`
				where voucher_type = %s and voucher_no in %s and is_cancelled = 0
				order by posted, creation""",
				(voucher_type, tuple(chunk)),
				as_dict=True,
			)
			for e in rows:
				out[(e.voucher_type, e.voucher_no)].append(e)
	return out


def transfer_legs(voucher_no: str, item_code: str) -> list:
	"""The legs a Material Transfer took one item out of its source warehouse(s) with."""
	return frappe.db.sql(
		f"""select {SLE_FIELDS} from `tabStock Ledger Entry`
		where voucher_type = 'Stock Entry' and voucher_no = %s and item_code = %s
		  and actual_qty < 0 and is_cancelled = 0
		order by posted, creation""",
		(voucher_no, item_code),
		as_dict=True,
	)


def _start_of_replay(entry):
	"""The latest Stock Reconciliation, or point where the balance fell to zero, before this entry."""
	rows = frappe.db.sql(
		f"""select {SLE_FIELDS} from `tabStock Ledger Entry`
		where item_code = %(item)s and warehouse = %(wh)s and is_cancelled = 0
		  and ({POSTED} < %(posted)s or ({POSTED} = %(posted)s and creation < %(creation)s))
		  and (voucher_type = %(reco)s or qty_after_transaction <= 0)
		order by posted desc, creation desc limit 1""",
		{
			"item": entry.item_code,
			"wh": entry.warehouse,
			"posted": entry.posted,
			"creation": entry.creation,
			"reco": RECONCILIATION,
		},
		as_dict=True,
	)
	return rows[0] if rows else None


def _read_window(item_code: str, warehouse: str, start, last) -> list:
	"""Ledger entries of one item in one warehouse from `start` (included; none = the beginning) through `last`."""
	conditions = [
		"item_code = %(item)s",
		"warehouse = %(wh)s",
		"is_cancelled = 0",
		f"({POSTED} < %(posted)s or ({POSTED} = %(posted)s and creation <= %(creation)s))",
	]
	values = {"item": item_code, "wh": warehouse, "posted": last.posted, "creation": last.creation}
	if start:
		# the reset entry itself is read (a reconciliation is the first bucket); a zero point is not
		conditions.append(
			f"({POSTED} > %(s_posted)s or ({POSTED} = %(s_posted)s and creation >= %(s_creation)s))"
		)
		values.update(s_posted=start.posted, s_creation=start.creation)
	return frappe.db.sql(
		f"""select {SLE_FIELDS} from `tabStock Ledger Entry`
		where {" and ".join(conditions)} order by posted, creation""",
		values,
		as_dict=True,
	)


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------
def balance_before(entry) -> frappe._dict:
	"""The warehouse balance just before this entry: qty, value and the Moving Average rate."""
	qty = flt(entry.qty_after_transaction) - flt(entry.actual_qty)
	value = flt(entry.stock_value) - flt(entry.stock_value_difference)
	return frappe._dict(qty=qty, value=value, rate=(value / qty) if qty else 0)


def _bucket(e, qty, value):
	return frappe._dict(
		voucher_type=e.voucher_type,
		voucher_no=e.voucher_no,
		posting_date=e.posting_date,
		qty=flt(qty),
		value=flt(value),
		rate=0,
	)


def _apply(buckets: dict, e) -> None:
	"""Move the buckets past one ledger entry, the way Moving Average does."""
	key = (e.voucher_type, e.voucher_no)
	qty_after = flt(e.qty_after_transaction)
	qty = flt(e.actual_qty)

	if e.voucher_type == RECONCILIATION:
		buckets.clear()
		if qty_after > EPSILON:
			buckets[key] = _bucket(e, qty_after, e.stock_value)
		return
	if qty_after <= EPSILON:
		# the bin is empty (or negative): nothing before this is stock any more
		buckets.clear()
		return

	if qty > 0:
		if qty_after - qty <= EPSILON:
			# arrived over an empty or negative bin: only what is above zero is stock, and the
			# ledger's own balance value is what it is worth -- not the quantity that was received
			buckets.clear()
			buckets[key] = _bucket(e, qty_after, e.stock_value)
			return
		bucket = buckets.get(key) or _bucket(e, 0, 0)
		bucket.qty += qty
		bucket.value += flt(e.stock_value_difference)
		buckets[key] = bucket
	elif qty < 0:
		held = sum(b.qty for b in buckets.values())
		if held <= EPSILON:
			return
		# Moving Average takes the same share out of everything in the warehouse
		keep = max(0.0, 1 + qty / held)
		for b in buckets.values():
			b.qty *= keep
			b.value *= keep


def _snapshot(buckets: dict) -> list:
	"""The buckets as they stand, with each one's rate -- a copy, so the replay can go on mutating."""
	return [frappe._dict(b, rate=b.value / b.qty) for b in buckets.values() if b.qty > EPSILON]


def sources_before_many(entries) -> dict:
	"""{entry name: the documents the balance just before it is made of, as buckets (qty, value, rate)}.

	The ledger of one item in one warehouse is read once, from the latest reset before its earliest
	entry here to its latest, and every entry of it is answered from that single pass."""
	groups = defaultdict(list)
	for e in entries:
		groups[(e.item_code, e.warehouse)].append(e)

	snapshots = {}
	for (item_code, warehouse), targets in groups.items():
		targets.sort(key=lambda e: (e.posted, e.creation))
		wanted = {e.name for e in targets}
		window = _read_window(item_code, warehouse, _start_of_replay(targets[0]), targets[-1])
		buckets = {}
		for row in window:
			if row.name in wanted:
				snapshots[row.name] = _snapshot(buckets)
			_apply(buckets, row)
	return {e.name: snapshots.get(e.name, []) for e in entries}


def sources_before(entry) -> list:
	"""The documents the balance just before this entry is made of, as buckets (qty, value, rate)."""
	return sources_before_many([entry])[entry.name]


def split(buckets: list, qty: float) -> list:
	"""`qty` units taken pro rata from the buckets, each at its own rate."""
	held = sum(b.qty for b in buckets)
	if held <= EPSILON or qty <= EPSILON:
		return []
	rows = []
	for b in buckets:
		part = qty * b.qty / held
		rows.append(
			frappe._dict(
				voucher_type=b.voucher_type,
				voucher_no=b.voucher_no,
				posting_date=b.posting_date,
				qty=part,
				rate=b.rate,
				value=part * b.rate,
			)
		)
	return rows


def compact(rows: list, share: float = 0.01) -> list:
	"""Shares under `share` of the movement (a sliver of an old receipt, worn down by every sale since) as one row.

	The largest `MIN_SOURCES_SHOWN` stay listed whatever their share -- a line made of a hundred equal
	slivers would otherwise collapse into nothing -- and a lone leftover is shown as itself. Kept as a
	row rather than dropped, so the source rows still add up to the line they explain."""
	floor = sum(r.qty for r in rows) * share
	largest = sorted(range(len(rows)), key=lambda i: -rows[i].qty)[:MIN_SOURCES_SHOWN]
	listed = {i for i, r in enumerate(rows) if r.qty >= floor} | set(largest)
	shown = [r for i, r in enumerate(rows) if i in listed]
	rest = [r for i, r in enumerate(rows) if i not in listed]
	if len(rest) == 1:
		shown.append(rest[0])
	elif rest:
		qty = sum(r.qty for r in rest)
		value = sum(r.value for r in rest)
		shown.append(
			frappe._dict(
				voucher_type=None,
				voucher_no=None,
				posting_date=None,
				qty=qty,
				rate=(value / qty) if qty else 0,
				value=value,
				others=len(rest),
			)
		)
	return shown


def gap_row(qty: float, value: float, label: str) -> frappe._dict:
	"""A source row for cost no document explains, so the rows under a line still sum to it."""
	return frappe._dict(
		voucher_type=None,
		voucher_no=None,
		posting_date=None,
		qty=qty,
		rate=(value / qty) if qty else 0,
		value=value,
		gap=label,
	)


def explain(entry, buckets: list, share: float = 0.01) -> frappe._dict:
	"""The documents behind one outgoing line, whether they add up to it, and a row for what they do not.

	`flag` is OK when the replay agrees with the ledger (balance qty before, the line's value within
	rounding), NEGATIVE when the line left more than the bin held, DIFFERS when it simply does not agree."""
	qty = -flt(entry.actual_qty)
	value = -flt(entry.stock_value_difference)
	in_bin = max(balance_before(entry).qty, 0)
	held = sum(b.qty for b in buckets)
	from_bin = min(qty, in_bin)

	parts = split(buckets, from_bin)
	residual = value - sum(p.value for p in parts)
	short = qty - from_bin
	rows = compact(parts, share)

	if short > QTY_TOLERANCE:
		flag = NEGATIVE
		rows.append(gap_row(short, residual, _("Left more than the bin held (negative stock)")))
	elif abs(held - in_bin) > QTY_TOLERANCE or abs(residual) > tolerance(value):
		flag = DIFFERS
		if abs(residual) > tolerance(value):
			rows.append(gap_row(0, residual, _("Not explained by the ledger replay")))
	else:
		flag = OK
	return frappe._dict(rows=rows, flag=flag, residual=residual, short=short)


def source_label(voucher_type: str, voucher_no: str) -> str:
	"""What kind of document brought the stock in, as the accounts team would name it."""
	if not voucher_type:
		return ""
	if voucher_type in ("Sales Invoice", "Delivery Note"):
		# stock comes back into a warehouse from a sale only by a return
		return "Sales Return"
	if voucher_type == "Stock Entry":
		return frappe.get_cached_value("Stock Entry", voucher_no, "purpose") or voucher_type
	return voucher_type


def source_type(s) -> str:
	"""The Source column text of a source row."""
	if s.get("gap"):
		return s.gap
	if s.voucher_type:
		return _(source_label(s.voucher_type, s.voucher_no))
	return _("Other documents ({0})").format(s.others)


def is_transfer(voucher_type: str, voucher_no: str) -> bool:
	return (
		voucher_type == "Stock Entry"
		and frappe.get_cached_value("Stock Entry", voucher_no, "purpose") == "Material Transfer"
	)


# ---------------------------------------------------------------------------
# Following a chain of transfers, and splitting a receipt's cost
# ---------------------------------------------------------------------------
def new_budget(lookups: int = MAX_CHAIN_LOOKUPS) -> frappe._dict:
	return frappe._dict(left=lookups, spent=False)


def attach_chain(rows: list, item_code: str, budget, level: int = 0, seen=frozenset(), share=0.01) -> None:
	"""Give each Material Transfer source row the documents *its* source warehouse held, `children`.

	Without this a transfer is one row with a link to its own breakdown and a chain is read a hop at a
	time. Followed to `MAX_CHAIN_DEPTH` hops, never into a transfer already on the path, and for no more
	than `budget` lookups per report run. A row that was followed has `followed` set; one that was not
	keeps its link."""
	for s in rows:
		s.children = []
		s.followed = False
		if not s.voucher_type or not is_transfer(s.voucher_type, s.voucher_no):
			continue
		if level + 1 >= MAX_CHAIN_DEPTH or s.voucher_no in seen:
			continue
		if budget.left <= 0:
			budget.spent = True
			continue
		budget.left -= 1

		legs = transfer_legs(s.voucher_no, item_code)
		total = sum(-flt(leg.actual_qty) for leg in legs)
		if not legs or total <= EPSILON:
			continue
		snapshots = sources_before_many(legs)
		kids = []
		for leg in legs:
			leg_qty = s.qty * (-flt(leg.actual_qty)) / total
			part = compact(split(snapshots[leg.name], leg_qty), share)
			for k in part:
				k.warehouse = leg.warehouse
			kids.extend(part)
		residual = s.value - sum(k.value for k in kids)
		if abs(residual) > tolerance(s.value):
			kids.append(gap_row(0, residual, _("Not explained by the ledger replay")))
		s.children = kids
		s.followed = True
		attach_chain(kids, item_code, budget, level + 1, seen | {s.voucher_no}, share)


def walk(rows: list, level: int = 0):
	"""Every source row of a tree with its depth, parents before their children."""
	for s in rows:
		yield s, level
		yield from walk(s.get("children") or [], level + 1)


def receipt_totals(pairs) -> dict:
	"""{(voucher_type, voucher_no, item_code): stock qty and the amounts its cost was made of}.

	`pairs` are (voucher_type, voucher_no) of Purchase Receipts / Purchase Invoices that brought stock in.
	A receipt's value is its price (net amount) plus the taxes and charges included in valuation plus
	what a Landed Cost Voucher added; `rm_supp_cost` (subcontracting) is left to the adjustment row."""
	by_type = defaultdict(set)
	for voucher_type, voucher_no in pairs:
		if voucher_type in RECEIPT_ITEM_DOCTYPE:
			by_type[voucher_type].add(voucher_no)

	totals = {}
	for voucher_type, names in by_type.items():
		for chunk in chunks(sorted(names)):
			for r in receipt_item_rows(voucher_type, chunk):
				t = totals.setdefault(
					(voucher_type, r.parent, r.item_code),
					frappe._dict(qty=0.0, price=0.0, tax=0.0, landed=0.0),
				)
				t.qty += flt(r.stock_qty)
				t.price += flt(r.base_net_amount)
				t.tax += flt(r.item_tax_amount)
				t.landed += flt(r.landed_cost_voucher_amount)
	return totals


def receipt_item_rows(voucher_type: str, parents) -> list:
	"""The item rows of Purchase Receipts / Purchase Invoices that carry what their cost was made of."""
	item = frappe.qb.DocType(RECEIPT_ITEM_DOCTYPE[voucher_type])
	return (
		frappe.qb.from_(item)
		.select(
			item.parent,
			item.item_code,
			item.stock_qty,
			item.base_net_amount,
			item.item_tax_amount,
			item.landed_cost_voucher_amount,
		)
		.where(item.parent.isin(list(parents)))
		.run(as_dict=True)
	)


def receipt_components(s, totals: dict, item_code: str) -> list:
	"""Rows that split a Purchase Receipt source into price, taxes and landed cost -- only where the
	receipt carries taxes in valuation or landed cost; a plain receipt is its price and needs no split."""
	t = totals.get((s.voucher_type, s.voucher_no, item_code))
	if not t or t.qty <= EPSILON or s.qty <= EPSILON:
		return []
	if t.landed <= tolerance() and t.tax <= tolerance():
		return []

	rows = []
	for label, amount in (
		(_("Purchase price"), t.price),
		(_("Taxes and charges in valuation"), t.tax),
		(_("Landed cost"), t.landed),
	):
		if abs(amount) > EPSILON:
			value = s.qty * amount / t.qty
			rows.append(frappe._dict(component=label, rate=value / s.qty, value=value))
	residual = s.value - sum(r.value for r in rows)
	if abs(residual) > tolerance(s.value):
		rows.append(frappe._dict(component=_("Rate adjustments"), rate=residual / s.qty, value=residual))
	return rows


# ---------------------------------------------------------------------------
# Lookups the reports share: items, customers, groups, sales people, returns, transfers
# ---------------------------------------------------------------------------
def item_info(codes) -> dict:
	"""{item_code: item_name, item_group, valuation_method}, one query per chunk instead of one per line."""
	out = {}
	for chunk in chunks(sorted({c for c in codes if c})):
		for r in item_fields(chunk):
			out[r.name] = r
	return out


def item_fields(codes) -> list:
	item = frappe.qb.DocType("Item")
	return (
		frappe.qb.from_(item)
		.select(item.name, item.item_name, item.item_group, item.valuation_method)
		.where(item.name.isin(list(codes)))
		.run(as_dict=True)
	)


def customer_info(names) -> dict:
	"""{customer: customer_name, customer_group}."""
	out = {}
	for chunk in chunks(sorted({n for n in names if n})):
		for r in customer_fields(chunk):
			out[r.name] = r
	return out


def customer_fields(names) -> list:
	customer = frappe.qb.DocType("Customer")
	return (
		frappe.qb.from_(customer)
		.select(customer.name, customer.customer_name, customer.customer_group)
		.where(customer.name.isin(list(names)))
		.run(as_dict=True)
	)


def group_members(doctype: str, name: str) -> set:
	"""`name` and everything under it in a nested-set tree (Item Group, Customer Group), as the stock
	reports of ERPNext read a group filter."""
	from frappe.utils.nestedset import get_descendants_of

	return {name, *get_descendants_of(doctype, name, ignore_permissions=True)}


def sales_persons(vouchers) -> dict:
	"""{(voucher_type, voucher_no): [sales person, ...]} from the voucher's Sales Team."""
	by_type = defaultdict(list)
	for voucher_type, voucher_no in vouchers:
		by_type[voucher_type].append(voucher_no)

	out = defaultdict(list)
	for voucher_type, names in by_type.items():
		for chunk in chunks(names):
			for r in sales_team_rows(voucher_type, chunk):
				if r.sales_person not in out[(voucher_type, r.parent)]:
					out[(voucher_type, r.parent)].append(r.sales_person)
	return out


def sales_team_rows(voucher_type: str, parents) -> list:
	team = frappe.qb.DocType("Sales Team")
	return (
		frappe.qb.from_(team)
		.select(team.parent, team.sales_person)
		.where((team.parenttype == voucher_type) & team.parent.isin(list(parents)))
		.orderby(team.idx)
		.run(as_dict=True)
	)


def ledger_cost_many(vouchers) -> dict:
	"""{(voucher_type, voucher_no): what its stock cost per the ledger}, grouped in the database."""
	by_type = defaultdict(list)
	for voucher_type, voucher_no in vouchers:
		by_type[voucher_type].append(voucher_no)

	out = {}
	for voucher_type, names in by_type.items():
		for chunk in chunks(names):
			rows = frappe.db.sql(
				"""select voucher_no, sum(-stock_value_difference) from `tabStock Ledger Entry`
				where voucher_type = %s and voucher_no in %s and is_cancelled = 0
				group by voucher_no""",
				(voucher_type, tuple(chunk)),
			)
			for voucher_no, cost in rows:
				out[(voucher_type, voucher_no)] = flt(cost)
	return out


def original_costs(originals) -> dict:
	"""{(voucher_type, voucher_no, item_code): cost and qty the sale took out}, for the sales given returns
	were made against. `originals` are (voucher_type, voucher_no) pairs."""
	by_type = defaultdict(set)
	for voucher_type, voucher_no in originals:
		by_type[voucher_type].add(voucher_no)

	out = {}
	for voucher_type, names in by_type.items():
		for chunk in chunks(sorted(names)):
			rows = frappe.db.sql(
				"""select voucher_no, item_code, sum(-stock_value_difference), sum(-actual_qty)
				from `tabStock Ledger Entry`
				where voucher_type = %s and voucher_no in %s and actual_qty < 0 and is_cancelled = 0
				group by voucher_no, item_code""",
				(voucher_type, tuple(chunk)),
			)
			for voucher_no, item_code, cost, qty in rows:
				out[(voucher_type, voucher_no, item_code)] = frappe._dict(cost=flt(cost), qty=flt(qty))
	return out


def return_check(voucher_type: str, return_against, entry, originals: dict):
	"""(flag, original rate) of a return line: restocked at what the sale took it out at, or not."""
	original = originals.get((voucher_type, return_against, entry.item_code)) if return_against else None
	if not original or original.qty <= EPSILON:
		return RETURN_STANDALONE, None
	rate = original.cost / original.qty
	value = flt(entry.stock_value_difference)
	expected = flt(entry.actual_qty) * rate
	if abs(value - expected) <= max(tolerance(value), RETURN_RATE_TOLERANCE * abs(expected)):
		return RETURN_OK, rate
	return RETURN_DIFFERS, rate


def incoming_legs(details) -> dict:
	"""{voucher_detail_no: qty and value that arrived} -- the receiving leg of each Stock Entry row."""
	out = {}
	for chunk in chunks(sorted({d for d in details if d})):
		rows = frappe.db.sql(
			"""select voucher_detail_no, sum(actual_qty), sum(stock_value_difference)
			from `tabStock Ledger Entry`
			where voucher_type = 'Stock Entry' and voucher_detail_no in %s
			  and actual_qty > 0 and is_cancelled = 0
			group by voucher_detail_no""",
			(tuple(chunk),),
		)
		for detail, qty, value in rows:
			out[detail] = frappe._dict(qty=flt(qty), value=flt(value))
	return out


def branch_warehouses(branch: str) -> set:
	"""The warehouses a Branch Configuration lists for the branch. A warehouse can sit under several."""
	row = frappe.qb.DocType("Branch Configuration Warehouse")
	return set(
		frappe.qb.from_(row)
		.select(row.warehouse)
		.where((row.parent == branch) & (row.parenttype == "Branch Configuration"))
		.run(pluck=True)
	)


# ---------------------------------------------------------------------------
# The GL side, sales amounts and User Permissions
# ---------------------------------------------------------------------------
def gl_cogs_many(vouchers) -> dict:
	"""{(voucher_type, voucher_no): what the GL booked for the cost of its stock (COGS, or SDBNB on a note)}."""
	by_type = defaultdict(list)
	for voucher_type, voucher_no in vouchers:
		by_type[voucher_type].append(voucher_no)

	out = {}
	for voucher_type, names in by_type.items():
		for chunk in chunks(names):
			rows = frappe.db.sql(
				"""select gl.voucher_no, coalesce(sum(gl.debit - gl.credit), 0) from `tabGL Entry` gl
				join `tabAccount` a on a.name = gl.account and a.account_type in %s
				where gl.voucher_type = %s and gl.voucher_no in %s and gl.is_cancelled = 0
				group by gl.voucher_no""",
				(COST_ACCOUNT_TYPES, voucher_type, tuple(chunk)),
			)
			for voucher_no, booked in rows:
				out[(voucher_type, voucher_no)] = flt(booked)
	return out


def gl_cogs(voucher_type: str, voucher_no: str) -> float:
	"""What the GL booked for the cost of this voucher's stock (COGS, or SDBNB on a Delivery Note)."""
	return gl_cogs_many([(voucher_type, voucher_no)]).get((voucher_type, voucher_no), 0.0)


def permission_scope(user: str | None = None) -> dict:
	"""{linked doctype: [(allowed document, applicable_for)]} for the doctypes the user is restricted on.

	`frappe.db.sql` applies no permission filtering, so without this a user restricted to one company,
	branch, warehouse, cost centre or customer set would see every voucher here while the desk list is
	restricted. A user with no User Permissions (Administrator, most head-office logins) gets an empty
	scope and is unaffected."""
	from frappe.permissions import get_user_permissions

	permissions = get_user_permissions(user or frappe.session.user) or {}
	scope = {}
	for doctype in SCOPED_DOCTYPES:
		rows = [(r.get("doc"), r.get("applicable_for") or "") for r in permissions.get(doctype) or []]
		rows = [r for r in rows if r[0]]
		if rows:
			scope[doctype] = rows
	return scope


def permitted(scope: dict, link_doctype: str, value, for_doctype: str) -> bool:
	"""False when `value` lies outside a User Permission that applies to `for_doctype`.

	A blank value passes, the way frappe treats an empty link field under a User Permission, and a
	permission limited to other doctypes (applicable_for) does not restrict this one."""
	allowed = {
		doc for doc, applicable in scope.get(link_doctype, ()) if not applicable or applicable == for_doctype
	}
	return not (allowed and value and value not in allowed)
