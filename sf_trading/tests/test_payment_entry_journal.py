# sf_trading/tests/test_payment_entry_journal.py
"""Get Outstanding Journal Entries on the Payment Entry (sf_trading/payment_entry_journal.py).

The database and ERPNext's own function are patched at the module's seams, so nothing is created or read:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_payment_entry_journal
"""

import json
import os
import shutil
import subprocess
import sys
import types
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import payment_entry_journal as pej

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


def row(voucher_no, outstanding, voucher_type="Journal Entry", posting_date="2026-01-15", **extra):
	return frappe._dict(voucher_no=voucher_no, voucher_type=voucher_type, outstanding_amount=outstanding, invoice_amount=outstanding,
		posting_date=posting_date, due_date=posting_date, account="130302E0001 - Easy Touch - SFB", exchange_rate=1, **extra)


def account_row(parent, debit=0, credit=0, reference_type=None):
	return frappe._dict(parent=parent, debit=debit, credit=credit, reference_type=reference_type)


ARGS = dict(company="Steel Force Trading WLL", party_type="Customer", party="Easy Touch", payment_type="Receive",
	party_account="130302E0001 - Easy Touch - SFB", posting_date="2026-10-19")


class Seams:
	"""ERPNext's function, the journal tables and the permission check, as settable values."""

	def __init__(self, core_rows=(), accounts=(), journals=()):
		self.core_rows, self.accounts, self.journals = list(core_rows), list(accounts), list(journals)
		self.calls = []
		self.patches = []
		self.saved = {}

	def core(self, args, validate=False):
		self.calls.append((dict(args), validate))
		return self.core_rows

	def get_all(self, doctype, **kwargs):
		self.calls.append((doctype, kwargs))
		return self.accounts if doctype == "Journal Entry Account" else self.journals

	def __enter__(self):
		module = types.ModuleType("erpnext.accounts.doctype.payment_entry.payment_entry")
		module.get_outstanding_reference_documents = self.core
		chain = ["erpnext", "erpnext.accounts", "erpnext.accounts.doctype", "erpnext.accounts.doctype.payment_entry"]
		for name in chain:
			self.saved[name] = sys.modules.get(name)
			sys.modules.setdefault(name, types.ModuleType(name))
		self.saved[module.__name__] = sys.modules.get(module.__name__)
		sys.modules[module.__name__] = module
		for target, attr, value in [
			(pej.frappe, "get_all", MagicMock(side_effect=self.get_all)),
			(pej.frappe, "has_permission", MagicMock(return_value=True)),
		]:
			p = patch.object(target, attr, value, create=True)
			p.start()
			self.patches.append(p)
		return self

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()
		for name, old in self.saved.items():
			if old is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = old


class TestWhichJournalsAreOffered(FrappeTestCase):
	def test_only_journals_are_offered_not_the_invoices_among_them(self):
		core = [row("ACC-SINV-1", 500, voucher_type="Sales Invoice"), row("ACC-JV-1", 28.9), row("ACC-PAY-1", 5, voucher_type="Payment Entry")]
		with Seams(core, [account_row("ACC-JV-1", debit=28.9)], [frappe._dict(name="ACC-JV-1", voucher_type="Journal Entry", user_remark="Opening", cheque_no=None, title="x", cheque_date=None)]):
			out = pej.outstanding_journal_entries(json.dumps(ARGS))
		self.assertEqual([e["voucher_no"] for e in out], ["ACC-JV-1"])

	def test_the_core_function_is_asked_for_invoices_only_and_told_not_to_message(self):
		seams = Seams([row("ACC-JV-1", 10)], [account_row("ACC-JV-1", debit=10)])
		with seams:
			pej.outstanding_journal_entries(json.dumps(dict(ARGS, get_orders_to_be_billed=True, voucher_type="Sales Invoice", voucher_no="X", vouchers=[1])))
		args, validate = seams.calls[0]
		self.assertTrue(args["get_outstanding_invoices"])
		self.assertNotIn("get_orders_to_be_billed", args)
		for key in ("voucher_type", "voucher_no", "vouchers"):
			self.assertNotIn(key, args)
		self.assertTrue(validate, "validate=True keeps core from saying 'no outstanding invoices'")

	def test_a_journal_the_entry_cannot_settle_in_its_direction_is_not_offered(self):
		# Receive settles a DEBIT against the customer; a credit journal is the other side
		accounts = [account_row("ACC-JV-DR", debit=100), account_row("ACC-JV-CR", credit=100)]
		with Seams([row("ACC-JV-DR", 100), row("ACC-JV-CR", 100)], accounts):
			out = pej.outstanding_journal_entries(json.dumps(ARGS))
		self.assertEqual([e["voucher_no"] for e in out], ["ACC-JV-DR"])
		accounts = [account_row("ACC-JV-DR", debit=100), account_row("ACC-JV-CR", credit=100)]
		with Seams([row("ACC-JV-DR", 100), row("ACC-JV-CR", 100)], accounts):
			out = pej.outstanding_journal_entries(json.dumps(dict(ARGS, payment_type="Pay", party_type="Supplier")))
		self.assertEqual([e["voucher_no"] for e in out], ["ACC-JV-CR"])

	def test_a_journal_row_already_tied_to_another_voucher_is_not_offered(self):
		accounts = [account_row("ACC-JV-1", debit=100, reference_type="Sales Invoice"), account_row("ACC-JV-2", debit=5, reference_type="Sales Order"),
			account_row("ACC-JV-3", debit=7, reference_type="")]
		with Seams([row("ACC-JV-1", 100), row("ACC-JV-2", 5), row("ACC-JV-3", 7)], accounts):
			out = pej.outstanding_journal_entries(json.dumps(ARGS))
		self.assertEqual(sorted(e["voucher_no"] for e in out), ["ACC-JV-2", "ACC-JV-3"])

	def test_the_journals_rows_are_looked_up_on_the_entrys_party_account_and_party(self):
		seams = Seams([row("ACC-JV-1", 10)], [account_row("ACC-JV-1", debit=10)])
		with seams:
			pej.outstanding_journal_entries(json.dumps(ARGS))
		doctype, kwargs = next(c for c in seams.calls if c[0] == "Journal Entry Account")
		self.assertEqual(kwargs["filters"]["account"], ARGS["party_account"])
		self.assertEqual(kwargs["filters"]["party"], "Easy Touch")
		self.assertEqual(kwargs["filters"]["docstatus"], 1)

	def test_each_offer_says_what_the_journal_is(self):
		about = frappe._dict(name="ACC-JV-1", voucher_type="Bank Entry", title="Opening balance", user_remark="Brought forward", cheque_no="000123", cheque_date="2026-01-01")
		with Seams([row("ACC-JV-1", 10)], [account_row("ACC-JV-1", debit=10)], [about]):
			entry = pej.outstanding_journal_entries(json.dumps(ARGS))[0]
		self.assertEqual((entry["journal_type"], entry["title"], entry["user_remark"], entry["cheque_no"]), ("Bank Entry", "Opening balance", "Brought forward", "000123"))
		self.assertEqual(entry["outstanding_amount"], 10)

	def test_a_word_finds_a_journal_by_remark_title_or_cheque_number_whatever_its_case(self):
		journals = [frappe._dict(name="ACC-JV-1", voucher_type="J", title="T1", user_remark="Opening BALANCE", cheque_no=None, cheque_date=None),
			frappe._dict(name="ACC-JV-2", voucher_type="J", title="Rent", user_remark="", cheque_no="CHQ-77", cheque_date=None),
			frappe._dict(name="ACC-JV-3", voucher_type="J", title="Other", user_remark="x", cheque_no=None, cheque_date=None)]
		accounts = [account_row("ACC-JV-%d" % n, debit=1) for n in (1, 2, 3)]
		core = [row("ACC-JV-%d" % n, 1) for n in (1, 2, 3)]
		for word, expected in (("opening", ["ACC-JV-1"]), ("chq-77", ["ACC-JV-2"]), ("rent", ["ACC-JV-2"]), ("", ["ACC-JV-1", "ACC-JV-2", "ACC-JV-3"]), ("zzz", [])):
			with Seams(core, accounts, journals):
				out = pej.outstanding_journal_entries(json.dumps(dict(ARGS, remark=word)))
			self.assertEqual([e["voucher_no"] for e in out], expected, word)

	def test_the_oldest_comes_first(self):
		core = [row("ACC-JV-B", 1, posting_date="2026-03-01"), row("ACC-JV-A", 1, posting_date="2026-01-01"), row("ACC-JV-C", 1, posting_date="2026-01-01")]
		accounts = [account_row(r.voucher_no, debit=1) for r in core]
		with Seams(core, accounts):
			out = pej.outstanding_journal_entries(json.dumps(ARGS))
		self.assertEqual([e["voucher_no"] for e in out], ["ACC-JV-A", "ACC-JV-C", "ACC-JV-B"])

	def test_nothing_open_gives_an_empty_list_and_asks_nothing_more(self):
		seams = Seams([row("ACC-SINV-1", 5, voucher_type="Sales Invoice")])
		with seams:
			self.assertEqual(pej.outstanding_journal_entries(json.dumps(ARGS)), [])
		self.assertFalse([c for c in seams.calls if c[0] == "Journal Entry Account"], "no journal, no lookup")


class TestWhatIsRefused(FrappeTestCase):
	def refused(self, **changes):
		args = dict(ARGS, **changes)
		with Seams(), self.assertRaises(frappe.ValidationError):
			pej.outstanding_journal_entries(json.dumps(args))

	def test_a_missing_company_party_account_or_direction_is_refused(self):
		for key in ("company", "party_type", "party", "party_account", "payment_type"):
			self.refused(**{key: ""})

	def test_an_internal_transfer_is_refused(self):
		self.refused(payment_type="Internal Transfer")

	def test_the_user_needs_to_read_payment_entries_and_journal_entries(self):
		checked = []
		with Seams([row("ACC-JV-1", 1)], [account_row("ACC-JV-1", debit=1)]):
			with patch.object(pej.frappe, "has_permission", side_effect=lambda doctype, ptype, **k: checked.append((doctype, ptype, k)) or True, create=True):
				pej.outstanding_journal_entries(json.dumps(ARGS))
		self.assertEqual([(d, p) for d, p, _k in checked], [("Payment Entry", "read"), ("Journal Entry", "read")])
		self.assertTrue(all(k.get("throw") for _d, _p, k in checked))

	def test_no_permission_stops_it_before_anything_is_read(self):
		with Seams() as seams, patch.object(pej.frappe, "has_permission", side_effect=frappe.PermissionError, create=True):
			with self.assertRaises(frappe.PermissionError):
				pej.outstanding_journal_entries(json.dumps(ARGS))
		self.assertEqual(seams.calls, [])


class TestTheButton(FrappeTestCase):
	def test_it_is_a_button_beside_invoices_and_orders_shown_on_drafts(self):
		made = {}
		with patch.object(pej, "create_custom_fields", side_effect=lambda fields, **k: made.update(fields)):
			pej.ensure_custom_fields()
		field = made["Payment Entry"][0]
		self.assertEqual((field["fieldname"], field["fieldtype"], field["insert_after"]), ("sf_get_outstanding_journal_entries", "Button", "get_outstanding_orders"))
		self.assertEqual(field["depends_on"], "eval:doc.docstatus==0")

	def test_the_hooks_are_registered(self):
		text = read("hooks.py")
		self.assertIn("sf_trading.payment_entry_journal.ensure_custom_fields", text)
		block = text[text.index('"Payment Entry":      ['):]
		self.assertIn("public/js/payment_entry_journal.js", block[: block.index("],")])
		self.assertTrue(os.path.exists(os.path.join(ROOT, "public", "js", "payment_entry_journal.js")))

	def test_the_handler_name_is_the_button_field_name(self):
		self.assertIn("%s(frm)" % pej.BUTTON, read("public", "js", "payment_entry_journal.js"))


JS_RUNNER = r"""
const fs = require("fs"), vm = require("vm");
const calls = { allocate: [], set_value: [], msgprint: [], call: [], dialog: null };
const frm = JSON.parse(process.argv[3]);
frm.add_child = (table) => { const c = {}; (frm.doc[table] = frm.doc[table] || []).push(c); return c; };
frm.refresh_field = () => {};
frm.set_value = (field, value) => { frm.doc[field] = value; calls.set_value.push([field, value]); return Promise.resolve(); };
frm.events = { allocate_party_amount_against_ref_docs: (f, amount, changed) => calls.allocate.push([amount, changed]) };
const sandbox = {
	console, __: (s, a) => (a || []).reduce((t, v, i) => t.replace("{" + i + "}", v), s),
	cint: (v) => parseInt(v) || 0, flt: (v) => parseFloat(v) || 0, Promise,
};
sandbox.frappe = {
	provide(path) { path.split(".").reduce((o, k) => (o[k] = o[k] || {}, o[k]), sandbox); },
	msgprint: (m) => calls.msgprint.push(m), flags: {}, show_alert() {},
	get_doc: () => ({ default_currency: "BHD" }),
	utils: { escape_html: (s) => s }, ui: { form: { on() {} }, Dialog: function (o) { calls.dialog = o; this.show = () => {}; this.hide = () => {}; } },
	call: (o) => calls.call.push(o),
};
sandbox.sf_trading = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const pej = sandbox.sf_trading.pe_journal;
const action = process.argv[4];
const out = {};
(async () => {
if (action === "problem") out.problem = pej.problem(frm);
if (action === "add") {
	pej.add(frm, JSON.parse(process.argv[5]), { allocate_payment_amount: 1 });
	out.references = frm.doc.references;
}
if (action === "choose") {
	pej.choose(frm, JSON.parse(process.argv[5]), {});
	out.rows = calls.dialog.fields[0].data;
	out.label = calls.dialog.primary_action_label;
	const rows = calls.dialog.fields[0].data;
	calls.dialog.primary_action({ entries: rows.map((r, i) => Object.assign({}, r, { add: i === 0 ? 1 : 0 })) });
	out.references = frm.doc.references || [];
}
if (action === "fetch_empty") {
	sandbox.frappe.call = (o) => o.callback({ message: [] });
	pej.fetch(frm, {});
}
await Promise.resolve();
await Promise.resolve();
out.calls = calls;
console.log(JSON.stringify(out));
})();
"""

BASE_FRM = {"doc": {"company": "Co", "party_type": "Customer", "party": "Easy Touch", "payment_type": "Receive",
	"paid_from": "130302E0001 - Easy Touch - SFB", "paid_to": "Bank - SFB", "paid_from_account_currency": "BHD", "paid_to_account_currency": "BHD",
	"paid_amount": 0, "references": []}}


class TestTheForm(FrappeTestCase):
	def run_js(self, action, frm=None, extra=None):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		cmd = [node, "-e", JS_RUNNER, "x", os.path.join(ROOT, "public", "js", "payment_entry_journal.js"), json.dumps(frm or BASE_FRM), action]
		if extra is not None:
			cmd.append(json.dumps(extra))
		done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
		self.assertEqual(done.returncode, 0, done.stderr)
		return json.loads(done.stdout)

	def doc(self, **changes):
		return {"doc": dict(BASE_FRM["doc"], **changes)}

	def test_it_says_what_is_missing_before_asking_anything(self):
		self.assertIsNone(self.run_js("problem")["problem"])
		self.assertIn("Pay or Receive", self.run_js("problem", self.doc(payment_type="Internal Transfer"))["problem"])
		self.assertIn("party type and the party", self.run_js("problem", self.doc(party=""))["problem"])
		self.assertIn("Account Paid From", self.run_js("problem", self.doc(paid_from=""))["problem"])
		self.assertIn("Account Paid To", self.run_js("problem", self.doc(payment_type="Pay", paid_to=""))["problem"])

	def test_ticked_journals_are_added_as_references_and_allocated(self):
		picked = [{"voucher_no": "ACC-JV-1", "outstanding_amount": 28.9, "invoice_amount": 82.52, "account": "A", "exchange_rate": 1, "due_date": "2026-02-01"},
			{"voucher_no": "ACC-JV-2", "outstanding_amount": 121.34, "invoice_amount": 121.34, "account": "A", "exchange_rate": 1, "due_date": "2026-02-18"}]
		out = self.run_js("add", extra=picked)
		self.assertEqual([(r["reference_doctype"], r["reference_name"], r["outstanding_amount"], r["total_amount"]) for r in out["references"]],
			[("Journal Entry", "ACC-JV-1", 28.9, 82.52), ("Journal Entry", "ACC-JV-2", 121.34, 121.34)])
		self.assertEqual(out["calls"]["set_value"], [["paid_amount", 150.24]])
		self.assertEqual(out["calls"]["allocate"], [[150.24, False]])

	def test_the_other_references_stay_and_a_journal_already_there_is_not_added_twice(self):
		frm = self.doc(references=[{"reference_doctype": "Sales Invoice", "reference_name": "SINV-1", "allocated_amount": 5},
			{"reference_doctype": "Journal Entry", "reference_name": "ACC-JV-1"}], paid_amount=500)
		out = self.run_js("add", frm, [{"voucher_no": "ACC-JV-1", "outstanding_amount": 1}, {"voucher_no": "ACC-JV-2", "outstanding_amount": 2}])
		self.assertEqual([r["reference_name"] for r in out["references"]], ["SINV-1", "ACC-JV-1", "ACC-JV-2"])
		self.assertEqual(out["calls"]["set_value"], [], "an amount already typed is left alone")
		self.assertEqual(out["calls"]["allocate"], [[500, False]])

	def test_a_pay_entry_allocates_by_the_received_amount(self):
		frm = self.doc(payment_type="Pay", party_type="Supplier", paid_to="220104S0001 - S - SFB", received_amount=300, paid_amount=300)
		out = self.run_js("add", frm, [{"voucher_no": "ACC-JV-9", "outstanding_amount": 50}])
		self.assertEqual(out["calls"]["allocate"], [[300, False]])

	def test_the_dialog_pre_ticks_what_is_new_and_adds_only_what_is_ticked(self):
		frm = self.doc(references=[{"reference_doctype": "Journal Entry", "reference_name": "ACC-JV-1"}], paid_amount=10)
		entries = [{"voucher_no": "ACC-JV-1", "outstanding_amount": 1, "title": "T", "user_remark": "R", "cheque_no": "55"},
			{"voucher_no": "ACC-JV-2", "outstanding_amount": 2, "user_remark": "Second"}, {"voucher_no": "ACC-JV-3", "outstanding_amount": 3}]
		out = self.run_js("choose", frm, entries)
		self.assertEqual([r["add"] for r in out["rows"]], [0, 1, 1], "one already on the entry is not pre-ticked")
		self.assertEqual(out["rows"][0]["remark"], "T · R · Cheque 55")
		self.assertEqual(out["label"], "Add to Payment References")
		# the test ticks only the first row, which is already there: nothing new is added
		self.assertEqual([r["reference_name"] for r in out["references"]], ["ACC-JV-1"])

	def test_nothing_found_says_so_in_words(self):
		out = self.run_js("fetch_empty")
		self.assertIn("No outstanding journal entries were found", out["calls"]["msgprint"][0])
