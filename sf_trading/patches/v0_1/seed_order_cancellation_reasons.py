# sf_trading/patches/v0_1/seed_order_cancellation_reasons.py
"""Seed the standard Order Cancellation Reason list (also seeded on every migrate and install)."""

import frappe

from sf_trading.order_cancellation import seed_reasons


def execute():
	seed_reasons()
	frappe.db.commit()
