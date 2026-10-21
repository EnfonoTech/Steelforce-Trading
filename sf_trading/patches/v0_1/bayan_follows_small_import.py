# sf_trading/patches/v0_1/bayan_follows_small_import.py
"""The Bayan number is waived for a small import; the Landed Cost Voucher is not.

Client, 2026-10-20: an import below the amount set on SF Trading Settings > Small Imports needs no Bayan number,
and nothing else is waived (0.1.47 had also waived the voucher). The Purchase Invoice form now decides the Bayan
requirement (public/js/purchase_invoice_lcv.js), so the site's old "Bayan Field  Validation" Client Script, which
requires it on every foreign-currency invoice whatever the total, is switched off. Idempotent; a script that is
not exactly that one is left as it is.
"""

from sf_trading.landed_cost_with_invoice import retire_site_bayan_script


def execute():
	retire_site_bayan_script()
