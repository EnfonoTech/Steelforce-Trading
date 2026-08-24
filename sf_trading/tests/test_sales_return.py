"""Tests for the sales return window and the return approval gate, both now sf_trading's own.

The decisions are functions of SF Trading Settings and a few fields on the document, so the
documents here are plain dicts — what is under test is the decision, and that permission_manager's
engine gets the right answer from our resolver.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_sales_return
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from sf_trading import sales_return

OVERRIDE_ROLE = "_Test SF Return Override Role"
OVERRIDE_USER = "_test_sf_return_override@example.com"
PLAIN_USER = "_test_sf_return_plain@example.com"


class TestSalesReturn(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Role", OVERRIDE_ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": OVERRIDE_ROLE}).insert()
		for email in (OVERRIDE_USER, PLAIN_USER):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": email.split("@")[0],
						"send_welcome_email": 0,
					}
				).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.configure(window=0, approval=0)

	def configure(self, window=1, days=7, counted_from=sales_return.FROM_INVOICE, overrides=None,
	              approval=1, by_amount=1, threshold=500, mandatory=1):
		settings = frappe.get_single("SF Trading Settings")
		settings.restrict_sales_return = window
		settings.sales_return_days = days
		settings.sales_return_days_from = counted_from
		settings.set("sales_return_overrides", [])
		for override_type, override in overrides or []:
			settings.append(
				"sales_return_overrides", {"override_type": override_type, "override": override}
			)
		settings.si_return_approval_enabled = approval
		settings.si_return_amount_restriction = by_amount
		settings.si_return_approval_threshold = threshold
		settings.si_return_requires_workflow = mandatory
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="SF Trading Settings")

	def make_return(self, amount=1000, posting_date=None, against="SINV-TEST-0001"):
		return frappe._dict(
			doctype="Sales Invoice",
			docstatus=0,
			is_return=1,
			return_against=against,
			posting_date=posting_date or nowdate(),
			base_grand_total=-amount,
			company="_Test Company",
		)

	# ── the window ───────────────────────────────────────────────────────────
	def test_off_by_default_lets_anything_through(self):
		self.configure(window=0)
		doc = self.make_return()
		with patch.object(sales_return, "_original_date", return_value=getdate(add_days(nowdate(), -400))):
			sales_return.validate_return_window(doc)

	def test_inside_the_window_is_allowed(self):
		self.configure(days=7)
		doc = self.make_return()
		with patch.object(sales_return, "_original_date", return_value=getdate(add_days(nowdate(), -3))):
			self.assertEqual(sales_return.age_in_days(doc), 3)
			sales_return.validate_return_window(doc)

	def test_past_the_window_is_refused(self):
		self.configure(days=7)
		doc = self.make_return()
		with patch.object(sales_return, "_original_date", return_value=getdate(add_days(nowdate(), -8))):
			with patch.object(sales_return, "may_override", return_value=False):
				self.assertRaises(frappe.ValidationError, sales_return.validate_return_window, doc)

	def test_an_override_is_told_it_is_overriding(self):
		"""Silence is what made this look switched off to whoever tests as Administrator."""
		self.configure(days=1)
		doc = self.make_return()
		with patch.object(sales_return, "_original_date", return_value=getdate(add_days(nowdate(), -40))):
			frappe.clear_messages()
			sales_return.validate_return_window(doc)  # Administrator: allowed
			titles = [m.get("title") for m in frappe.get_message_log()]
			self.assertIn("Return Window Overridden", titles)

	def test_a_return_naming_no_invoice_is_left_alone(self):
		self.configure(days=0)
		doc = self.make_return(against=None)
		self.assertIsNone(sales_return.age_in_days(doc))
		sales_return.validate_return_window(doc)

	def test_posting_date_basis_measures_from_today(self):
		self.configure(days=2, counted_from=sales_return.FROM_POSTING)
		self.assertEqual(
			sales_return.age_in_days(self.make_return(posting_date=add_days(nowdate(), -2))), 2
		)
		with patch.object(sales_return, "may_override", return_value=False):
			self.assertRaises(
				frappe.ValidationError,
				sales_return.validate_return_window,
				self.make_return(posting_date=add_days(nowdate(), -3)),
			)

	def test_a_named_role_may_override(self):
		self.configure(days=1, overrides=[("Role", OVERRIDE_ROLE)])
		frappe.get_doc("User", OVERRIDE_USER).add_roles(OVERRIDE_ROLE)
		frappe.set_user(OVERRIDE_USER)
		self.assertTrue(sales_return.may_override())

	def test_a_user_not_named_may_not(self):
		self.configure(days=1, overrides=[("User", OVERRIDE_USER)])
		frappe.set_user(PLAIN_USER)
		self.assertFalse(sales_return.may_override())

	# ── the approval ─────────────────────────────────────────────────────────
	def test_above_the_threshold_needs_approval(self):
		self.configure(threshold=500)
		self.assertTrue(sales_return.needs_approval(self.make_return(500.001)))
		self.assertFalse(sales_return.needs_approval(self.make_return(500)))

	def test_without_the_amount_restriction_every_return_needs_approval(self):
		self.configure(by_amount=0)
		self.assertTrue(sales_return.needs_approval(self.make_return(1)))

	def test_an_ordinary_invoice_never_needs_approval(self):
		self.configure(by_amount=0)
		plain = frappe._dict(doctype="Sales Invoice", is_return=0, base_grand_total=1000000)
		self.assertFalse(sales_return.needs_approval(plain))

	def test_approval_off_means_no_opinion_at_all(self):
		self.configure(approval=0)
		self.assertIsNone(sales_return.workflow_applicability("Sales Invoice", self.make_return(9999)))

	# ── what permission_manager is told ──────────────────────────────────────
	def test_the_resolver_narrows_sales_invoice_to_big_returns(self):
		self.configure(threshold=500)
		big = sales_return.workflow_applicability("Sales Invoice", self.make_return(600))
		small = sales_return.workflow_applicability("Sales Invoice", self.make_return(100))
		plain = sales_return.workflow_applicability(
			"Sales Invoice", frappe._dict(doctype="Sales Invoice", is_return=0, base_grand_total=9000)
		)
		self.assertTrue(big["applies"])
		self.assertTrue(big["guard_submit"])
		self.assertTrue(big["require_workflow"])
		self.assertFalse(small["applies"])
		self.assertFalse(plain["applies"])
		# and the doctype is flagged as only partly routed, so it stays off the boot list
		self.assertTrue(big["conditional"])

	def test_no_opinion_on_other_doctypes(self):
		self.configure()
		self.assertIsNone(sales_return.workflow_applicability("Journal Entry", frappe._dict(doctype="Journal Entry")))

	def test_a_doctype_level_question_is_answered_conditionally(self):
		"""No document in hand: say the doctype is partly routed, claim nothing about applying."""
		self.configure()
		verdict = sales_return.workflow_applicability("Sales Invoice", None)
		self.assertTrue(verdict["conditional"])
		self.assertNotIn("applies", verdict)

	def test_the_engine_agrees_with_the_resolver(self):
		from permission_manager.permission_manager.workflow import workflow_applies

		self.configure(threshold=500)
		self.assertTrue(workflow_applies("Sales Invoice", self.make_return(600)))
		self.assertFalse(workflow_applies("Sales Invoice", self.make_return(100)))
		self.assertTrue(workflow_applies("Journal Entry", frappe._dict(doctype="Journal Entry")))

	def test_mandatory_workflow_can_be_turned_off(self):
		self.configure(threshold=500, mandatory=0)
		verdict = sales_return.workflow_applicability("Sales Invoice", self.make_return(600))
		self.assertTrue(verdict["guard_submit"])
		self.assertFalse(verdict["require_workflow"])
