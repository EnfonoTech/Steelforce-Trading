from frappe import _


def get_data():
	# a Purchase Invoice links its Goods In from its item rows; each Goods In row keeps the invoice
	# made from it, so the Connections read that (sf_trading/goods_in.update_invoiced_qty)
	return {
		"fieldname": "goods_in",
		"internal_links": {
			"Purchase Order": "purchase_order",
			"Purchase Invoice": ["items", "purchase_invoice"],
		},
		"transactions": [
			{"label": _("Reference"), "items": ["Purchase Order"]},
			{"label": _("Billing"), "items": ["Purchase Invoice"]},
		],
	}
