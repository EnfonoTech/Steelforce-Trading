# sf_trading/tests/test_party_alert.py
"""The early alert on a voucher's customer / supplier (sf_trading/party_alert.py).

    bench --site <site> run-tests --module sf_trading.tests.test_party_alert
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import hooks
from sf_trading import party_alert as pa
from sf_trading.sales_order_governance import CASH_OVERRIDE_FIELD


def _registered(doctype) -> list:
	"""Every handler hooks.py registers on a doctype, whatever the event."""
	handlers = []
	for event in hooks.doc_events.get(doctype, {}).values():
		handlers += [event] if isinstance(event, str) else list(event)
	return handlers


def _is_party_gate(path: str) -> bool:
	"""The naming the gates share: a check at the point of billing / buying / paying."""
	name = path.rsplit(".", 1)[-1]
	return name.endswith("_at_transaction") or name.endswith("approved_for_invoicing")


class TestGatesMirrorHooks(unittest.TestCase):
	"""The alert asks the gates hooks.py registers. A gate added there and not here would refuse
	with no warning; one listed here and not registered would warn about nothing."""

	def test_every_listed_gate_is_registered_on_that_voucher(self):
		for doctype, gates in pa.GATES.items():
			for gate in gates:
				self.assertIn(gate, _registered(doctype), f"{gate} is not a hook on {doctype}")

	def test_every_registered_party_gate_is_listed(self):
		for doctype in hooks.doc_events:
			registered = {path for path in _registered(doctype) if _is_party_gate(path)}
			self.assertEqual(registered, set(pa.GATES.get(doctype, ())), f"party gates on {doctype}")

	def test_every_gated_voucher_names_its_party_field(self):
		self.assertEqual(set(pa.PARTY_FIELD), set(pa.GATES))


class TestAsking(FrappeTestCase):
	def test_a_refusal_is_returned_and_the_popup_it_queued_is_dropped(self):
		before = list(frappe.local.message_log)

		def gate(doc):
			frappe.throw("Supplier <b>X</b> is not complete", title="Supplier Details Incomplete")

		self.assertEqual(pa._refusal(gate, frappe._dict()), "Supplier <b>X</b> is not complete")
		self.assertEqual(frappe.local.message_log, before)

	def test_a_gate_that_lets_the_voucher_through_says_nothing(self):
		self.assertIsNone(pa._refusal(lambda doc: None, frappe._dict()))

	def test_a_gate_that_breaks_is_ignored_rather_than_shown(self):
		def gate(doc):
			raise KeyError("boom")

		self.assertIsNone(pa._refusal(gate, frappe._dict()))

	def test_the_form_header_loses_what_is_not_a_header(self):
		# any doctype will do: only the filtering of the form's fields is under test
		doc = pa._voucher(
			"ToDo",
			{
				"description": "S-1",
				"priority": "Low",
				"rows": [{"a": 1}],
				"__unsaved": 1,
				"__islocal": 1,
				"reference_name": None,
			},
		)
		self.assertEqual(doc.doctype, "ToDo")
		self.assertEqual(doc.description, "S-1")
		self.assertFalse(doc.get("rows"))
		self.assertFalse(doc.get("__unsaved"))

	def _alert(self, doctype, values, fakes, override=0):
		def get_value(doctype, name, fieldname=None, *args, **kwargs):
			return override if fieldname == CASH_OVERRIDE_FIELD else "Acme"

		with (
			patch.object(pa, "_voucher", side_effect=lambda doctype, values: frappe._dict(values)),
			patch.object(pa.frappe, "get_attr", side_effect=lambda path: fakes[path]),
			patch.object(pa.frappe.db, "exists", return_value=True),
			patch.object(pa.frappe.db, "get_value", side_effect=get_value),
			patch.object(pa.frappe, "has_permission", return_value=True),
			patch.dict(pa.GATES, {doctype: tuple(fakes)}),
		):
			return pa.get_party_alert(doctype, values)

	def test_every_refusing_gate_is_reported_once_and_a_quiet_one_is_not(self):
		def refuses(text):
			def gate(doc):
				frappe.throw(text)

			return gate

		fakes = {"a.gate": refuses("one"), "b.gate": lambda doc: None, "c.gate": refuses("two"), "d.gate": refuses("one")}
		result = self._alert("Purchase Invoice", {"supplier": "S-1"}, fakes)
		self.assertEqual(result["party"], "S-1")
		self.assertEqual(result["party_type"], "Supplier")
		self.assertEqual(result["problems"], ["one", "two"])
		self.assertEqual(result["hints"], [])
		self.assertNotIn("can_fix", result)  # a popup, nothing to edit from it

	def test_a_customer_voucher_is_asked_about_its_customer(self):
		result = self._alert("Sales Invoice", {"customer": "C-1"}, {"a.gate": lambda doc: None})
		self.assertEqual((result["party_type"], result["party"], result["problems"]), ("Customer", "C-1", []))

	def test_no_party_no_question(self):
		self.assertEqual(self._alert("Purchase Order", {"supplier": ""}, {"a.gate": lambda doc: frappe.throw("x")})["problems"], [])

	def test_a_payment_entry_is_only_asked_about_a_supplier(self):
		gate = {"a.gate": lambda doc: frappe.throw("x")}
		self.assertEqual(self._alert("Payment Entry", {"party": "C-1", "party_type": "Customer"}, gate)["problems"], [])
		self.assertEqual(
			self._alert("Payment Entry", {"party": "S-1", "party_type": "Supplier"}, gate)["problems"], ["x"]
		)

	def test_a_voucher_no_gate_covers_is_not_asked(self):
		self.assertEqual(pa.get_party_alert("Journal Entry", {}), {"party": None, "problems": []})

	# ─── a credit customer and the Payment Mode ───────────────────────────────────────────────────

	def _credit_gate(self, doc):
		"""The credit-customer gate as sales_order_governance runs it for a customer whose documents are
		missing: refused unless the sale is Cash and the customer may buy for Cash without them."""
		if doc.get("custom_payment_mode") == "Cash" and doc.get("_may_buy_for_cash"):
			return
		frappe.throw("Credit customer C-1 is missing: Payment Terms")

	def _ask_credit(self, mode, override):
		values = {"customer": "C-1", "_may_buy_for_cash": override}
		if mode is not None:
			values["custom_payment_mode"] = mode
		return self._alert("Sales Invoice", values, {pa.CREDIT_GATE: self._credit_gate}, override=override)

	def test_a_cash_sale_to_a_credit_customer_allowed_cash_sales_raises_nothing(self):
		result = self._ask_credit("Cash", override=1)
		self.assertEqual((result["problems"], result["hints"]), ([], []))

	def test_the_server_answers_for_the_document_as_given_a_blank_mode_is_not_cash(self):
		# the form decides what to ask when no mode is picked yet (party_gate_alert.js); here it is literal
		result = self._ask_credit(None, override=1)
		self.assertEqual(result["problems"], ["Credit customer C-1 is missing: Payment Terms"])
		self.assertEqual(result["hints"], ["This customer may buy for Cash until these are completed."])

	def test_credit_or_cheque_for_that_customer_is_refused_and_says_cash_is_open(self):
		for mode in ("Credit", "Cheque"):
			result = self._ask_credit(mode, override=1)
			self.assertEqual(result["problems"], ["Credit customer C-1 is missing: Payment Terms"], mode)
			self.assertEqual(result["hints"], ["This customer may buy for Cash until these are completed."], mode)

	def test_a_credit_customer_not_allowed_cash_sales_is_refused_in_every_mode_with_no_hint(self):
		for mode in (None, "Cash", "Credit", "Cheque"):
			result = self._ask_credit(mode, override=0)
			self.assertEqual(result["problems"], ["Credit customer C-1 is missing: Payment Terms"], repr(mode))
			self.assertEqual(result["hints"], [], repr(mode))

	def test_a_supplier_voucher_is_never_given_a_mode(self):
		seen = {}

		def gate(doc):
			seen["mode"] = doc.get("custom_payment_mode")

		self._alert("Purchase Invoice", {"supplier": "S-1"}, {"a.gate": gate})
		self.assertIsNone(seen["mode"])
