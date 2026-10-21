# sf_trading/tests/test_purchase_receipt_gate.py
"""Disable Purchase Receipt (GRN) stops new and changed receipts, never the sight of the ones that exist
(sf_trading/purchase_receipt_gate.py).

    bench --site <site> run-tests --module sf_trading.tests.test_purchase_receipt_gate

The settings are patched at the module's own seam, so nothing is created or read.
"""

import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import purchase_receipt_gate as gate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OPEN_PTYPES = ("read", "select", "print", "email", "report", "export", "share")


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


def settings(disabled=1, roster=()):
	return frappe._dict(disable_purchase_receipt=disabled,
		purchase_receipt_bypass=[frappe._dict(override_type=t, override=o) for t, o in roster])


class Site:
	"""A site whose GRN switch and roster are as given, and a user with the roles given."""

	def __init__(self, disabled=1, roster=(), roles=(), user="buyer@example.com"):
		self.settings, self.roles, self.user = settings(disabled, roster), list(roles), user
		self.patches = []

	def __enter__(self):
		for target, name, value in [(gate, "_settings", lambda: self.settings), (gate.frappe, "get_roles", lambda user=None: self.roles)]:
			p = patch.object(target, name, side_effect=value, create=True)
			p.start()
			self.patches.append(p)
		p = patch.object(gate.frappe, "session", frappe._dict(user=self.user), create=True)  # the session user, when no user is named
		p.start()
		self.patches.append(p)
		return self

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()


class TestWhatABlockedUserMayDo(FrappeTestCase):
	def test_looking_is_never_closed(self):
		with Site():
			for ptype in OPEN_PTYPES:
				self.assertIsNone(gate.has_permission(None, ptype, "buyer@example.com"), ptype)

	def test_making_or_changing_a_receipt_is_closed(self):
		with Site():
			for ptype in gate.CLOSED:
				self.assertFalse(gate.has_permission(None, ptype, "buyer@example.com"), ptype)
		self.assertEqual(set(gate.CLOSED), {"create", "write", "submit", "cancel", "amend", "delete", "import"})

	def test_cancelling_an_existing_receipt_is_closed(self):
		with Site():
			self.assertFalse(gate.has_permission(frappe._dict(name="MAT-PRE-1", docstatus=1), "cancel", "buyer@example.com"))

	def test_nothing_is_closed_while_the_switch_is_off(self):
		with Site(disabled=0):
			for ptype in gate.CLOSED + OPEN_PTYPES:
				self.assertIsNone(gate.has_permission(None, ptype, "buyer@example.com"), ptype)

	def test_the_roster_and_administrator_may_still_do_everything(self):
		with Site(roster=[("User", "keeper@example.com"), ("Role", "Stores Head")], roles=["Stores Head"]):
			for user in ("keeper@example.com", "Administrator"):
				for ptype in gate.CLOSED:
					self.assertIsNone(gate.has_permission(None, ptype, user), (user, ptype))
			# a role on the roster counts for a user who holds it
			self.assertIsNone(gate.has_permission(None, "create", "head@example.com"))
		with Site(roster=[("Role", "Stores Head")], roles=["Stock User"]):
			self.assertFalse(gate.has_permission(None, "create", "buyer@example.com"))

	def test_no_ptype_is_a_look_not_a_change(self):
		with Site():
			self.assertIsNone(gate.has_permission(None, None, "buyer@example.com"))

	def test_a_missing_settings_record_closes_nothing(self):
		with Site() as s:
			s.settings = None
			for ptype in gate.CLOSED:
				self.assertIsNone(gate.has_permission(None, ptype, "buyer@example.com"), ptype)


class TestWhatTheDeskIsTold(FrappeTestCase):
	def boot(self):
		keys = ("can_create", "can_write", "can_cancel", "can_delete", "can_import", "can_read", "can_select", "can_search",
			"can_get_report", "can_export", "can_print", "can_email")
		return {"user": {k: ["Purchase Receipt", "Purchase Order"] for k in keys}}

	def test_a_blocked_user_loses_create_and_change_but_keeps_every_way_of_looking(self):
		boot = self.boot()
		with Site():
			gate.boot_session(boot)
		for key in ("can_create", "can_write", "can_cancel", "can_delete", "can_import"):
			self.assertEqual(boot["user"][key], ["Purchase Order"], key)
		for key in ("can_read", "can_select", "can_search", "can_get_report", "can_export", "can_print", "can_email"):
			self.assertEqual(boot["user"][key], ["Purchase Receipt", "Purchase Order"], key)
		self.assertEqual(boot["sf_grn_disabled"], 1)

	def test_an_exempt_user_or_a_site_with_the_switch_off_is_told_nothing(self):
		for site in (Site(disabled=0), Site(roster=[("User", "keeper@example.com")], user="keeper@example.com")):
			boot = self.boot()
			with site:
				gate.boot_session(boot)
			self.assertEqual(boot, self.boot())


class TestWiring(FrappeTestCase):
	"""The wiring is part of the control: a hook left registered, or a summary that says the old thing."""

	def hooks(self):
		return read("hooks.py")

	def test_the_permission_hook_is_registered_and_the_list_filter_is_gone(self):
		text = self.hooks()
		self.assertIn('"Purchase Receipt": "sf_trading.purchase_receipt_gate.has_permission"', text)
		self.assertNotIn("purchase_receipt_gate.permission_query_conditions", text)

	def test_the_old_function_answers_no_condition_for_a_bench_still_naming_it(self):
		# between a pull and the hooks reload the cached hooks.py still calls it: it must not fail, nor filter
		with Site():
			self.assertEqual(gate.permission_query_conditions("buyer@example.com"), "")

	def test_the_receipt_form_script_is_registered_and_takes_the_buttons_away(self):
		self.assertIn('"public/js/purchase_receipt_gate.js"', self.hooks())
		js = read("public", "js", "purchase_receipt_gate.js")
		for needle in ("frappe.boot.sf_grn_disabled", "clear_primary_action", 'remove_custom_button(__("Purchase Return")', "disable_save"):
			self.assertIn(needle, js)

	def test_the_desk_is_told_and_the_boot_cache_follows_the_settings(self):
		text = self.hooks()
		self.assertIn("sf_trading.purchase_receipt_gate.boot_session", text)
		self.assertIn("sf_trading.purchase_receipt_gate.clear_boot_cache", text)

	def test_the_settings_say_receipts_stay_visible(self):
		fields = {f["fieldname"]: f for f in json.loads(read("sf_trading", "doctype", "sf_trading_settings", "sf_trading_settings.json"))["fields"]}
		text = fields["disable_purchase_receipt"]["description"]
		for needle in ("no new Purchase Receipt", "cancelled", "stay visible", "Landed Cost Voucher"):
			self.assertIn(needle, text)
		self.assertNotIn("cannot be opened", text)
		self.assertIn("make, change and cancel", fields["purchase_receipt_bypass"]["description"])
