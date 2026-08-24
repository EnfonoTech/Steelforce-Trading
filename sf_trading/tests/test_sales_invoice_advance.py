"""Tests for automatic Sales Order advance allocation on Sales Invoice.

The switches are a decision about the document in hand, so the documents here are stubs — what
matters is which invoice gets ERPNext's two advance switches turned on, and which does not.
The purchase-side twin (test_advance_allocation) covers the end-to-end allocation itself.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_sales_invoice_advance
"""

from frappe.tests.utils import FrappeTestCase

from sf_trading.overrides.sales_invoice_advance import set_advance_allocation


class StubInvoice(dict):
	"""Just enough of a document for the hook: field access, .get() and .is_new()."""

	def __init__(self, new=True, **fields):
		super().__init__(**fields)
		self._new = new

	def is_new(self):
		return self._new

	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		if key == "_new":
			super().__setattr__(key, value)
		else:
			self[key] = value


def invoice(**fields):
	fields.setdefault("items", [])
	fields.setdefault("advances", [])
	return StubInvoice(**fields)


class TestSalesInvoiceAdvanceAllocation(FrappeTestCase):
	def test_invoice_from_an_order_keeps_both_switches_on(self):
		doc = invoice(items=[{"sales_order": "SO-0001"}])
		set_advance_allocation(doc)
		self.assertEqual(doc.allocate_advances_automatically, 1)
		self.assertEqual(doc.only_include_allocated_payments, 1)

	def test_invoice_naming_no_order_turns_them_off(self):
		"""Otherwise set_advances sweeps every on-account customer payment in, FIFO."""
		doc = invoice(items=[{"item_code": "X"}])
		set_advance_allocation(doc)
		self.assertEqual(doc.allocate_advances_automatically, 0)
		self.assertEqual(doc.only_include_allocated_payments, 0)

	def test_a_pos_invoice_does_not_claim_to_allocate(self):
		"""accounts_controller skips set_advances entirely when is_pos is set."""
		doc = invoice(items=[{"sales_order": "SO-0001"}], is_pos=1)
		set_advance_allocation(doc)
		self.assertEqual(doc.allocate_advances_automatically, 0)

	def test_a_return_does_not_consume_an_advance(self):
		doc = invoice(items=[{"sales_order": "SO-0001"}], is_return=1)
		set_advance_allocation(doc)
		self.assertEqual(doc.allocate_advances_automatically, 0)
		self.assertEqual(doc.only_include_allocated_payments, 0)

	def test_rows_somebody_already_fetched_are_left_alone(self):
		doc = invoice(items=[{"item_code": "X"}], advances=[{"reference_name": "PE-0001"}])
		set_advance_allocation(doc)
		self.assertNotIn("allocate_advances_automatically", doc)

	def test_a_saved_invoice_is_never_touched_again(self):
		"""Anyone who unticks a box and saves again keeps that choice."""
		doc = invoice(items=[{"sales_order": "SO-0001"}], new=False)
		set_advance_allocation(doc)
		self.assertNotIn("allocate_advances_automatically", doc)
