# sf_trading/patches/v0_1/grandfather_existing_customers_approved.py
"""Credit customer approval only ever meant to gate NEW customers.

Every Customer that already existed when this feature shipped is grandfathered in as
Approved -- otherwise the whole existing customer base would wake up unable to be
invoiced. Only touches rows still blank, so re-running this (or a later manual rejection)
never clobbers a real decision.
"""

import frappe


def execute():
	frappe.db.sql(
		"""
		UPDATE `tabCustomer`
		SET custom_approval_status = 'Approved'
		WHERE custom_approval_status IS NULL OR custom_approval_status = ''
		"""
	)
	frappe.logger("sf_trading").info("grandfathered existing customers as Approved for credit approval")
