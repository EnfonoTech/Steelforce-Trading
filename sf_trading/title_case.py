# sf_trading/title_case.py
"""Customer and Supplier names in Title Case -- the name AND the ID ("camel casing").

Steel Force Trading names customers and suppliers by their name (Selling Settings "Customer Naming By" =
Customer Name, the same for suppliers), so the ID IS the name a clerk typed. The Title Case that
used to run on Customer / Supplier `validate` only fixed the display name, and by then the ID had been
made from what was typed: a customer typed THE DAILY BREAD BAKERY WLL was shown as "The Daily Bread Bakery
Wll" and found in every link field as THE DAILY BREAD BAKERY WLL.

So the rule runs where the ID is made, and again where the name is edited:

* `before_insert`  -- before Frappe names the document, so the ID comes out in Title Case;
* `validate`       -- every save keeps the display name in Title Case (an old customer's ID is left alone:
                      only a rename changes an ID, and only a rename that goes through `before_rename`);
* `before_rename`  -- a rename from the desk is returned in Title Case too, so an ID cannot be put back
                      the old way by hand. A merge keeps the name of the record it merges into.

The rule: every run of letters gets a capital first letter and the rest lower case. A digit, a space
or any mark ends a run, so "W.L.L" stays "W.L.L", "AL-AWANI & SONS" becomes "Al-Awani & Sons" and
"E2E" stays "E2E". An apostrophe between two letters is part of the word ("O'BRIEN" is "O'Brien",
"JOHN'S" is "John's"). Letters of any script count as letters, so an Arabic name has nothing to change
and a Latin letter with an accent keeps its word whole. Runs of spaces are collapsed to one.

Switch: SF Trading Settings > "Save Customer and Supplier Names in Title Case". Ticked by default; a site that
never set it counts as ticked. Untick it and nothing here does anything.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, cstr

SETTINGS = "SF Trading Settings"
SWITCH = "title_case_masters"

#: the field each master takes its name from, and the global default that makes the name its ID
NAME_FIELD = {"Customer": "customer_name", "Supplier": "supplier_name"}
NAMED_BY = {"Customer": ("cust_master_name", "Customer Name"), "Supplier": ("supp_master_name", "Supplier Name")}


def title_case(text) -> str:
	"""`text` in Title Case, by the rule in the module docstring."""
	words = " ".join(cstr(text).split())
	out = []
	capital_next = True
	apostrophe_used = False
	for i, ch in enumerate(words):
		if ch.isalpha():
			out.append(ch.upper() if capital_next else ch.lower())
			capital_next = False
		elif (
			ch in "'’"
			and not capital_next
			and not apostrophe_used
			and i + 1 < len(words)
			and words[i + 1].isalpha()
		):
			# O'Brien, John's: the word goes on
			out.append(ch)
			apostrophe_used = True
		else:
			out.append(ch)
			capital_next = True
			apostrophe_used = False
	return "".join(out)


def switched_on() -> bool:
	"""The settings switch; a site that never set it counts as on (a Single never receives a new field's default)."""
	value = frappe.db.get_single_value(SETTINGS, SWITCH)
	return True if value is None else bool(cint(value))


def title_case_party(doc, method=None):
	"""before_insert and validate on Customer / Supplier: the name in Title Case.

	On insert this runs before the ID is made, so a customer or supplier named by its name gets a
	Title Case ID. On a save of an existing one it fixes the display name only.
	"""
	field = NAME_FIELD.get(doc.doctype)
	if not field or not switched_on():
		return
	name = doc.get(field)
	if not name:
		return
	fixed = title_case(name)
	if fixed != name:
		setattr(doc, field, fixed)


def title_case_rename(doc, method=None, old=None, new=None, merge=False):
	"""before_rename on Customer / Supplier: the new ID in Title Case, when the ID is the name.

	Returned, not set: Frappe takes the name a before_rename hook returns as the new one. A customer
	named by a naming series is left as given (CUST-0001 is not a name), and so is a merge, which must
	keep the spelling of the record it merges into.
	"""
	if merge or not new or doc.doctype not in NAMED_BY or not switched_on():
		return None
	default, by_name = NAMED_BY[doc.doctype]
	if frappe.defaults.get_global_default(default) != by_name:
		return None
	fixed = title_case(new)
	return fixed if fixed and fixed != new else None
