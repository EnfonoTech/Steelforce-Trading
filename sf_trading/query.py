# sf_trading/query.py
"""Reading a long list of names without handing the database one enormous query.

Frappe 15.114 added `DatabaseQuery.validate_generated_query`, which runs `sqlparse.parse()` over
every query `frappe.get_all` / `frappe.get_list` builds, and sqlparse refuses any statement over
10,000 tokens. A `{"name": ("in", [...])}` filter spends about two tokens per value, so a read of
roughly five thousand names or more now dies before it reaches MariaDB:

    sqlparse.exceptions.SQLParseError: Maximum number of tokens exceeded (10000).

It surfaced on production the moment the bench moved from 15.112 to 15.114 -- a report of 5,055
invoices could no longer take a custom column -- and it applies to every read of that shape, not
only that one. Batching keeps each generated statement small, and the caller gets the same rows
back in one list either way.
"""

import frappe

# 500 names is roughly a thousand tokens, comfortably inside the cap with the rest of the
# statement, and still only a handful of round trips for a report-sized page of rows.
IN_BATCH = 500


def fetch_in(
	doctype: str,
	values,
	fields=None,
	in_field: str = "name",
	filters: dict = None,
	pluck: str = None,
	as_list: bool = False,
	permissions: bool = False,
	batch: int = IN_BATCH,
) -> list:
	"""Rows of `doctype` whose `in_field` is one of `values`, read a batch at a time.

	Args:
		values: the list to match; duplicates and blanks are dropped
		fields: as for frappe.get_all -- ignored when `pluck` is given
		filters: extra filters applied to every batch
		pluck: return a flat list of this one field
		as_list: return rows as lists rather than dicts (for name/value pairs)
		permissions: read through frappe.get_list (permissions enforced) instead of get_all
		batch: values per query

	An empty `values` returns an empty list rather than reading the whole table -- the opposite
	mistake, and a far more expensive one.
	"""
	values = list(dict.fromkeys(value for value in (values or []) if value))
	if not values:
		return []

	reader = frappe.get_list if permissions else frappe.get_all
	rows = []
	for start in range(0, len(values), max(1, batch)):
		chunk = values[start : start + max(1, batch)]
		where = dict(filters or {})
		where[in_field] = ("in", chunk)
		kwargs = {"filters": where, "limit_page_length": 0}
		if pluck:
			kwargs["pluck"] = pluck
		else:
			kwargs["fields"] = fields or ["name"]
			if as_list:
				kwargs["as_list"] = 1
		rows.extend(reader(doctype, **kwargs))
	return rows


def fetch_in_map(doctype: str, values, fields, in_field: str = "name", **kwargs) -> dict:
	"""The same read, keyed by the matched field -- the shape most callers actually want."""
	key = in_field.split(" as ")[-1]
	return {row.get(key): row for row in fetch_in(doctype, values, fields=fields, in_field=in_field, **kwargs)}
