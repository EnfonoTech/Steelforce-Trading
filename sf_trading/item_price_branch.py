# sf_trading/item_price_branch.py
"""Branch-wise Item Price: an Item Price may name the branches it serves.

An Item Price carries an **Applicable Branches** table (Custom Field `custom_applicable_branches`,
child doctype `Item Price Branch`).

  * Empty -- every Item Price until somebody fills it -- the price serves every branch. No filter.
  * Branches named -- only a document of one of those branches is priced from it.
  * One item, one list, one UOM may then hold a price for everybody and a different price for a
    branch (or two branches with prices of their own). A document is priced from the price that
    names its branch when there is one, from the general price when there is not, and from nothing
    that is limited to other branches. A document with no branch sees general prices only.

ERPNext knows nothing of this, so three things are taught here and nothing in erpnext is edited:

1. **The lookup.** `get_price_list_rate_for` builds its own argument dict out of `args` and drops
   everything it does not know, branch included, before it asks `get_item_price`. So it is wrapped to
   remember the branch for the duration of the call, and `get_item_price` is wrapped to drop the
   prices that are limited to other branches and put the branch's own ahead of the general ones.
   Both wrap the stock function and return its answer untouched when no price names a branch, so a
   site that has not used the table behaves exactly as before. They are installed once per process
   from `before_request` / `before_job` (`install`), the way report_speed.py installs its own.

   The branch comes from `args.branch` -- the server-side re-pricing of a saved document passes the
   whole header -- and, for a call from the browser, from the `doc` ERPNext's form script sends along
   with the item (`frappe.form_dict.doc`).

2. **The duplicate check.** Two prices that agree on item, list, UOM, party, batch, packing unit and
   dates are refused. Two such prices for different branches are not duplicates, and neither are a
   branch price and the general one; only the same scope twice is. `CustomItemPrice` redoes the check
   with that rule (overrides/item_price_class.py). It is a copy of ERPNext's own, so it runs only on
   the release it was copied from (`KNOWN_CHECK_DUPLICATES`); on any other it hands back to the stock
   check and says so in logs/sf_trading.log, which refuses a branch's second price rather than
   silently doing something else.

3. **The readers in this app** (the cost guard's price-list floor, the item search, the quick entry
   grid, the per-row price list, and the Items Without Price List report) ask `rate_for` /
   `visible` instead of picking "the" Item Price of an item, which is no longer one row.

Branch Configuration still owns which price lists a branch may use and which one is its default
(branch_price_list.py). This table owns which prices inside those lists reach the branch.
"""

import contextlib
import hashlib
import inspect
import textwrap

import frappe
from frappe import _

CHILD = "Item Price Branch"
FIELD = "custom_applicable_branches"

# One `IN` list of thousands of names trips frappe 15.114's query parser ("Maximum number of
# tokens exceeded"); every by-name read here goes in slices of this size.
BATCH = 500

#: sha1 (see `fingerprint`) of ItemPrice.check_duplicates in the releases `CustomItemPrice` was copied from.
KNOWN_CHECK_DUPLICATES = {"264d23cfe59d98ef2e82598cd437404156af5e42"}  # erpnext 15.113 and 15.116

_UNSET = object()
_STATE = {"tried": False}
_STOCK: dict = {}


# ─── Reading the table ────────────────────────────────────────────────────────

def table_ready() -> bool:
	return bool(frappe.db.table_exists(CHILD))


def branches_by_price(names) -> dict:
	"""{Item Price name: {branches}} for the prices that name any. A price missing from the answer is general."""
	names = [name for name in dict.fromkeys(names or []) if name]
	if not names or not table_ready():
		return {}

	found = {}
	for start in range(0, len(names), BATCH):
		for row in frappe.get_all(
			CHILD,
			filters={"parenttype": "Item Price", "parentfield": FIELD, "parent": ["in", names[start : start + BATCH]]},
			fields=["parent", "branch"],
			ignore_permissions=True,
		):
			if row.branch:
				found.setdefault(row.parent, set()).add(row.branch)
	return found


def applies_to(branches, branch) -> bool:
	"""May a price naming `branches` serve a document of `branch`? A price naming none serves every branch."""
	return not branches or bool(branch and branch in branches)


def same_scope(mine, other) -> bool:
	"""Would two prices compete for the same documents? Both general, or sharing a branch. A general
	price and a branch price do not: the branch's own wins for that branch."""
	return (not mine and not other) or bool(set(mine) & set(other))


def visible(rows, branch, name_of=lambda row: row[0]):
	"""`rows` (Item Price rows, in the order given) as a document of `branch` sees them.

	Prices limited to other branches go; those naming this branch come ahead of the general ones,
	each group keeping the stock order (newest valid-from first, and so on).
	"""
	rows = list(rows)
	if not rows:
		return rows

	limited = branches_by_price([name_of(row) for row in rows])
	if not limited:
		return rows

	own, general = [], []
	for row in rows:
		branches = limited.get(name_of(row))
		if not branches:
			general.append(row)
		elif branch and branch in branches:
			own.append(row)
	return own + general


def rate_for(item_code, price_list, branch, **filters):
	"""The price_list_rate a document of `branch` would be priced from for this item in this list, or 0.

	`filters` narrow the Item Price rows further (selling=1, uom=...). Customer and supplier prices
	are left out: this is the general price, the one a guard or a search result may quote.
	"""
	conditions = {"item_code": item_code, "price_list": price_list, "customer": ["is", "not set"], "supplier": ["is", "not set"]}
	conditions.update(filters)
	rows = frappe.get_all(
		"Item Price",
		filters=conditions,
		fields=["name", "price_list_rate", "currency"],
		order_by="valid_from desc, creation desc",
		ignore_permissions=True,
	)
	seen = visible(rows, branch, name_of=lambda row: row.name)
	return seen[0] if seen else None


# ─── Saving ───────────────────────────────────────────────────────────────────

def validate_item_price_branches(doc, method=None):
	"""Item Price validate: no branch twice in one price."""
	seen = set()
	for row in doc.get(FIELD) or []:
		if not row.get("branch"):
			continue
		if row.branch in seen:
			frappe.throw(
				_("Branch %s is listed twice in Applicable Branches.") % frappe.bold(row.branch),
				title=_("Duplicate Branch"),
			)
		seen.add(row.branch)


# ─── Teaching ERPNext's lookup about branches ─────────────────────────────────

def branch_of(args) -> str | None:
	"""The branch the document being priced belongs to: args first, then the doc a browser call carries."""
	branch = args.get("branch") if args is not None and hasattr(args, "get") else None
	if branch:
		return branch

	form = getattr(frappe.local, "form_dict", None) or {}
	doc = form.get("doc")
	if isinstance(doc, str):
		try:
			doc = frappe.parse_json(doc)
		except Exception:
			return None
	return doc.get("branch") if isinstance(doc, dict) else None


def get_price_list_rate_for(args, item_code):
	"""Stock `get_price_list_rate_for`, run with the document's branch remembered for `get_item_price`."""
	with pricing_for(branch_of(args)):
		return _STOCK["get_price_list_rate_for"](args, item_code)


def get_item_price(args, item_code, ignore_party=False, force_batch_no=False):
	"""Stock `get_item_price`, less the prices limited to other branches, the branch's own first."""
	rows = _STOCK["get_item_price"](args, item_code, ignore_party=ignore_party, force_batch_no=force_batch_no)
	if not rows:
		return rows

	branch = getattr(frappe.local, "sf_price_branch", _UNSET)
	if branch is _UNSET:
		branch = branch_of(args)

	try:
		result = visible(rows, branch)
	except Exception:
		frappe.logger("sf_trading").exception("item_price_branch: could not read Applicable Branches; price left as ERPNext found it")
		return rows
	return tuple(result) if isinstance(rows, tuple) else result


def install(*args, **kwargs) -> None:
	"""Point ERPNext's two lookup functions at the wrappers above. Once per process; cheap for `before_request`."""
	if _STATE["tried"]:
		return
	_STATE["tried"] = True

	try:
		from erpnext.stock import get_item_details as gid
	except ImportError:
		return

	for name, wrapper in (("get_item_price", get_item_price), ("get_price_list_rate_for", get_price_list_rate_for)):
		stock = getattr(gid, name, None)
		if stock is None or stock is wrapper:
			continue
		_STOCK[name] = stock
		setattr(gid, name, wrapper)


def fingerprint(function) -> str:
	"""SHA-1 of a function's source with indentation and trailing spaces ignored; "" when unreadable."""
	try:
		source = textwrap.dedent(inspect.getsource(function))
	except (OSError, TypeError):
		return ""
	return hashlib.sha1("\n".join(line.rstrip() for line in source.strip().splitlines()).encode()).hexdigest()


@contextlib.contextmanager
def pricing_for(branch):
	"""Price as a document of `branch` for the duration (what the wrappers do around ERPNext's call)."""
	previous = getattr(frappe.local, "sf_price_branch", _UNSET)
	frappe.local.sf_price_branch = branch
	try:
		yield
	finally:
		if previous is _UNSET:
			del frappe.local.sf_price_branch
		else:
			frappe.local.sf_price_branch = previous
