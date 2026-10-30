# sf_trading/cogs_breakdown.py
"""How a cost was reached: the engine behind COGS Cost Breakdown and Transfer Cost Breakdown.

Read only. Nothing here posts or changes an entry; both reports recompute from the Stock Ledger every
time they open, so a backdated entry shows its new cost on the next open with nothing flagged.

THE METHOD
----------
This site values every item at Moving Average (Stock Settings, and no item overrides it with FIFO;
there are no batch, serial, bundle or transit-warehouse movements), so this replays Moving Average:
the outgoing rate of a movement is the warehouse's balance value / balance qty just before it.

Moving Average blends everything in the warehouse into one rate, so "which documents is this cost
made of" can only be answered pro rata. The warehouse balance just before a movement is held as
buckets, one per document that brought stock in: an inbound entry adds to its document's bucket, an
outbound entry takes the same share out of every bucket, so the buckets always add up to the balance
qty and value. A movement of q units is then q x (bucket qty / balance qty) from each bucket, at that
bucket's own rate -- which adds up to exactly the movement's own value.

WHERE THE REPLAY STARTS
-----------------------
Not at the beginning of time. A Stock Reconciliation resets the balance and the rate (it is shown as
one source row), and a balance that falls to zero leaves nothing behind; the replay starts at the
latest of either before the movement. That keeps one breakdown to one item in one warehouse over a
short stretch of ledger, which matters with well over a hundred thousand entries.

Amounts are company currency (the Stock Ledger's own values); quantities are in stock UOM.
"""

import frappe

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


def valuation_method(item_code: str) -> str:
	return (frappe.get_cached_value("Item", item_code, "valuation_method")
		or frappe.db.get_single_value("Stock Settings", "valuation_method") or "FIFO")


def voucher_entries(voucher_type: str, voucher_no: str) -> list:
	"""The live Stock Ledger Entries a voucher made, in posting order."""
	return frappe.db.sql(
		f"""select {SLE_FIELDS} from `tabStock Ledger Entry`
		where voucher_type = %s and voucher_no = %s and is_cancelled = 0
		order by posted, creation""",
		(voucher_type, voucher_no),
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
		{"item": entry.item_code, "wh": entry.warehouse, "posted": entry.posted,
		 "creation": entry.creation, "reco": RECONCILIATION},
		as_dict=True,
	)
	return rows[0] if rows else None


def balance_before(entry) -> frappe._dict:
	"""The warehouse balance just before this entry: qty, value and the Moving Average rate."""
	qty = (entry.qty_after_transaction or 0) - (entry.actual_qty or 0)
	value = (entry.stock_value or 0) - (entry.stock_value_difference or 0)
	return frappe._dict(qty=qty, value=value, rate=(value / qty) if qty else 0)


def sources_before(entry) -> list:
	"""The documents the balance just before this entry is made of, as buckets (qty, value, rate)."""
	start = _start_of_replay(entry)
	conditions = ["item_code = %(item)s", "warehouse = %(wh)s", "is_cancelled = 0",
		f"({POSTED} < %(posted)s or ({POSTED} = %(posted)s and creation < %(creation)s))"]
	values = {"item": entry.item_code, "wh": entry.warehouse, "posted": entry.posted, "creation": entry.creation}
	if start:
		# the reset entry itself is read (a reconciliation is the first bucket); a zero point is not
		conditions.append(f"({POSTED} > %(s_posted)s or ({POSTED} = %(s_posted)s and creation >= %(s_creation)s))")
		values.update(s_posted=start.posted, s_creation=start.creation)
	entries = frappe.db.sql(
		f"""select {SLE_FIELDS} from `tabStock Ledger Entry`
		where {" and ".join(conditions)} order by posted, creation""",
		values,
		as_dict=True,
	)

	buckets = {}
	for e in entries:
		if e.voucher_type == RECONCILIATION:
			buckets = {}
			if (e.qty_after_transaction or 0) > 0:
				buckets[(e.voucher_type, e.voucher_no)] = _bucket(e, e.qty_after_transaction, e.stock_value)
			continue
		if (e.qty_after_transaction or 0) <= 0:
			buckets = {}
			continue
		if e.actual_qty > 0:
			key = (e.voucher_type, e.voucher_no)
			bucket = buckets.get(key) or _bucket(e, 0, 0)
			bucket.qty += e.actual_qty
			bucket.value += e.stock_value_difference or 0
			buckets[key] = bucket
		elif e.actual_qty < 0:
			held = sum(b.qty for b in buckets.values())
			if held <= EPSILON:
				continue
			# Moving Average takes the same share out of everything in the warehouse
			keep = max(0.0, 1 + e.actual_qty / held)
			for b in buckets.values():
				b.qty *= keep
				b.value *= keep

	out = [b for b in buckets.values() if b.qty > EPSILON]
	for b in out:
		b.rate = b.value / b.qty if b.qty else 0
	return out


def _bucket(e, qty, value):
	return frappe._dict(voucher_type=e.voucher_type, voucher_no=e.voucher_no, posting_date=e.posting_date,
		qty=qty, value=value, rate=0)


def split(buckets: list, qty: float) -> list:
	"""`qty` units taken pro rata from the buckets, each at its own rate."""
	held = sum(b.qty for b in buckets)
	if held <= EPSILON:
		return []
	rows = []
	for b in buckets:
		part = qty * b.qty / held
		rows.append(frappe._dict(voucher_type=b.voucher_type, voucher_no=b.voucher_no,
			posting_date=b.posting_date, qty=part, rate=b.rate, value=part * b.rate))
	return rows


def compact(rows: list, share: float = 0.01) -> list:
	"""Shares under `share` of the movement (a sliver of an old receipt, worn down by every sale since) as one row.

	Kept as a row rather than dropped, so the source rows still add up to the line they explain."""
	floor = sum(r.qty for r in rows) * share
	shown = [r for r in rows if r.qty >= floor]
	rest = [r for r in rows if r.qty < floor]
	if rest:
		qty = sum(r.qty for r in rest)
		value = sum(r.value for r in rest)
		shown.append(frappe._dict(voucher_type=None, voucher_no=None, posting_date=None, qty=qty,
			rate=(value / qty) if qty else 0, value=value, others=len(rest)))
	return shown


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


def is_transfer(voucher_type: str, voucher_no: str) -> bool:
	return voucher_type == "Stock Entry" and frappe.get_cached_value(
		"Stock Entry", voucher_no, "purpose") == "Material Transfer"


#: where the GL books the cost of stock that left: Cost of Goods Sold, or -- where the company holds a
#: delivered-but-unbilled note's cost until it is invoiced (sdbnb.py) -- Stock Delivered But Not Billed
COST_ACCOUNT_TYPES = ("Cost of Goods Sold", "Stock Delivered But Not Billed")


def gl_cogs(voucher_type: str, voucher_no: str) -> float:
	"""What the GL booked for the cost of this voucher's stock (COGS, or SDBNB on a Delivery Note)."""
	row = frappe.db.sql(
		"""select coalesce(sum(gl.debit - gl.credit), 0) from `tabGL Entry` gl
		join `tabAccount` a on a.name = gl.account and a.account_type in %s
		where gl.voucher_type = %s and gl.voucher_no = %s and gl.is_cancelled = 0""",
		(COST_ACCOUNT_TYPES, voucher_type, voucher_no),
	)
	return row[0][0] if row else 0
