"""Tests for the Journal Entry attachment-required-before-submit rule (client call, 2026-09-28).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_journal_entry_attachment
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import journal_entry_attachment as jea

GET_ALL = "sf_trading.journal_entry_attachment.frappe.get_all"


class StubDoc:
	def __init__(self, doctype, **fields):
		self.doctype = doctype
		self.__dict__.update(fields)
		self.name = fields.get("name")

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


class TestJournalEntryAttachmentRequired(FrappeTestCase):
	def test_blocks_submit_with_no_attachment(self):
		doc = StubDoc("Journal Entry", name="ACC-JV-2026-00001", is_system_generated=0)
		with patch(GET_ALL, return_value=[]):
			with self.assertRaises(frappe.ValidationError):
				jea.validate_attachment_required(doc)

	def test_allows_submit_with_an_attachment(self):
		doc = StubDoc("Journal Entry", name="ACC-JV-2026-00001", is_system_generated=0)
		with patch(GET_ALL, return_value=[{"name": "FILE-0001"}]) as get_all:
			jea.validate_attachment_required(doc)  # must not raise
		get_all.assert_called_once_with(
			"File",
			filters={"attached_to_doctype": "Journal Entry", "attached_to_name": "ACC-JV-2026-00001"},
			limit=1,
		)

	def test_system_generated_entries_are_exempt(self):
		doc = StubDoc("Journal Entry", name="ACC-JV-2026-00002", is_system_generated=1)
		with patch(GET_ALL) as get_all:
			jea.validate_attachment_required(doc)  # must not raise
		get_all.assert_not_called()
