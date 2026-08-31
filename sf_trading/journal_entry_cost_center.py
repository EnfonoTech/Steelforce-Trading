# sf_trading/journal_entry_cost_center.py
"""A Journal Entry row's cost centre follows the Branch on that row.

Journal Entry carries no header cost centre. Every row of `Journal Entry Account` carries its
own, and core seeds it from the child field's own default -- `"default": ":Company"` -- which
resolves to `Company.cost_center`. On this site that is **Main - SFB**, so every row a branch
user typed landed on the head-office cost centre while the same row named their Branch:
on production 879 rows across 290 entries, all four branch users, every single one on Main - SFB
with `branch` correctly filled. A Property Setter makes the field `reqd`, so the wrong value was
never merely blank -- it was pre-filled, mandatory, and looked deliberate.

`Branch` is an Accounting Dimension here, and Frappe auto-fills a row's `branch` from the user's
default `User Permission`. So the row already knows which branch it belongs to; nothing was
translating that into the cost centre. This module does, in one place, for the form and the server.

**A user with no branch is left to choose.** For someone outside every Branch Configuration
(a head-office accountant), guessing is worse than asking: the form clears the pre-filled
company default instead of writing it, and `cost_center` being mandatory makes the desk ask for a
real choice. Main - SFB stays a perfectly good answer -- it just stops being the silent one.
"""

import frappe
from frappe import _
from frappe.utils import cint

# Frappe's own auto-fill for the row's `branch` reads the DEFAULT User Permission, so the cost
# centre resolved from a user's branch has to read the same one or the two would disagree.
_CACHE_KEY = "sf_je_branch_cost_center"


def _cache() -> dict:
	"""Request-scoped memo: one Journal Entry can hold dozens of rows on the same branch."""
	if not hasattr(frappe.local, _CACHE_KEY):
		setattr(frappe.local, _CACHE_KEY, {})
	return getattr(frappe.local, _CACHE_KEY)


def branch_cost_centers(branch: str | None) -> list:
	"""Every cost centre a Branch may post to, in Branch Configuration order.

	Ordered by `idx`, not by name: SFWH lists `SFWH - SFB` first and `Main - SFB` second, and an
	alphabetical read would hand back Main for the one branch that has an explicit alternative.
	The first entry is the default; the rest are the branch's own declared alternatives, which is
	how a warehouse can still book a genuine head-office cost without leaving its branch.

	A group or disabled cost centre is dropped: GL posting rejects a group cost centre outright,
	so writing one would turn a reporting annoyance into a document that cannot be submitted.
	"""
	if not branch:
		return []
	cache = _cache()
	if branch in cache:
		return cache[branch]

	config = frappe.db.get_value("Branch Configuration", {"branch": branch}, "name")
	names = (
		frappe.get_all(
			"Branch Configuration Cost Center",
			filters={"parent": config, "parenttype": "Branch Configuration"},
			pluck="cost_center",
			order_by="idx asc",
			ignore_permissions=True,
		)
		if config
		else []
	)

	usable = []
	for name in names:
		row = frappe.get_cached_value("Cost Center", name, ["is_group", "disabled"], as_dict=True)
		if row and not cint(row.is_group) and not cint(row.disabled) and name not in usable:
			usable.append(name)

	cache[branch] = usable
	return usable


def branch_cost_center(branch: str | None) -> str | None:
	"""The cost centre a Branch posts to by default -- the first row of its configuration."""
	options = branch_cost_centers(branch)
	return options[0] if options else None


def user_branch(user: str | None = None) -> str | None:
	"""The user's own Branch -- their default User Permission, or their only one.

	This is deliberately the same resolution the desk uses to pre-fill the row's `branch` field,
	so the cost centre this module writes can never name a different branch than the row does.
	"""
	user = user or frappe.session.user
	if user in ("Administrator", "Guest"):
		return None

	rows = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Branch"},
		fields=["for_value", "is_default"],
		ignore_permissions=True,
	)
	if not rows:
		return None
	for row in rows:
		if cint(row.is_default):
			return row.for_value
	return rows[0].for_value if len(rows) == 1 else None


def _permitted(cost_center: str, user: str | None = None) -> bool:
	"""Whether this user may reference this cost centre at all.

	A User Permission on Cost Center that does not include the branch's own cost centre would
	make `validate_link` reject the value and block the whole save. Every branch user on this
	site is permitted their branch's cost centre, so this only ever guards a misconfiguration --
	and it guards it by leaving the row alone rather than by refusing the document.
	"""
	from frappe.core.doctype.user_permission.user_permission import get_user_permissions

	perms = (get_user_permissions(user) or {}).get("Cost Center") or []
	allowed = {p.get("doc") for p in perms if p.get("doc")}
	return (not allowed) or cost_center in allowed


def set_cost_center_from_branch(doc, method=None):
	"""validate on Journal Entry: every row's cost centre follows its Branch.

	`validate` and not `before_validate`: the controller's own `validate_cost_center()` runs
	inside validate and reads whatever is on the row, and a hooked handler runs AFTER the
	controller method -- so writing here is the last word without fighting core.

	Two rules, in order:
	  1. The row names a branch -> a cost centre that branch declared wins. The branch is the
	     fact the user stated; the cost centre is the bookkeeping consequence of it. A branch
	     listing several cost centres keeps all of them as valid choices.
	  2. The row names no branch -> fill from the user's own branch, but only over a value
	     nobody chose (blank, or the company default core pre-seeded). A cost centre somebody
	     actually picked is somebody's decision and is left alone.
	"""
	rows = doc.get("accounts") or []
	if not rows:
		return

	company_default = (
		frappe.get_cached_value("Company", doc.company, "cost_center") if doc.company else None
	)
	# rule 2 is a convenience for the person typing; it has no business rewriting a document
	# erpnext generated for itself (a write-off, a credit note) from whoever happened to click.
	own_cost_center = (
		None if cint(doc.get("is_system_generated")) else branch_cost_center(user_branch())
	)

	for row in rows:
		allowed = branch_cost_centers(row.get("branch"))
		if allowed:
			# `allowed`, not just the default: a branch that lists more than one cost centre has
			# declared those alternatives itself, and a row already sitting on one of them was a
			# choice. Everything else -- blank, the company default, another branch's centre --
			# is corrected to the branch's own first cost centre.
			if row.cost_center not in allowed and _permitted(allowed[0]):
				row.cost_center = allowed[0]
			continue

		if own_cost_center and (not row.cost_center or row.cost_center == company_default):
			if _permitted(own_cost_center):
				row.cost_center = own_cost_center
				# leave the row self-describing: a reader of the row should be able to see why
				# it carries this cost centre without knowing who typed it
				if not row.get("branch") and row.meta.has_field("branch"):
					row.branch = user_branch()


@frappe.whitelist()
def form_defaults() -> dict:
	"""What the Journal Entry form needs to stop pre-filling the wrong cost centre.

	Read-only, no arguments, scoped to the session user: it exposes the branch-to-cost-centre
	map every user can already read off the Branch Configuration list, plus this user's own
	branch cost centre and the company default the form must NOT silently keep.
	"""
	branches = frappe.get_all("Branch Configuration", pluck="branch", ignore_permissions=True)
	mapping = {b: branch_cost_center(b) for b in branches if b}

	mine = branch_cost_center(user_branch())
	defaults = {}
	for company in frappe.get_all("Company", fields=["name", "cost_center"], ignore_permissions=True):
		if company.cost_center:
			defaults[company.name] = company.cost_center

	return {
		"branch_cost_centers": {k: v for k, v in mapping.items() if v},
		"user_branch": user_branch(),
		"user_cost_center": mine,
		"company_defaults": defaults,
	}
