# sf_trading/item_case.py
"""The Item master is kept in CAPITALS: Item Code (for a new item) and Item Name, always.

On sft prod 16,717 of 16,748 item names and 16,739 of 16,748 item codes are already ALL UPPER CASE; the exceptions are 31 names and 9 codes,
nearly all Services items typed by hand since July ("Office Expenses", "Air Fare -Employees", "Gift,Cake and Hampers" ...). The owner asked for
the whole master to be capitalised, existing entries and future ones.

Future entries -- two doc events on Item, one function:

* `before_insert`: runs before Frappe names the document (`Document.insert`: before_insert, then set_new_name), so a stock item's manually typed
  Item Code becomes the record name already in capitals. A non-stock item is named by item_naming.autoname (SVC-.#####), which is capitals anyway.
* `before_validate`: keeps Item Name in capitals on every save, so editing an old mixed-case item fixes it on the spot, and ERPNext's own
  defaults (description from the name, name from the code) are filled from the capitalised text.

The Item Code of an EXISTING item is never touched here: it is the record's name, and changing a name is a rename (patches/v0_1/uppercase_existing_items.py
does that, once, for the nine that need it).

Leading and trailing spaces go too ("Customs Duty " had one). Nothing inside the text is reshaped: no collapsing of double spaces, no change to
non-breaking spaces or symbols, only case, so a name that is already in capitals is returned unchanged. The Item description (a rich-text
field) is left to the user; when it is empty ERPNext fills it from the capitalised name.

`SF Trading Settings > Capitalise the Item master` turns this off at once, no restart; a switch nobody stored is ON (switches.py).
"""

from sf_trading import switches

SWITCH = "uppercase_item_master"


def capitals(text):
	"""`text` stripped and in capitals; None / blank stays as it is."""
	if not text or not isinstance(text, str):
		return text
	return text.strip().upper()


def capitalise_item(doc, method=None):
	"""before_insert and before_validate on Item."""
	if not switches.is_on(SWITCH):
		return
	if doc.get("item_name"):
		doc.item_name = capitals(doc.item_name)
	if doc.is_new() and doc.get("item_code"):
		doc.item_code = capitals(doc.item_code)
