"""A new account is numbered in order, the way its parent's other accounts are (sf_trading/account_numbering.py).

    bench --site <site> run-tests --module sf_trading.tests.test_account_numbering
"""

import importlib
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import account_numbering as an


def account(name, number, parent=None, group=0, **extra):
	return frappe._dict(
		name=name, account_number=number, parent_account=parent, is_group=group, account_name=name, **extra
	)


def chart(*rows):
	return an.Chart(rows)


# the shapes of Steel Force's own chart, in miniature
DEBTORS = "130301 - Trade Debtors - SFB"
CREDITORS = "2201 - Trade Creditors - SFB"
INVENTORY = "130901 - Inventory - SFB"
CAPITAL = "3101 - Share Capital - SFB"
ADMIN = "5201 - General & Administration Expenses - SFB"


def steel_force():
	rows = [
		account(DEBTORS, "130301", "13 - Current Assets - SFB", 1),
		account(CREDITORS, "2201", "22 - Current Liabilities - SFB", 1),
		account(INVENTORY, "130901", "13 - Current Assets - SFB", 1),
		account(CAPITAL, "3101", "3 - Equity - SFB", 1),
		account(ADMIN, "5201", "52 - Non Operational Expenditure - SFB", 1),
	]
	# customers: parent digits + initial + serial; some sit under a sub-group prefix (130302)
	for number in ("130301A0001", "130301A0002", "130301M0012", "130301P0001", "130301P0002", "130302A0035", "130302A0034",
			"130302B0001"):
		rows.append(account("C " + number, number, DEBTORS))
	# suppliers: 220102 is the prefix nearly all of them carry
	for number in ("220102A0002", "220102A0057", "220102B0008", "220102T0006", "220102S0021", "220104A0012", "220102M0015"):
		rows.append(account("S " + number, number, CREDITORS))
	rows += [account("I " + n, n, INVENTORY) for n in ("13090100001", "13090100002")]
	rows += [account("K " + n, n, CAPITAL) for n in ("31010100001", "31010100002", "31010100005")]
	rows += [account("G " + n, n, ADMIN, 1) for n in ("520101", "520102", "520106")]
	return chart(*rows)


class TestTheBase(FrappeTestCase):
	def test_a_number_is_its_own_base(self):
		self.assertEqual(an.number_base("130301"), "130301")
		self.assertEqual(an.number_base(" 3101 "), "3101")

	def test_a_range_group_starts_at_its_first_number(self):
		self.assertEqual(an.number_base("1100-1600"), "1100")

	def test_no_number_or_a_word_is_no_base(self):
		for value in (None, "", "  ", "ABC", "A-1"):
			self.assertEqual(an.number_base(value), "", value)


class TestTheInitial(FrappeTestCase):
	def test_the_first_latin_letter_capitalised(self):
		self.assertEqual(an.initial_of("peniel Stainless"), "P")
		self.assertEqual(an.initial_of("7 Seas Trading"), "S")
		self.assertEqual(an.initial_of("(Al) Salam"), "A")

	def test_a_name_with_no_latin_letter(self):
		self.assertEqual(an.initial_of("شركة 123"), an.NO_INITIAL)
		self.assertEqual(an.initial_of(""), an.NO_INITIAL)
		self.assertEqual(an.initial_of(None), an.NO_INITIAL)


class TestAPartyLedger(FrappeTestCase):
	def setUp(self):
		self.chart = steel_force()

	def test_a_customer_takes_the_next_serial_of_its_initial(self):
		self.assertEqual(an.propose(self.chart, DEBTORS, "Peniel Stainless Steel", 0), "130301P0003")

	def test_the_parents_own_digits_are_used_when_it_has_ledgers_under_them(self):
		# 130302A0035 is the highest A, but it belongs to the sub-group prefix: this is 130301's series
		self.assertEqual(an.propose(self.chart, DEBTORS, "Al Takatuf", 0), "130301A0003")

	def test_a_new_initial_starts_at_one(self):
		self.assertEqual(an.propose(self.chart, DEBTORS, "Vayu Media", 0), "130301V0001")

	def test_a_supplier_takes_the_prefix_most_of_its_ledgers_carry(self):
		# no ledger is numbered 2201<letter>...: the group is 2201, its ledgers sit under 220102
		self.assertEqual(an.propose(self.chart, CREDITORS, "Batelco", 0), "220102B0009")
		self.assertEqual(an.propose(self.chart, CREDITORS, "Arab United Steel", 0), "220102A0058")

	def test_the_serial_keeps_the_width_the_series_has(self):
		self.assertEqual(len(an.propose(self.chart, CREDITORS, "Zayani Motors", 0)), len("220102Z0001"))

	def test_a_name_with_no_latin_letter_goes_under_x(self):
		self.assertEqual(an.propose(self.chart, DEBTORS, "شركة", 0), "130301X0001")

	def test_a_group_is_not_a_ledger_and_takes_a_plain_serial(self):
		number = an.propose(self.chart, DEBTORS, "Online Customers", 1)
		self.assertTrue(number.startswith("130301") and number.isdigit(), number)

	def test_a_few_ledgers_that_are_not_a_series_do_not_make_one(self):
		small = chart(account(DEBTORS, "130301", None, 1), account("A", "130301A0001", DEBTORS),
			account("B", "13030100001", DEBTORS), account("C", "13030100002", DEBTORS), account("D", "13030100003", DEBTORS))
		self.assertEqual(an.propose(small, DEBTORS, "Peniel", 0), "13030100004")


class TestAPlainAccount(FrappeTestCase):
	def setUp(self):
		self.chart = steel_force()

	def test_a_ledger_continues_its_parents_serial(self):
		self.assertEqual(an.propose(self.chart, INVENTORY, "Trading Inventory 2", 0), "13090100003")

	def test_the_infix_a_four_digit_parent_gives_its_ledgers_is_kept(self):
		# 3101 + 01 + serial: the width is the series', not worked out from the parent
		self.assertEqual(an.propose(self.chart, CAPITAL, "Another Partner", 0), "31010100006")

	def test_a_group_continues_the_groups_beside_it(self):
		self.assertEqual(an.propose(self.chart, ADMIN, "Financial Expenses", 1), "520107")

	def test_a_range_group_numbers_its_children_from_its_stem(self):
		ranged = chart(account("R", "1100-1600", None, 1), account("a", "1101", "R", 1), account("b", "1102", "R", 1))
		self.assertEqual(an.propose(ranged, "R", "Other", 1), "1103")

	def test_a_parent_numbered_with_trailing_zeros_numbers_its_children_from_the_stem(self):
		fixed = chart(account("P", "5200", None, 1), account("a", "5201", "P"), account("b", "5202", "P"))
		self.assertEqual(an.propose(fixed, "P", "Other", 0), "5203")

	def test_the_first_child_of_a_group_starts_a_series(self):
		empty = chart(account("P", "130901", None, 1))
		self.assertEqual(an.propose(empty, "P", "Warehouse Stock", 1), "13090101")
		self.assertEqual(an.propose(empty, "P", "Warehouse Stock", 0), "13090100001")

	def test_the_first_ledger_of_a_short_parent_still_has_eleven_digits(self):
		empty = chart(account("P", "3101", None, 1))
		self.assertEqual(an.propose(empty, "P", "Capital", 0), "31010000001")
		self.assertEqual(len(an.propose(empty, "P", "Capital", 0)), an.LEDGER_DIGITS)

	def test_a_number_that_is_taken_elsewhere_is_skipped(self):
		rows = list(steel_force().number_of.items())
		taken = chart(*[account(name, number) for name, number in rows],
			account("X", "13090100003", "other"))
		taken.children[INVENTORY] = steel_force().children[INVENTORY]
		self.assertEqual(an.propose(taken, INVENTORY, "Trading Inventory 2", 0), "13090100004")

	def test_the_highest_number_wins_not_the_count(self):
		gappy = chart(account("P", "1301", None, 1), account("a", "130101", "P", 1), account("b", "130105", "P", 1))
		self.assertEqual(an.propose(gappy, "P", "x", 1), "130106")


class TestNothingToDo(FrappeTestCase):
	def test_a_parent_with_no_number_gives_none(self):
		unnumbered = chart(account("P", "", None, 1), account("a", "", "P"))
		self.assertIsNone(an.propose(unnumbered, "P", "x", 0))

	def test_an_unknown_parent_gives_none(self):
		self.assertIsNone(an.propose(steel_force(), "No Such Parent", "x", 0))

	def test_a_word_for_a_number_gives_none(self):
		odd = chart(account("P", "ABC", None, 1))
		self.assertIsNone(an.propose(odd, "P", "x", 0))


class TestTheSwitch(FrappeTestCase):
	def read(self, stored, field_exists=True):
		with patch.object(an.frappe.db, "get_single_value", return_value=stored) as read, \
				patch.object(an.frappe, "get_meta", return_value=MagicMock(has_field=lambda fieldname: field_exists)):
			return an.switched_on(), read

	def test_a_site_that_never_set_it_counts_as_on(self):
		self.assertTrue(self.read(None)[0])

	def test_a_stored_value_decides(self):
		self.assertTrue(self.read(1)[0])
		self.assertFalse(self.read(0)[0])
		self.assertFalse(self.read("0")[0])

	def test_the_field_not_being_there_yet_counts_as_on_and_is_not_read(self):
		# the code is pulled before migrate makes the field: reading it would raise on every account made
		on, read = self.read(None, field_exists=False)
		self.assertTrue(on)
		read.assert_not_called()

	def test_a_switch_that_cannot_be_read_counts_as_on(self):
		with patch.object(an.frappe, "get_meta", side_effect=RuntimeError("boom")):
			self.assertTrue(an.switched_on())
		with patch.object(an.frappe, "get_meta", return_value=MagicMock(has_field=lambda fieldname: True)), \
				patch.object(an.frappe.db, "get_single_value", side_effect=frappe.ValidationError("no such field")):
			self.assertTrue(an.switched_on())


class TestTheHook(FrappeTestCase):
	def setUp(self):
		on = patch.object(an, "switched_on", return_value=True)
		on.start()
		self.addCleanup(on.stop)
		used = patch.object(an, "chart_of", return_value=steel_force())
		self.chart_of = used.start()
		self.addCleanup(used.stop)

	def doc(self, **values):
		base = dict(account_number="", parent_account=DEBTORS, company="Steel Force Trading WLL",
			account_name="Peniel Stainless Steel", is_group=0)
		base.update(values)
		return frappe._dict(base)

	def test_an_account_with_no_number_gets_the_next_one(self):
		doc = self.doc()
		an.number_new_account(doc, "before_insert")
		self.assertEqual(doc.account_number, "130301P0003")

	def test_the_chart_read_is_the_accounts_own_company(self):
		an.number_new_account(self.doc(), "before_insert")
		self.chart_of.assert_called_once_with("Steel Force Trading WLL")

	def test_a_number_typed_by_hand_is_never_changed(self):
		doc = self.doc(account_number="999")
		an.number_new_account(doc)
		self.assertEqual(doc.account_number, "999")
		self.chart_of.assert_not_called()

	def test_an_account_with_no_parent_is_left_for_a_person(self):
		doc = self.doc(parent_account=None)
		an.number_new_account(doc)
		self.assertEqual(doc.account_number, "")

	def test_a_parent_with_no_number_leaves_the_account_unnumbered(self):
		with patch.object(an, "chart_of", return_value=chart(account("P", "", None, 1))):
			doc = self.doc(parent_account="P")
			an.number_new_account(doc)
		self.assertEqual(doc.account_number, "")

	def test_switched_off_nothing_is_done(self):
		doc = self.doc()
		with patch.object(an, "switched_on", return_value=False):
			an.number_new_account(doc)
		self.assertEqual(doc.account_number, "")
		self.chart_of.assert_not_called()

	def test_a_failure_is_logged_and_the_account_is_made_without_a_number(self):
		doc = self.doc()
		with patch.object(an, "chart_of", side_effect=RuntimeError("boom")), patch.object(an.frappe, "log_error") as log, \
				patch.object(an, "_", side_effect=lambda text: text):
			an.number_new_account(doc)
		self.assertEqual(doc.account_number, "")
		log.assert_called_once()

	def test_the_hook_is_registered_on_account_before_insert(self):
		hooks = importlib.import_module("sf_trading.hooks")
		self.assertEqual(hooks.doc_events["Account"]["before_insert"], "sf_trading.account_numbering.number_new_account")


class TestTheAccountsAlreadyThere(FrappeTestCase):
	"""The plan for accounts that were made without a number."""

	ROWS = [
		frappe._dict(name="130301 - Trade Debtors - SFB", account_name="Trade Debtors", account_number="130301",
			parent_account="13 - Current Assets - SFB", is_group=1, account_type="", creation="2026-07-06 10:00:00"),
		frappe._dict(name="C1", account_name="Arise Trading", account_number="130301A0001",
			parent_account="130301 - Trade Debtors - SFB", is_group=0, account_type="Receivable", creation="2026-07-06 10:00:01"),
		frappe._dict(name="C2", account_name="Arab Metals", account_number="130301A0002",
			parent_account="130301 - Trade Debtors - SFB", is_group=0, account_type="Receivable", creation="2026-07-06 10:00:02"),
		frappe._dict(name="C3", account_name="Atlas Steel", account_number="130301A0003",
			parent_account="130301 - Trade Debtors - SFB", is_group=0, account_type="Receivable", creation="2026-07-06 10:00:03"),
		# made later, by a person, with no number
		frappe._dict(name="Peniel Wll - SFB", account_name="Peniel Wll", account_number="",
			parent_account="130301 - Trade Debtors - SFB", is_group=0, account_type="Receivable", creation="2026-07-16 08:54:00"),
		frappe._dict(name="Peniel Two - SFB", account_name="Peniel Two", account_number=None,
			parent_account="130301 - Trade Debtors - SFB", is_group=0, account_type="Receivable", creation="2026-07-17 08:54:00"),
		frappe._dict(name="Another Group - SFB", account_name="Another Group", account_number="",
			parent_account="130301 - Trade Debtors - SFB", is_group=1, account_type="", creation="2026-07-18 08:54:00"),
		frappe._dict(name="Child Of Another - SFB", account_name="Child Of Another", account_number="",
			parent_account="Another Group - SFB", is_group=0, account_type="", creation="2026-07-19 08:54:00"),
		# no number anywhere above it: left alone
		frappe._dict(name="Orphan - SFB", account_name="Orphan", account_number="",
			parent_account="Unnumbered Group - SFB", is_group=0, account_type="", creation="2026-07-19 09:00:00"),
		frappe._dict(name="Unnumbered Group - SFB", account_name="Unnumbered Group", account_number="",
			parent_account=None, is_group=1, account_type="", creation="2026-07-01 09:00:00"),
	]

	def plan(self):
		with patch.object(an.frappe, "get_all", return_value=[frappe._dict(row) for row in self.ROWS]), \
				patch.object(an.frappe, "get_cached_value", return_value="SFB"):
			return an.missing_number_plan("Steel Force Trading WLL")

	def test_the_accounts_without_a_number_are_planned_in_the_order_they_were_made(self):
		by_account = {item["account"]: item["number"] for item in self.plan()}
		self.assertEqual(by_account["Peniel Wll - SFB"], "130301P0001")
		self.assertEqual(by_account["Peniel Two - SFB"], "130301P0002")

	def test_the_numbers_run_in_order_because_each_is_worked_out_after_the_one_before(self):
		numbers = [item["number"] for item in self.plan() if item["account"].startswith("Peniel")]
		self.assertEqual(numbers, ["130301P0001", "130301P0002"])

	def test_a_group_is_numbered_before_its_children_so_they_can_start_from_it(self):
		plan = self.plan()
		accounts = [item["account"] for item in plan]
		self.assertLess(accounts.index("Another Group - SFB"), accounts.index("Child Of Another - SFB"))
		group = next(item for item in plan if item["account"] == "Another Group - SFB")
		child = next(item for item in plan if item["account"] == "Child Of Another - SFB")
		self.assertTrue(child["number"].startswith(group["number"]), (group["number"], child["number"]))

	def test_the_new_name_is_what_erpnext_will_build(self):
		item = next(item for item in self.plan() if item["account"] == "Peniel Wll - SFB")
		self.assertEqual(item["new_name"], "130301P0001 - Peniel Wll - SFB")

	def test_an_account_under_a_parent_with_no_number_is_left_alone(self):
		self.assertNotIn("Orphan - SFB", [item["account"] for item in self.plan()])

	def test_an_account_that_has_a_number_is_not_in_the_plan(self):
		self.assertFalse({"C1", "C2", "C3"} & {item["account"] for item in self.plan()})


class TestRenamingThem(FrappeTestCase):
	def setUp(self):
		only = patch.object(an.frappe, "only_for")
		self.only_for = only.start()
		self.addCleanup(only.stop)
		self.plan = [
			{"account": "A - SFB", "account_name": "A", "number": "130301A0010", "new_name": "n", "parent_account": "p",
				"account_type": "Receivable", "is_group": 0},
			{"account": "B - SFB", "account_name": "B", "number": "130301B0010", "new_name": "n", "parent_account": "p",
				"account_type": "Receivable", "is_group": 0},
			{"account": "C - SFB", "account_name": "C", "number": "130301C0010", "new_name": "n", "parent_account": "p",
				"account_type": "Receivable", "is_group": 0},
		]
		planned = patch.object(an, "missing_number_plan", return_value=list(self.plan))
		planned.start()
		self.addCleanup(planned.stop)
		db = patch.object(an.frappe, "db", new=MagicMock())
		self.db = db.start()
		self.addCleanup(db.stop)

	def rename(self, **kwargs):
		module = MagicMock()
		with patch.dict("sys.modules", {"erpnext.accounts.doctype.account.account": module}):
			result = an.number_existing_accounts("Steel Force Trading WLL", **kwargs)
		return result, module.update_account_number

	def test_only_a_system_or_accounts_manager_may_ask(self):
		self.rename()
		self.only_for.assert_called_once_with(("System Manager", "Accounts Manager"))

	def test_a_preview_writes_nothing(self):
		result, update = self.rename()
		update.assert_not_called()
		self.db.commit.assert_not_called()
		self.assertEqual((result["left"], len(result["plan"]), result["applied"]), (3, 3, []))

	def test_apply_renames_each_account_through_erpnext_and_commits_each(self):
		result, update = self.rename(apply=1, limit=25)
		self.assertEqual([call.args for call in update.call_args_list],
			[("A - SFB", "A", "130301A0010"), ("B - SFB", "B", "130301B0010"), ("C - SFB", "C", "130301C0010")])
		self.assertEqual(self.db.commit.call_count, 3)
		self.assertEqual((len(result["applied"]), result["left"], result["stopped"]), (3, 0, None))

	def test_a_batch_stops_at_the_limit_and_says_how_many_are_left(self):
		result, update = self.rename(apply=1, limit=2)
		self.assertEqual(update.call_count, 2)
		self.assertEqual((len(result["applied"]), result["left"]), (2, 1))

	def test_a_refusal_stops_the_batch_and_is_reported(self):
		module = MagicMock()
		module.update_account_number.side_effect = [None, frappe.ValidationError("wait 5 minutes"), None]
		with patch.dict("sys.modules", {"erpnext.accounts.doctype.account.account": module}):
			result = an.number_existing_accounts("Steel Force Trading WLL", apply=1)
		self.assertEqual(module.update_account_number.call_count, 2)
		self.assertEqual([item["account"] for item in result["applied"]], ["A - SFB"])
		self.assertEqual(result["stopped"]["account"], "B - SFB")
		self.assertIn("wait 5 minutes", result["stopped"]["reason"])
		self.assertEqual(result["left"], 2)
		self.db.rollback.assert_called_once()
