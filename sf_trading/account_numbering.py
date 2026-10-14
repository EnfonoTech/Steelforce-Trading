# sf_trading/account_numbering.py
"""Number a new Account the moment it is made, in order, the way its parent's other accounts are numbered.

Steel Force's chart is numbered, but only what was imported or typed with a number is. An account
made since by a person or by the party hooks (party_accounts.py makes a payable ledger for every
supplier and a receivable one for a credit customer) came out with no number at all, so the
Trial Balance sorts it apart from its group. `number_new_account` fills the number in on
`Account.before_insert`, i.e. before ERPNext builds the account's name from it ("<number> - <name> - <abbr>").

The number comes from what the parent already holds, never from a rule written here:

* a party ledger under a parent whose ledgers are an initial-letter series (130301A0001, 220102T0006:
  group digits + the initial of the party's name + a serial) takes the next serial of ITS initial;
* anything else takes the parent's number plus the next serial of the width its other children use
  (parent 130901 with 13090100001 -> 13090100002; parent 3101 with 31010100005 -> 31010100006; a
  parent numbered 1100 whose children are 1101.. -> 1102, since a trailing 00 is a range, not a prefix);
* a parent with no numbered child yet starts a series: two digits for a group, a number of 11
  digits for a ledger (the width Steel Force's ledgers already have).

Only an account with no number, under a parent that has one, is touched; a number typed by hand
is never changed and a branch with no numbers stays as it is. A number that is taken is skipped,
so two accounts can never receive the same one. A failure is logged and the account is made without a
number: the chart is never held up by this.

`missing_number_plan` lists the accounts that already exist without a number and what each would
get (oldest first, a group before its children); `number_existing_accounts` renames them through
ERPNext's own `update_account_number`, which follows every link. ERPNext refuses that while entries
are being posted (it wants 5 quiet minutes), so a batch stops at the first refusal and says so.

Switch: SF Trading Settings > "Number New Accounts Automatically" (ticked by default; a site that never
set it counts as ticked).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import frappe
from frappe import _
from frappe.utils import cint, cstr

SETTINGS = "SF Trading Settings"
SWITCH = "number_new_accounts"

#: a party ledger number: group digits, the party's initial, a serial (220102A0002)
LETTER_SERIES = re.compile(r"^(\d+)([A-Z])(\d+)$")
#: the initial given to a name with no Latin letter in it
NO_INITIAL = "X"
#: the width of a ledger's number when its parent has no ledger to copy it from
LEDGER_DIGITS = 11
#: numbers tried before giving up on finding a free one
ATTEMPTS = 10000


# ─── the rule, on plain data ─────────────────────────────────────────────────────────────────────


class Chart:
	"""The accounts of one company, indexed the way numbering reads them."""

	def __init__(self, rows):
		self.number_of = {}
		self.children = defaultdict(list)
		for row in rows:
			self.register(row.get("name"), row.get("parent_account"), row.get("account_number"), row.get("is_group"))

	def register(self, name, parent, number, is_group):
		number = cstr(number).strip()
		self.number_of[name] = number
		self.children[parent].append((number, cint(is_group)))

	@property
	def taken(self) -> set:
		return {number for number in self.number_of.values() if number}


def number_base(number) -> str:
	"""The digits a child's number starts with: the parent's own number, or for a range
	group ("1100-1600") the number it starts at."""
	first = cstr(number).split("-")[0].strip()
	return first if first.isdigit() else ""


def initial_of(name) -> str:
	"""The first Latin letter of a name, capitalised (X when it has none)."""
	for ch in cstr(name):
		if "A" <= ch.upper() <= "Z":
			return ch.upper()
	return NO_INITIAL


def propose(chart: Chart, parent: str, account_name: str, is_group) -> str | None:
	"""The number a new account called `account_name` under `parent` should get, or None when the
	parent has no number (or none can be found)."""
	base = number_base(chart.number_of.get(parent))
	if not base:
		return None
	siblings = [(number, kind) for number, kind in chart.children.get(parent, []) if number]
	taken = chart.taken
	if not cint(is_group):
		number = _next_in_letter_series(siblings, base, account_name, taken)
		if number:
			return number
	return _next_in_serial(siblings, base, cint(is_group), taken)


def _free(make, start: int, taken: set) -> str | None:
	for serial in range(start, start + ATTEMPTS):
		candidate = make(serial)
		if candidate not in taken:
			return candidate
	return None


def _usual(widths) -> int:
	"""The width most of them have; the shorter one when it is a tie."""
	counts = Counter(widths)
	return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _next_in_letter_series(siblings, base, account_name, taken) -> str | None:
	"""A party ledger: the next serial of the party's initial, when the parent's ledgers are such a series."""
	parts = [match.groups() for match in (LETTER_SERIES.match(number) for number, _kind in siblings) if match]
	if len(parts) < 3 or len(parts) * 2 < len(siblings):
		return None
	prefixes = Counter(prefix for prefix, _letter, _serial in parts)
	# the parent's own digits when it has ledgers under them, else whatever prefix most of them carry
	prefix = base if base in prefixes else sorted(prefixes.items(), key=lambda item: (-item[1], item[0]))[0][0]
	letter = initial_of(account_name)
	width = _usual(len(serial) for pre, _letter, serial in parts if pre == prefix)
	last = max((int(serial) for pre, let, serial in parts if pre == prefix and let == letter), default=0)
	return _free(lambda serial: prefix + letter + str(serial).zfill(width), last + 1, taken)


def _next_in_serial(siblings, base, is_group, taken) -> str | None:
	"""The parent's number plus the next serial, of the width its other children (of the same kind) use."""
	for stem in _stems(base):
		tails = [(number[len(stem):], kind) for number, kind in siblings
			if number.startswith(stem) and number[len(stem):].isdigit()]
		if not tails:
			continue
		same_kind = [tail for tail, kind in tails if kind == is_group] or [tail for tail, _kind in tails]
		width = _usual(len(tail) for tail in same_kind)
		last = max(int(tail) for tail in same_kind if len(tail) == width)
		return _free(lambda serial: stem + str(serial).zfill(width), last + 1, taken)
	# the first child of its parent starts a series
	stem = _stems(base)[-1]
	width = 2 if is_group else max(3, LEDGER_DIGITS - len(stem))
	return _free(lambda serial: stem + str(serial).zfill(width), 1, taken)


def _stems(base: str) -> list:
	"""What a child's number may start with: the parent's number, then without its trailing zeros
	(1100 is the range 11xx, so its children are 1101, 1102 ...)."""
	stems = [base]
	short = base.rstrip("0")
	if short and short != base:
		stems.append(short)
	return stems


# ─── the hook ────────────────────────────────────────────────────────────────────────────────────


def switched_on() -> bool:
	"""The settings switch; a site that never set it counts as on (a Single never receives a new field's default)."""
	value = frappe.db.get_single_value(SETTINGS, SWITCH)
	return True if value is None else bool(cint(value))


def chart_of(company: str) -> Chart:
	return Chart(frappe.get_all("Account", filters={"company": company},
		fields=["name", "account_number", "parent_account", "is_group"], limit_page_length=0))


def number_new_account(doc, method=None):
	"""before_insert on Account: a number for an account made without one."""
	if cstr(doc.get("account_number")).strip() or not doc.get("parent_account") or not doc.get("company"):
		return
	if not switched_on():
		return
	try:
		number = propose(chart_of(doc.company), doc.parent_account, doc.get("account_name"), cint(doc.get("is_group")))
	except Exception:
		frappe.log_error(title=_("Account number could not be assigned"))
		return
	if number:
		doc.account_number = number


# ─── accounts that are already there ─────────────────────────────────────────────────────────────


def missing_number_plan(company: str) -> list:
	"""Every account of the company with no number under a numbered parent, and the number it would get.

	Groups first (their children are numbered from them), each in the order they were made, so the
	serials run in order. Each number is worked out with the earlier ones already in the chart.
	"""
	rows = frappe.get_all("Account", filters={"company": company},
		fields=["name", "account_name", "account_number", "parent_account", "is_group", "account_type", "creation"],
		order_by="creation asc, name asc", limit_page_length=0)
	chart = Chart(rows)
	abbr = frappe.get_cached_value("Company", company, "abbr")
	unnumbered = [row for row in rows if not cstr(row.account_number).strip() and row.parent_account]
	plan = []
	for row in sorted(unnumbered, key=lambda row: not cint(row.is_group)):  # stable: oldest first inside each kind
		number = propose(chart, row.parent_account, row.account_name, cint(row.is_group))
		if not number:
			continue
		chart.number_of[row.name] = number
		chart.children[row.parent_account].append((number, cint(row.is_group)))
		plan.append({
			"account": row.name,
			"account_name": row.account_name,
			"parent_account": row.parent_account,
			"account_type": row.account_type,
			"is_group": cint(row.is_group),
			"number": number,
			"new_name": " - ".join([number, row.account_name.strip(), abbr]),
		})
	return plan


@frappe.whitelist()
def number_existing_accounts(company: str, apply=0, limit=25) -> dict:
	"""Preview, or carry out, the numbering of the accounts that have none.

	Without `apply` nothing is written. With it, up to `limit` accounts are renamed (ERPNext's
	`update_account_number`, one commit each); a batch stops at the first refusal -- ERPNext does not
	rename while entries are being posted -- and says why, so run it again when the system is quiet.
	"""
	frappe.only_for(("System Manager", "Accounts Manager"))
	plan = missing_number_plan(company)
	if not cint(apply):
		return {"applied": [], "stopped": None, "left": len(plan), "plan": plan}

	from erpnext.accounts.doctype.account.account import update_account_number

	applied, stopped = [], None
	for item in plan[: max(1, cint(limit))]:
		try:
			update_account_number(item["account"], item["account_name"], item["number"])
		except Exception as error:
			stopped = {"account": item["account"], "reason": cstr(error)}
			frappe.db.rollback()
			break
		frappe.db.commit()
		applied.append(item)
	return {"applied": applied, "stopped": stopped, "left": len(plan) - len(applied), "plan": []}
