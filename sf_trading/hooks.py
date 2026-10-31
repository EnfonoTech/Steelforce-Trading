app_name = "sf_trading"
app_title = "Sf Trading"
app_publisher = "enfono"
app_description = "Trading Feature for Steel force"
app_email = "ramees@enfono.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "sf_trading",
# 		"logo": "/assets/sf_trading/logo.png",
# 		"title": "Sf Trading",
# 		"route": "/sf_trading",
# 		"has_permission": "sf_trading.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/sf_trading/css/sf_trading.css"
from sf_trading import __version__ as _v
app_include_css = [
	# the Updates Hub page, the navbar megaphone and the must-read pop-up -- see announcements.py
	f"/assets/sf_trading/css/announcements.css?{_v}",
]
app_include_js = [
	# ── Multi-doctype: selling (Sales Invoice, Sales Order, Quotation, Delivery Note) ──
	# what a selling form asks about a customer when it is picked, in one call shared by every handler
	f"/assets/sf_trading/js/customer_selling_context.js?{_v}",
	# text being typed into a field is not wiped by the form redrawing itself under the cursor
	f"/assets/sf_trading/js/keep_typed_input.js?{_v}",
	f"/assets/sf_trading/js/last_selling_rate.js?{_v}",
	f"/assets/sf_trading/js/quick_entry.js?{_v}",
	f"/assets/sf_trading/js/create_customer.js?{_v}",
	f"/assets/sf_trading/js/selling_price_realtime.js?{_v}",
	# ── Multi-doctype: purchasing (Purchase Invoice, Purchase Order, Purchase Receipt, …) ──
	f"/assets/sf_trading/js/last_purchase_rate.js?{_v}",
	f"/assets/sf_trading/js/create_supplier.js?{_v}",
	# Customer / Supplier "+ Add" quick entry: required marks, no address picker on a new supplier
	f"/assets/sf_trading/js/party_quick_entry.js?{_v}",
	# ── Multi-doctype: cross-selling + purchasing ──
	f"/assets/sf_trading/js/return_qty_autofix.js?{_v}",
	f"/assets/sf_trading/js/accounting_dimension_sync.js?{_v}",
	f"/assets/sf_trading/js/branch_price_list.js?{_v}",
	f"/assets/sf_trading/js/sales_invoice_item_search.js?{_v}",
	f"/assets/sf_trading/js/purchase_invoice_cost_center.js?{_v}",
	f"/assets/sf_trading/js/stock_availability.js?{_v}",
	# the third button on the Purchase Order items grid: what the branch has been selling this
	# item for, which is the other half of a purchase negotiation
	f"/assets/sf_trading/js/selling_history.js?{_v}",
	# ── Global (all pages / all item_code fields / print patches) ──
	f"/assets/sf_trading/js/workflow_approval_shortcut.js?{_v}",
	f"/assets/sf_trading/js/item_search_cache_buster.js?{_v}",
	f"/assets/sf_trading/js/company_print_format.js?{_v}",
	# ── Overdue-invoice alert: chime + toast + desktop notification ──
	f"/assets/sf_trading/js/sf_overdue_alert.js?{_v}",
	# GS Issue 17: prompts for a cancellation remark before the cancel HTTP call fires.
	# The actual gate is server-side (sales_order_governance.py) -- this is UX only.
	f"/assets/sf_trading/js/sales_order_cancel.js?{_v}",
	# filters + formatting shared by the two cash-flow reports (a report script cannot import another's)
	f"/assets/sf_trading/js/cash_flow_filters.js?{_v}",
	# GS Issue 15: "N similar records found" while typing, on Item/Customer/Supplier/Account.
	# UX only -- the real uniqueness rule stays server-side.
	f"/assets/sf_trading/js/duplicate_check_popup.js?{_v}",
	# the search bar finds a report by its number (RPT-0042) as well as its name -- see report_numbering.py
	f"/assets/sf_trading/js/report_number_search.js?{_v}",
	# formatting and drill-down into the General Ledger of the Cash Flow Statement -- see cash_flow_statement.py
	f"/assets/sf_trading/js/cash_flow_statement.js?{_v}",
	# the Alternative Cost Center field offers only cost centres of other companies -- see alternative_cost_center.py
	f"/assets/sf_trading/js/alternative_cost_center.js?{_v}",
	# formatting and drill-down of the MIS Profit and Loss and its detail -- see mis_profit_loss.py
	f"/assets/sf_trading/js/mis_profit_loss.js?{_v}",
	# account links on Trial Balance / Balance Sheet / P&L stay clickable while searching a column
	f"/assets/sf_trading/js/financial_statements_search_links.js?{_v}",
	# the navbar megaphone, the must-read pop-up and the shared card renderer of the Updates Hub page
	f"/assets/sf_trading/js/announcements.js?{_v}",
]

# doctype_js: loaded only when that specific doctype form opens
doctype_js = {
	"Sales Invoice":    [
		# View > COGS Calculation -- how this invoice's cost was reached (cogs_breakdown.py)
		"public/js/cogs_breakdown.js",
		"public/js/sales_invoice.js",
		# declines to paint Return / Credit Note once the return window has closed, and says why
		"public/js/sales_return_window.js",
		# fix the customer's own phone/CR/VAT from inside the draft invoice, without navigating away
		"public/js/customer_quick_edit.js",
		# a popup when the customer / supplier picked on a new voucher would be refused -- see party_alert.py
		"public/js/party_gate_alert.js",
		# copies the picked Sales Return Reason into Remarks; makes Remarks mandatory while the
		# reason is "Other" -- a different, narrower concern than sales_return_window.js's
		# window/approval gate (the picker itself is marked mandatory by its Custom Field)
		"public/js/sales_return_reason.js",
		# Sales Team's Sales Person picker only offers salespersons valid for this Company
		"public/js/salesperson_by_company.js",
		# the whole Sales Order -> Delivery Note -> Sales Invoice chain, not just this one's own hop
		"public/js/document_trail.js",
		# no Print on a draft sale; a draft return still prints -- see sf_trading/draft_print.py
		"public/js/sales_invoice_draft_print.js",
		# a Cash sale is due the day it is made, whatever terms the customer carries -- see cash_due_date.py
		"public/js/cash_due_date.js",
	],
	# View > Supplier Quotation Comparison Extended, opened on this request -- see the report of that name
	"Request for Quotation": "public/js/rfq_quotation_comparison.js",
	"Stock Entry":      [
		"public/js/stock_entry.js",
		# View > Transfer Cost Breakdown on a Material Transfer -- see cogs_breakdown.py
		"public/js/cogs_breakdown.js",
	],
	# Actions -> Announce this report: a new Updates Hub post aimed at the roles that may run the report
	"Report":           "public/js/report_announce.js",
	# Item Code and Item Name are typed in capitals on the form -- the server enforces it, see item_case.py
	"Item":             [
		"public/js/item_uppercase.js",
		# Approve / Bypass Approval / the missing-requirements banner -- see master_approval.py
		"public/js/master_approval.js",
		# Print Label for a stock / fixed-asset item -- see item_label.py
		"public/js/item_label.js",
		"public/js/item_label_form.js",
	],
	# "Items Without Price" button: opens that report on this list
	"Price List":       "public/js/price_list.js",
	"Company":          "public/js/company_stock_reco_account.js",
	"Material Request": "public/js/material_request.js",
	"Customer":         [
		"public/js/customer_company.js",
		# core's own mobile_no can be made mandatory from Customize Form with no auto-fill of its
		# own; this fills it from the G11 phone cache instead of demanding a retype
		"public/js/party_mobile_no_prefill.js",
		# "Quick Edit Billing Fields" button -- same dialog the Sales Invoice Customer field uses
		"public/js/customer_quick_edit.js",
		# what the approval still wants of the master, Approve / Bypass Approval -- see master_approval.py
		"public/js/master_approval.js",
		# the Supporting Documents grid and the new-master required marks
		"public/js/party_documents.js",
	],
	"Supplier":         [
		"public/js/party_mobile_no_prefill.js",
		"public/js/party_documents.js",
		# "Quick Edit Billing Fields" -- the supplier twin of the Customer one
		"public/js/supplier_quick_edit.js",
		"public/js/master_approval.js",
	],
	"Quotation":        [
		"public/js/quotation.js",
		# a Price List on each item row, as on the Sales Invoice -- public/js/row_price_list.js
		"public/js/row_price_list.js",
	],
	# Receive Payment on a submitted order — the invoice popup's twin — and the three fields the
	# order now shares with the invoice
	"Sales Order":      [
		"public/js/sales_order_payment.js",
		"public/js/sales_order_parity.js",
		# the Sales Invoice's credit checks while the customer is picked -- see sales_order_credit.js
		"public/js/sales_order_credit.js",
		# fix the customer's own phone/CR/VAT from inside the draft order, without navigating away
		# -- same dialog the Sales Invoice Customer field and Customer master both already use
		"public/js/customer_quick_edit.js",
		# a popup when the customer / supplier picked on a new voucher would be refused -- see party_alert.py
		"public/js/party_gate_alert.js",
		# Sales Team's Sales Person picker only offers salespersons valid for this Company
		"public/js/salesperson_by_company.js",
		# the whole Sales Order -> Delivery Note -> Sales Invoice chain, not just this one's own hop
		"public/js/document_trail.js",
		# a Cash order carries no payment terms of its customer -- see cash_due_date.py
		"public/js/cash_due_date.js",
		# a Price List on each item row, as on the Sales Invoice -- public/js/row_price_list.js
		"public/js/row_price_list.js",
	],
	# same Sales Team restriction as Sales Invoice / Sales Order -- Delivery Note carries the same
	# child table but no other doctype-specific script of its own yet; also the SO -> DN -> SI chain
	"Delivery Note":    [
		# View > COGS Calculation -- how this note's cost was reached (cogs_breakdown.py)
		"public/js/cogs_breakdown.js",
		"public/js/salesperson_by_company.js",
		"public/js/document_trail.js",
		# a popup the moment a customer the approval holds back is picked -- see party_alert.py
		"public/js/party_gate_alert.js",
	],
	"Supplier Quotation": [
		"public/js/purchase_tax_template.js",
	],
	"Purchase Receipt":   [
		"public/js/purchase_tax_template.js",
		# while GRN is disabled for this user the form is a viewer: no Submit / Cancel / Return buttons
		"public/js/purchase_receipt_gate.js",
		# the whole Purchase Order -> Purchase Receipt -> Purchase Invoice chain
		"public/js/document_trail.js",
		# a Valuation charge names the expense entry that booked it
		"public/js/landed_cost.js",
		# fix the supplier's billing-blocking details from inside the draft
		"public/js/supplier_quick_edit.js",
		# a popup when the customer / supplier picked on a new voucher would be refused -- see party_alert.py
		"public/js/party_gate_alert.js",
		# the tracking Assets this receipt raised, as their own Asset Tracking section
		"public/js/asset_tracking_links.js",
	],
	"Landed Cost Voucher": "public/js/landed_cost.js",
	# Payment Advice sits in the Create menu beside Payment Request, under the same conditions
	"Purchase Order":     [
		"public/js/purchase_tax_template.js",
		"public/js/payment_advice_form_action.js",
		# the whole Purchase Order -> Purchase Receipt -> Purchase Invoice chain
		"public/js/document_trail.js",
		# fix the supplier's billing-blocking details from inside the draft
		"public/js/supplier_quick_edit.js",
		# a popup when the customer / supplier picked on a new voucher would be refused -- see party_alert.py
		"public/js/party_gate_alert.js",
		# Create -> Goods In: arrival recorded for quantity only (sf_trading/goods_in.py)
		"public/js/purchase_order_goods_in.js",
		# the physical stock count's flag and variance as it is typed (sf_trading/po_stock_count.py)
		"public/js/po_stock_count.js",
		# no Create -> Purchase Receipt while GRN is disabled for this user (purchase_receipt_gate.py)
		"public/js/purchase_order_grn.js",
	],
	"Purchase Invoice":   [
		"public/js/purchase_tax_template.js",
		"public/js/payment_advice_form_action.js",
		# the whole Purchase Order -> Purchase Receipt -> Purchase Invoice chain
		"public/js/document_trail.js",
		# a Valuation charge names the expense entry that booked it
		"public/js/landed_cost.js",
		# Create > Landed Cost Voucher on a draft stock invoice, and what the import still needs
		"public/js/purchase_invoice_lcv.js",
		# Update Stock ticked and locked (Administrator excepted) on an invoice that brings the goods in
		"public/js/purchase_invoice_update_stock.js",
		# fix the supplier's billing-blocking details from inside the draft
		"public/js/supplier_quick_edit.js",
		# a popup when the customer / supplier picked on a new voucher would be refused -- see party_alert.py
		"public/js/party_gate_alert.js",
		# the Return Reason Template picker on a debit note, as on a sales return
		"public/js/sales_return_reason.js",
		# the tracking Assets this invoice raised, as their own Asset Tracking section
		"public/js/asset_tracking_links.js",
	],
	# a tracking-only Asset (asset_tracking_item.py) offers no capitalization actions
	"Asset": "public/js/asset_tracking_links.js",
	# cancelling a payment must not demand the advice behind it be cancelled as well;
	# and a cleared post-dated cheque is banked from the cheque's own entry
	"Payment Entry":      [
		"public/js/payment_entry_cancel.js",
		"public/js/payment_entry_pdc.js",
		# Get Outstanding Journal Entries, beside the Invoices and Orders buttons -- see payment_entry_journal.py
		"public/js/payment_entry_journal.js",
		# a popup when the supplier being paid would be refused -- see party_alert.py
		"public/js/party_gate_alert.js",
	],
	# open item census + submit gate companion view
	"Period Closing Voucher": "public/js/period_closing_voucher.js",
	# every row's cost centre comes from that row's Branch, and a branch-less user is asked
	# to choose one rather than handed the company default
	"Journal Entry": "public/js/journal_entry_cost_center.js",
}

# include js, css files in header of web template
# web_include_css = "/assets/sf_trading/css/sf_trading.css"
# web_include_js = "/assets/sf_trading/js/sf_trading.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "sf_trading/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
doctype_list_js = {
	"Purchase Invoice": ["public/js/payment_advice_list_action.js", "public/js/purchase_invoice_list.js"],
	"Sales Invoice": ["public/js/payment_advice_list_action.js", "public/js/sales_invoice_list.js"],
	"Purchase Order": ["public/js/payment_advice_list_action.js", "public/js/purchase_order_list.js"],
	"Sales Order": ["public/js/payment_advice_list_action.js", "public/js/sales_order_list.js"],
	# Approve / Bypass Approval for the selected masters -- see master_approval.py
	"Customer": "public/js/master_approval_list.js",
	"Supplier": "public/js/master_approval_list.js",
	# Item: the approval actions, and a Print Label button on each stock / fixed-asset row -- see item_label.py
	"Item": ["public/js/master_approval_list.js", "public/js/item_label.js", "public/js/item_label_list.js"],
}

# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "sf_trading/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# the Item Label print format reads its barcode, price and text from here -- see item_label.py
jinja = {"methods": ["sf_trading.item_label.label_context"]}

# jinja = {
# 	"methods": "sf_trading.utils.jinja_methods",
# 	"filters": "sf_trading.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "sf_trading.install.before_install"
# after_install = "sf_trading.install.after_install"

after_migrate = [
	"sf_trading.inter_branch.ensure_branch_accounting_dimension",
	# Company/Sales Invoice Item fields + the two stock-timing Account Types
	"sf_trading.stock_billing_setup.setup",
	# per-company switch arming the Period Closing open item gate; runs after the
	# stock billing setup because it anchors below the field that one creates
	"sf_trading.period_closing.ensure_custom_fields",
	# header expense account for a Material Issue
	"sf_trading.stock_entry_expense.ensure_custom_fields",
	# the link from a cheque's Internal Transfer back to the cheque it banks
	"sf_trading.pdc_transfer.ensure_custom_fields",
	# the referenced document's own date, on the Payment Entry references grid
	"sf_trading.payment_entry_reference_date.ensure_custom_fields",
	# Sales Performance: number cards, charts, dashboard and the workspace that holds them
	"sf_trading.sales_performance.setup",
	# put Payment Unreconciliation beside Payment Reconciliation, where people look for it
	"sf_trading.payment_unreconciliation.ensure_workspace_links",
	# the invoice's Sales Person, Payment Mode and Delivery Person, on the order
	"sf_trading.sales_order_fields.ensure_custom_fields",
	# the refund a return will pay once it is approved
	"sf_trading.planned_payment.ensure_custom_fields",
	# a System Settings value with no global default row is blanked by its own form
	"sf_trading.system_defaults.sync_system_settings_defaults",
	# a secondary Cost Center / Warehouse link, seeded from whoever raised the document, must not
	# decide who may SEE it -- every parent link field gates the list
	"sf_trading.user_permission_fields.apply",
	# GS Issue 17: the Sales Order cancellation-remark field, and the Customer cash-sale override
	"sf_trading.sales_order_governance.ensure_custom_fields",
	# the Purchase Order cancellation-remark field
	"sf_trading.purchase_order_cancel.ensure_custom_fields",
	# the Purchase Order's Verbal (No Quotation) tick and its comment -- see purchase_order_procurement.py
	"sf_trading.purchase_order_procurement.ensure_custom_fields",
	# GS Issue 11: cached phone field on Customer/Supplier
	"sf_trading.party_contact_cache.ensure_custom_fields",
	# GS Issue 25: PDC Rejected-status field
	"sf_trading.pdc_transfer.ensure_rejection_field",
	# the one Difference Account a Stock Reconciliation may use, on Company
	"sf_trading.stock_reconciliation_account.ensure_custom_fields",
	# support tickets: the Tickets workspace, and Help -> Raise a Support Ticket
	"sf_trading.ticket.setup",
	# landed cost charge rows name the expense entry that booked them
	"sf_trading.landed_cost.ensure_custom_fields",
	# "Import Without Landed Cost" and its reason, on the Purchase Invoice
	"sf_trading.landed_cost_with_invoice.ensure_custom_fields",
	# the Report No column on Report, and a number for every report that has none
	"sf_trading.report_numbering.ensure_custom_fields",
	# the Get Outstanding Journal Entries button on the Payment Entry
	"sf_trading.payment_entry_journal.ensure_custom_fields",
	# every cost centre mirrored, and the Alternative Cost Center accounting dimension -- see alternative_cost_center.py
	"sf_trading.alternative_cost_center.ensure_dimension",
	# the cancellation reason picked from a list, on Sales Order and Purchase Order
	"sf_trading.order_cancellation.ensure_custom_fields",
	"sf_trading.order_cancellation.seed_reasons",
	# the standard Supporting Document Types (a fresh install marks the seed patch done unrun)
	"sf_trading.party_documents.seed_document_types",
	# the documents grid at the end of each master's Settings tab, saved field orders included
	"sf_trading.party_documents.place_documents_grid",
	# a Quotation is cancelled through a request too, where the Sales Order is
	"sf_trading.approval_routing.ensure_quotation_workflow",
	# PM's own Actions entry for a cancellation request stays hidden until a reason is picked
	"sf_trading.approval_routing.ensure_request_condition",
	# Strict Stock Reservation switches: Company check + Warehouse override
	"sf_trading.strict_stock_reservation.ensure_custom_fields",
	# the Goods In link on a Purchase Invoice row
	"sf_trading.goods_in.ensure_custom_fields",
	# Customer / Supplier / Item approval: approver access, the three PM Workflows, the switches, and the
	# first time, the start of the approval -- see master_approval.py
	"sf_trading.master_approval.setup",
	# the Master Rules Manager role and its access to the Party Master Rule Set -- see party_rules.py
	"sf_trading.party_rules.setup",
	# the physical stock count, system stock and variance on a Purchase Order row
	"sf_trading.po_stock_count.ensure_custom_fields",
	# the Updates Hub workspace -- see announcements.py
	"sf_trading.announcements.setup",
]

after_install = [
	"sf_trading.order_cancellation.seed_reasons",
	"sf_trading.party_documents.seed_document_types",
]

# Uninstallation
# ------------

# before_uninstall = "sf_trading.uninstall.before_uninstall"
# after_uninstall = "sf_trading.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "sf_trading.utils.before_app_install"
# after_app_install = "sf_trading.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "sf_trading.utils.before_app_uninstall"
# after_app_uninstall = "sf_trading.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "sf_trading.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"Item": "sf_trading.branch_defaults.item_permission_query",
	"Customer": "sf_trading.customer_permission.permission_query_conditions_for_customer",
	# a ticket is seen by the people on it, and by Ticket / System Managers
	"Ticket": "sf_trading.ticket.get_permission_query_conditions",
	# a branch user sees the delivery people who serve one of their branches (the Branches table)
	"Driver": "sf_trading.driver_branches.permission_query_conditions",
}

has_permission = {
	"Ticket": "sf_trading.ticket.has_permission",
	"Driver": "sf_trading.driver_branches.has_permission",
	"Purchase Receipt": "sf_trading.purchase_receipt_gate.has_permission",
}

# the desk drops Create / Cancel / Delete of Purchase Receipt for a user GRN is disabled for (reading stays
# open) -- see purchase_receipt_gate.py
boot_session = [
	"sf_trading.purchase_receipt_gate.boot_session",
	# which forms carry the Alternative Cost Center field, so the desk filters its picker on every one of them
	"sf_trading.alternative_cost_center.boot_carriers",
	# each report the user may open carries its number, so the search bar finds it by RPT-0042
	"sf_trading.report_numbering.boot_numbers",
	# whether account links stay clickable while a financial report column is searched
	"sf_trading.financial_statements_search_links.boot_session",
	# how many updates wait for the person, so the navbar megaphone shows its badge with no extra request
	"sf_trading.announcements.boot_session",
]

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	"Sales Invoice": "sf_trading.overrides.sales_invoice_class.CustomSalesInvoice",
	# the credit rule the invoice already had: core counts every unbilled order as exposure and
	# knows nothing about custom_payment_mode, so a cash order tripped the customer's limit
	"Sales Order": "sf_trading.overrides.sales_order_class.CustomSalesOrder",
	"Delivery Note": "sf_trading.overrides.delivery_note_class.CustomDeliveryNote",
	"Purchase Invoice": "sf_trading.overrides.purchase_invoice_class.CustomPurchaseInvoice",
	# a return needs no Purchase Order, as a debit note already doesn't (purchase_receipt_class.py)
	"Purchase Receipt": "sf_trading.overrides.purchase_receipt_class.CustomPurchaseReceipt",
	# lets a custom_track_as_asset shadow Asset (asset_tracking_item.py) be saved/submitted
	# normally after its initial insert -- core's own Asset.validate_item() refuses any Asset
	# whose Item isn't a Fixed Asset Item on every save, not only the first
	"Asset": "sf_trading.overrides.asset_class.CustomAsset",
	# two prices of one item, one list and one UOM may coexist when they serve different branches
	# (Applicable Branches) -- see sf_trading/overrides/item_price_class.py
	"Item Price": "sf_trading.overrides.item_price_class.CustomItemPrice",
	# a draft voucher may name a draft stock invoice: the voucher of an import purchase is drawn up with it
	# and submitted by its approval (landed_cost_with_invoice.py)
	"Landed Cost Voucher": "sf_trading.overrides.landed_cost_voucher_class.CustomLandedCostVoucher",
}

# Document Events
# ---------------
# Hook on document methods and events

# Pushes header accounting dimensions (branch, cost_center, project) down to
# item rows (and cost_center to taxes) on every transaction doctype.
_CC_HOOK = "sf_trading.branch_defaults.propagate_dimensions_to_items"
_BRANCH_HOOK = "sf_trading.inter_branch.auto_set_branch_from_warehouse"
# Applies the branch's letter head to the document. Runs on validate, after
# _BRANCH_HOOK so an auto-resolved branch is honoured.
_LH_HOOK = "sf_trading.branch_defaults.set_letter_head_from_branch"
_SP_HOOK = "sf_trading.api.selling_price_validation.validate_selling_price"
# Prices the document from its branch's Price List (Applicable for Branches), when the branch
# names one and nobody has deliberately picked another list.
_BPL_HOOK = "sf_trading.branch_price_list.apply_branch_price_list"
# Refuses a price list the document's branch is not configured for. Only bites when the branch
# names any: a branch with none has no opinion.
_BPL_GUARD = "sf_trading.branch_price_list.validate_price_list_allowed"
# No buying document (or payment) against a supplier still missing Tax ID, phone, email or an
# attached document -- see sf_trading/supplier_validation.py.
_SUPPLIER_GATE = "sf_trading.supplier_validation.validate_supplier_at_transaction"
# a new supplier / B2B customer must be complete, and no party may hold an expired document that
# carries a Financial Implication -- see sf_trading/party_documents.py
_MASTER_GATE = "sf_trading.party_documents.validate_party_at_transaction"
# the customer / supplier and every item on a selling or buying document (and a supplier payment) is
# approved, where the Customer / Supplier / Item approval covers it -- see sf_trading/master_approval.py
_APPROVAL_GATE = "sf_trading.master_approval.validate_master_approval_at_transaction"
# Quantity a Goods In already recorded cannot be received again by a Purchase Receipt or a
# stock-updating Purchase Invoice made straight from the Purchase Order -- see sf_trading/goods_in.py.
_GOODS_IN_GATE = "sf_trading.goods_in.validate_receipt_against_goods_in"

# Requires custom_return_reason to say more than just "Other" when that is the picked
# custom_return_reason_template -- a different, narrower concern than sales_return.py's
# window/approval gate: this is only about WHY a return is being made. See
# sf_trading/sales_return_reason.py.
_RETURN_REASON_HOOK = "sf_trading.sales_return_reason.validate_return_reason"
# ...and requires a return to pick custom_return_reason_template at all -- on the requester's own
# save, submit and Send for Approval, never on an approver's action (sales_return_reason.py)
_RETURN_REASON_REQUIRED = "sf_trading.sales_return_reason.require_reason_template"

# A custom_track_as_asset item stays on its normal expense account (no capitalization) but
# still gets a draft Asset for Asset Movement tracking (location/custodian/history). Fires on
# whichever of PR/PI actually books the purchase; see sf_trading/asset_tracking_item.py.
_ASSET_TRACKING_HOOK = "sf_trading.asset_tracking_item.create_tracking_asset"

# Picks the purchase tax template matching the document currency (default
# template for company-currency docs, "Import VAT 0%" otherwise).
_PTT_HOOK = "sf_trading.purchase_tax_template.set_template_by_currency"

# Books a Delivery Note's cost to the Stock Delivered But Not Billed account
# instead of COGS, for companies that enable it. See sf_trading/sdbnb.py.
_SDBNB_DN_HOOK = "sf_trading.sdbnb.set_delivery_note_expense_account"
# The mirror case — invoiced before delivery. Runs after _SDBNB_DN_HOOK so the
# invoice-linked rows it claims are the ones SDBNB deliberately left alone.
_SBND_DN_HOOK = "sf_trading.sbnd.set_delivery_note_expense_account"

doc_events = {
	# every cost centre has a mirror master that the Alternative Cost Center dimension points at
	"Cost Center": {
		"after_insert": "sf_trading.alternative_cost_center.sync_cost_center",
		"on_update": "sf_trading.alternative_cost_center.sync_cost_center",
		"on_trash": "sf_trading.alternative_cost_center.drop_cost_center",
		"after_rename": "sf_trading.alternative_cost_center.follow_rename",
	},
	# a new account with no number gets the next one in its parent's series, before ERPNext builds
	# the account's name from it -- see sf_trading/account_numbering.py
	"Account": {
		"before_insert": "sf_trading.account_numbering.number_new_account",
	},
	# every Report carries a short number (RPT-0042) shown in the Report list, and keeps it through a
	# re-sync from its app or a rename -- see sf_trading/report_numbering.py
	"Report": {
		"before_insert": "sf_trading.report_numbering.number_new_report",
		"after_rename": "sf_trading.report_numbering.follow_rename",
	},
	# a change to Disable Purchase Receipt (GRN) or its roster reaches the desk without a re-login
	"SF Trading Settings": {
		"on_update": "sf_trading.purchase_receipt_gate.clear_boot_cache",
	},
	# the branches an Item Price is limited to (Applicable Branches): none twice -- see sf_trading/item_price_branch.py
	"Item Price": {
		"validate": "sf_trading.item_price_branch.validate_item_price_branches",
	},
	"Stock Entry": {
		# before_validate, so the header account faces ERPNext's own account checks
		# rather than being written past them
		"before_validate": "sf_trading.stock_entry_expense.apply_expense_account",
	},
	"Period Closing Voucher": {
		# a period must not close while it still carries unbilled / undelivered items
		"before_submit": "sf_trading.period_closing.validate_open_items",
	},
	"Payment Entry": {
		# a reference with no Payment Term to a term-allocated document gets its term (split per instalment) BEFORE
		# core's own validate refuses it -- see payment_term_split.py
		"before_validate": "sf_trading.payment_term_split.fill_reference_terms",
		"validate": [
			# a Pay or Receive entry must name a voucher (SF Trading Settings switch) -- see payment_entry_voucher_gate.py
			"sf_trading.payment_entry_voucher_gate.validate_has_voucher",
			"sf_trading.api.payment_advice_hooks.validate_payment_entry_advice",
			# each reference row carries the date of the document it is paying
			"sf_trading.payment_entry_reference_date.set_reference_dates",
			# a transfer that names a post-dated cheque has to add up, however it was built
			"sf_trading.pdc_transfer.validate",
			# the journal a cheque names as having banked it must credit the account it is held in
			"sf_trading.pdc_transfer.validate_journal",
			_SUPPLIER_GATE,
			_MASTER_GATE,
			_APPROVAL_GATE,
		],
		# an Internal Transfer raised from a post-dated cheque closes that cheque when it is
		# submitted, and re-opens it if it is cancelled
		"on_submit": [
			"sf_trading.api.payment_advice_hooks.on_payment_entry_submit",
			"sf_trading.pdc_transfer.on_submit",
		],
		"on_cancel": [
			"sf_trading.api.payment_advice_hooks.on_payment_entry_cancel",
			"sf_trading.pdc_transfer.on_cancel",
		],
		# an invoice consuming an advance adds its reference row to a payment that is already
		# submitted, which never passes validate
		"on_update_after_submit": [
			"sf_trading.payment_entry_reference_date.fill_missing_reference_dates",
			# naming the journal that banked a cheque moves the cheque on
			"sf_trading.pdc_transfer.validate_journal",
			"sf_trading.pdc_transfer.on_update_after_submit",
		],
	},
	"Journal Entry": {
		# a journal core raises and submits itself (asset depreciation, its reversal, Scrap Asset) is
		# marked system-generated before anything validates, so permission_manager lets it through
		# -- see sf_trading/system_entries.py
		"before_validate": "sf_trading.system_entries.mark_system_generated",
		# validate, not before_validate: the controller fills `debit` from
		# debit_in_account_currency * exchange_rate during its own validate, and a hooked
		# handler runs after it, so the debit rows are already known here
		"validate": [
			"sf_trading.api.loyalty_reward_rows.validate_loyalty_reward_rows",
			# a row's cost centre follows the Branch on that row; core seeds it from the
			# company default instead, which put every branch entry on head office
			"sf_trading.journal_entry_cost_center.set_cost_center_from_branch",
			# a returned-cheque journal never reverses more than is left on its cheque
			"sf_trading.pdc_transfer.journal_validate",
		],
		# a returned-cheque journal moves its cheque to Returned, and back when cancelled
		"on_submit": "sf_trading.pdc_transfer.journal_on_change",
		"on_cancel": "sf_trading.pdc_transfer.journal_on_change",
		# `cost_center` on Journal Entry Account carries allow_on_submit, so it can still be
		# edited on a SUBMITTED entry -- and `validate` never runs on that path. Core's
		# on_update_after_submit reposts the ledger when a row changes, so the same rule has to
		# hold here or a post-submit edit would quietly re-post the wrong cost centre.
		"before_update_after_submit": "sf_trading.journal_entry_cost_center.set_cost_center_from_branch",
	},
	"Customer": {
		"validate": [
			# fills core's own mobile_no from the G11 phone cache before any mandatory-field
			# check runs -- see party_mobile_no_prefill.js's client-side twin
			"sf_trading.party_contact_cache.fill_mobile_no_from_cache",
			"sf_trading.api.customer_override.validate",
			# GS Issue 1: a Company-type customer must carry CR + VAT before it can be saved,
			# new or existing -- see sf_trading/party_completeness.py for the field-list note
			"sf_trading.party_completeness.validate_company_fields",
			"sf_trading.customer_permission.validate_credit_branch_access",
			# GS Issue 19: keep every branch's own Credit Limit sub-allocation from adding up to
			# more than the customer's own company-wide Credit Limit
			"sf_trading.customer_permission.validate_branch_credit_limit_allocation",
			# every save keeps the display name in Title Case -- see sf_trading/title_case.py
			"sf_trading.title_case.title_case_party",
			# only a credit approver may switch "Allow Cash Sales Without Credit Documents"
			"sf_trading.sales_order_governance.validate_cash_override_change",
			# the Create Customer dialog's VAT format / duplicate VAT / mobile rules, on every way of saving
			"sf_trading.party_master_rules.validate_master",
			# the Approval Status moves by the approval only, and into Pending / Approved only when complete; before
			# the next rule, so a master sent for approval hears everything it lacks at once
			"sf_trading.master_approval.validate_master",
			# supporting-document rows, and what a new B2B customer must carry
			"sf_trading.party_documents.validate_master",
			# one row per branch, and the Branch Cash Limits may not add up to more than the Cash Collection Limit
			"sf_trading.sales_order_limits.validate_branch_cash_limit_allocation",
		],
		"on_update": "sf_trading.party_documents.relink_attachments",
		"onload": "sf_trading.party_documents.set_onload",
		"before_save": [
			"sf_trading.customer_permission.auto_add_branch_on_credit_limit",
			# after every validate hook of every app (the backup app also title-cases names, with str.title(), which
			# turns John's into John'S) and before the receivable ledger is named from the name
			"sf_trading.title_case.title_case_party",
			"sf_trading.party_accounts.create_customer_receivable_account",
		],
		# a brand new customer starts as a Draft of the Customer approval -- see master_approval.py
		"before_insert": [
			"sf_trading.master_approval.set_initial_status",
			# before Frappe makes the ID from the name: the ID comes out in Title Case too
			"sf_trading.title_case.title_case_party",
		],
		# a rename from the desk is put in Title Case as well
		"before_rename": "sf_trading.title_case.title_case_rename",
	},
	"Supplier": {
		"validate": [
			"sf_trading.party_contact_cache.fill_mobile_no_from_cache",
			# Tax ID mandatory + unique and a document attached, for every supplier, existing
			# ones too -- see sf_trading/supplier_validation.py
			"sf_trading.supplier_validation.validate",
			"sf_trading.title_case.title_case_party",
			# the Create Supplier dialog's Tax ID format / duplicate / mobile rules, on every way of saving
			"sf_trading.party_master_rules.validate_master",
			# the Approval Status moves by the approval only, and into Pending / Approved only when complete; before
			# the next rule, so a master sent for approval hears everything it lacks at once
			"sf_trading.master_approval.validate_master",
			# supporting-document rows, and what a new supplier must carry
			"sf_trading.party_documents.validate_master",
		],
		"on_update": "sf_trading.party_documents.relink_attachments",
		"onload": "sf_trading.party_documents.set_onload",
		# title case first (after every validate hook of every app), then the payable ledger named from it
		"before_save": [
			"sf_trading.title_case.title_case_party",
			"sf_trading.party_accounts.create_supplier_payable_account",
		],
		"before_insert": [
			"sf_trading.title_case.title_case_party",
			# a new supplier starts as a Draft of the Supplier approval -- see master_approval.py
			"sf_trading.master_approval.set_initial_status",
		],
		"before_rename": "sf_trading.title_case.title_case_rename",
		"after_insert": "sf_trading.supplier_validation.remind_attachment",
	},
	# a Salesperson carries the same Cash Collection Limit / Payment Collection Days / Branch Cash Limits as a Driver and a Customer
	"Sales Person": {
		"validate": "sf_trading.sales_order_limits.validate_branch_cash_limit_allocation",
	},
	"Driver": {
		"validate": [
			# GS Issue 19: keep every branch's own Cash Limit sub-allocation from adding up to more
			# than this driver's own overall Cash Collection Limit
			"sf_trading.api.sales_invoice_override.validate_driver_branch_cash_limit_allocation",
			# the Branches table: one row per branch, and a branch user adds only their own
			"sf_trading.driver_branches.validate_branches",
		],
	},
	"Sales Invoice": {
		"before_validate": [
			"sf_trading.api.sales_invoice_override.before_validate",
			_CC_HOOK,
			"sf_trading.branch_defaults.override_payment_accounts_from_branch",
			# a Delivery Person belongs to a cash sale; the field is merely hidden on the others
			"sf_trading.api.sales_invoice_override.clear_driver_for_non_cash",
			# before_validate, because the controller's own validate is what reads the two
			# advance switches and calls set_advances()
			"sf_trading.overrides.sales_invoice_advance.set_advance_allocation",
			_BPL_HOOK,
			# the salesman on the header belongs in ERPNext's own Sales Team table, or every
			# native sales-person report reads only the invoices somebody typed on a form
			"sf_trading.sales_team_sync.set_sales_team",
		],
		"validate": [
			"sf_trading.api.sales_invoice_override.validate",
			"sf_trading.api.sales_invoice_override.validate_driver_payment",
			# GS Issue 19: an amount cap on top of the days-only check above
			"sf_trading.api.sales_invoice_override.validate_driver_cash_limit",
			# the delivery person must serve this invoice's branch (their Branches table)
			"sf_trading.driver_branches.validate_driver_serves_branch",
			# a late return cannot be SAVED, so it cannot be parked in drafts either
			"sf_trading.sales_return.validate_return_window",
			_BPL_GUARD,
			_BRANCH_HOOK,
			_LH_HOOK,
			_SP_HOOK,
			_RETURN_REASON_REQUIRED,
			_RETURN_REASON_HOOK,
			# freezes the valuation rate on rows invoiced ahead of delivery
			"sf_trading.sbnd.freeze_valuation_rate",
			# a cash return may not ENTER the approval chain with no refund planned: the approval
			# submits it server-side, where the payment popup cannot ask anything
			"sf_trading.planned_payment.require_plan_before_approval",
			# a term-allocated template must come with a schedule that names its terms -- see payment_term_split.py
			"sf_trading.payment_term_split.repair_term_less_schedule",
			# a Cash sale is due the day it is made; runs after the controller built the schedule
			"sf_trading.cash_due_date.apply_cash_due_date",
			# the customer and every item approved, where the approval covers them -- see master_approval.py
			_APPROVAL_GATE,
		],
		# the refund a return promised is checked while refusing is still safe, and paid the
		# moment the return is submitted -- approval and refund land together. before_submit,
		# not validate, so a draft can still be saved while a customer's contact details are
		# being fixed elsewhere -- only SUBMIT (the actual "billable") is refused.
		"before_submit": [
			"sf_trading.planned_payment.validate_planned_payments",
			# GS Issue 20: a customer missing a phone-bearing Contact is not billable, on an
			# existing customer exactly as much as a new one
			"sf_trading.sales_order_governance.validate_customer_contact_at_transaction",
			# GS Issue 13: a credit customer additionally needs 2 contacts + an attachment -- this
			# is now the ONLY 2-contact-number rule (2026-09-27: dropped as a B2B-specific rule,
			# client call -- a B2B customer with no credit standing no longer needs a 2nd number)
			"sf_trading.sales_order_governance.validate_credit_customer_requirements_at_transaction",
			_MASTER_GATE,
		],
		"on_submit": [
			"sf_trading.inter_company.sales_invoice_on_submit",
			"sf_trading.api.quotation.update_quotation_status_from_invoice",
			"sf_trading.planned_payment.apply_planned_payments",
		],
		"on_cancel": "sf_trading.api.quotation.update_quotation_status_from_invoice",
		# a draft sale does not print; a draft return does -- see sf_trading/draft_print.py
		"before_print": "sf_trading.draft_print.block_draft_sale_print",
	},
	"Sales Order": {
		"before_validate": [
			_CC_HOOK,
			_BPL_HOOK,
			# a delivery person belongs to a cash sale, on the order as on the invoice
			"sf_trading.api.sales_invoice_override.clear_driver_for_non_cash",
			# the order carries custom_sales_person too, so it gets the same mirror
			"sf_trading.sales_team_sync.set_sales_team",
		],
		"validate": [
			_LH_HOOK,
			_SP_HOOK,
			_BPL_GUARD,
			# an overdue credit invoice refuses a credit order, as it refuses a credit invoice
			"sf_trading.api.sales_invoice_override.validate",
			"sf_trading.api.sales_invoice_override.validate_driver_payment",
			"sf_trading.api.sales_invoice_override.validate_driver_cash_limit",
			"sf_trading.driver_branches.validate_driver_serves_branch",
			# a term-allocated template must come with a schedule that names its terms -- see payment_term_split.py
			"sf_trading.payment_term_split.repair_term_less_schedule",
			# a Cash order is due the day it is made, same as the invoice
			"sf_trading.cash_due_date.apply_cash_due_date",
			# the Driver's cash limit and payment days, by Customer and by Salesperson -- see sales_order_limits.py
			"sf_trading.sales_order_limits.validate_sales_order_limits",
			# the customer and every item approved, where the approval covers them -- see master_approval.py
			_APPROVAL_GATE,
		],
		# before_submit, not validate: a draft order can still be saved while a customer's
		# contact details are being fixed elsewhere -- only SUBMIT is refused.
		"before_submit": [
			# rows in a strict warehouse reserve their stock on submit, no others do -- see
			# sf_trading/strict_stock_reservation.py
			"sf_trading.strict_stock_reservation.reserve_strict_rows_on_submit",
			# GS Issue 20: same contact-completeness rule as Sales Invoice
			"sf_trading.sales_order_governance.validate_customer_contact_at_transaction",
			# GS Issue 13: same credit-customer rule as Sales Invoice -- the only 2-contact-number
			# rule now (2026-09-27: no separate B2B-specific one, see Sales Invoice's own comment)
			"sf_trading.sales_order_governance.validate_credit_customer_requirements_at_transaction",
			_MASTER_GATE,
		],
		"before_cancel": [
			# GS Issue 17 / tracker #23: a remark is mandatory, and only a Sales Manager may cancel
			# (everyone else requests it) -- checked here because before_cancel is the one hook that
			# runs before docstatus flips, see sf_trading/sales_order_governance.py
			"sf_trading.sales_order_governance.before_cancel_require_remark_and_branch_head",
		],
		# a cancellation request carries its reason into the Cancellation Remark
		"before_update_after_submit": "sf_trading.approval_routing.capture_cancellation_reason",
	},
	"Quotation": {
		"before_validate": [_CC_HOOK, _BPL_HOOK],
		"validate": [_LH_HOOK, _SP_HOOK, _BPL_GUARD],
		# cancelled as a Sales Order is: a reason, and only a Sales Manager -- everyone else requests it
		"before_cancel": "sf_trading.sales_order_governance.before_cancel_require_remark_and_branch_head",
		"before_update_after_submit": "sf_trading.approval_routing.capture_cancellation_reason",
	},
	"Delivery Note": {
		"before_validate": [_CC_HOOK, _BPL_HOOK],
		# _SDBNB_DN_HOOK last: it reads the expense_account the controller and the
		# earlier hooks have already settled on, then re-points stock rows at the
		# Stock Delivered But Not Billed account when the company opts in.
		"validate": [_BRANCH_HOOK, _LH_HOOK, _SP_HOOK, _BPL_GUARD, _SDBNB_DN_HOOK, _SBND_DN_HOOK, _APPROVAL_GATE],
	},
	"Company": {
		"validate": [
			"sf_trading.sdbnb.validate_company_sdbnb",
			"sf_trading.sbnd.validate_company_sbnd",
			"sf_trading.stock_reconciliation_account.validate_company_account",
			"sf_trading.strict_stock_reservation.validate_company",
		],
	},
	# Strict Stock Reservation: the warehouse's own override, and reservations only where it
	# applies -- see sf_trading/strict_stock_reservation.py
	"Warehouse": {
		"validate": "sf_trading.strict_stock_reservation.validate_warehouse",
	},
	"Stock Reservation Entry": {
		"validate": "sf_trading.strict_stock_reservation.validate_reservation_warehouse",
	},
	# Purpose "Stock Reconciliation": the Company's own Difference Account and no other, and none
	# at all without one -- see sf_trading/stock_reconciliation_account.py
	"Stock Reconciliation": {
		"before_validate": "sf_trading.stock_reconciliation_account.set_difference_account",
		"validate": "sf_trading.stock_reconciliation_account.validate_difference_account",
		# tracker #22: a reconciliation posts only with its count sheet attached
		"before_submit": "sf_trading.approval_routing.require_attachment",
	},
	"Purchase Invoice": {
		"before_validate": [
			"sf_trading.inter_company.purchase_invoice_before_validate",
			# goods a receipt has already booked must not be taken into stock a second time by
			# the bill, whoever ticked the box and whenever they ticked it
			"sf_trading.api.purchase_order_invoice.untick_stock_when_the_goods_already_arrived",
			# right after it: an invoice that IS the goods' arrival keeps Update Stock on, whoever
			# but Administrator tries to turn it off -- see api/purchase_order_invoice.py
			"sf_trading.api.purchase_order_invoice.require_stock_update",
			_CC_HOOK,
			_PTT_HOOK,
			# before_validate, because the controller's own validate is what reads these
			# two switches and calls set_advances()
			"sf_trading.overrides.purchase_invoice.set_advance_allocation",
			_BPL_HOOK,
		],
		"validate": [
			"sf_trading.overrides.purchase_invoice.validate",
			_BRANCH_HOOK,
			_LH_HOOK,
			_BPL_GUARD,
			_SUPPLIER_GATE,
			_MASTER_GATE,
			_APPROVAL_GATE,
			"sf_trading.landed_cost.validate_charges",
			# goods come in by one route only: a Goods In's quantity is invoiced from that Goods In
			_GOODS_IN_GATE,
			# a debit note names its reason from Purchase Return Reason, as a sales return does
			_RETURN_REASON_REQUIRED,
			_RETURN_REASON_HOOK,
			# a term-allocated template with a schedule that names no term could never be paid -- see payment_term_split.py
			"sf_trading.payment_term_split.repair_term_less_schedule",
			# an import needs its Landed Cost Voucher before it goes for approval; last, so the
			# other refusals come first -- see landed_cost_with_invoice.py
			"sf_trading.landed_cost_with_invoice.validate_invoice",
		],
		"on_save": "sf_trading.overrides.purchase_invoice.on_save",
		# a draft invoice that was changed pulls its draft voucher along
		"on_update": "sf_trading.landed_cost_with_invoice.follow_invoice",
		"on_submit": [
			# first: the draft Landed Cost Vouchers are submitted by the invoice's own approval, as
			# soon as core has posted the invoice's stock and ledger entries
			"sf_trading.landed_cost_with_invoice.submit_vouchers",
			"sf_trading.api.purchase_return.auto_create_pr_return",
			"sf_trading.goods_in.update_invoiced_qty",
			_ASSET_TRACKING_HOOK,
		],
		"on_cancel": "sf_trading.goods_in.update_invoiced_qty",
		# the invoice's submitted vouchers go first, so one cancellation is enough; and a deleted
		# draft takes its draft vouchers with it
		"before_cancel": "sf_trading.landed_cost_with_invoice.cancel_vouchers",
		"on_trash": "sf_trading.landed_cost_with_invoice.delete_vouchers",
		# Valuation charges are landed costs: held to the expense entry they name
		"before_submit": [
			"sf_trading.landed_cost_with_invoice.require_voucher_at_submit",
			"sf_trading.landed_cost.require_links",
		],
	},
	"Purchase Order": {
		"before_validate": [_CC_HOOK, _PTT_HOOK, _BPL_HOOK],
		"validate": [
			_LH_HOOK,
			_BPL_GUARD,
			_SUPPLIER_GATE,
			_MASTER_GATE,
			_APPROVAL_GATE,
			"sf_trading.payment_term_split.repair_term_less_schedule",
			# a Material Request behind every line and enough Request-for-Quotation quotations, unless it is a
			# verbal order (SF Trading Settings > Purchase Order Procurement Rules) -- see purchase_order_procurement.py
			"sf_trading.purchase_order_procurement.validate",
			# the physical stock count's system stock and variance, row by row -- see po_stock_count.py
			"sf_trading.po_stock_count.refresh",
			# ... and no order enters the approval chain with a stock row uncounted
			"sf_trading.po_stock_count.require_count_on_entry",
		],
		# every stock row counted before submit, while SF Trading Settings asks for it
		"before_submit": "sf_trading.po_stock_count.require_count",
		# a remark is mandatory, checked before docstatus flips -- see purchase_order_cancel.py
		"before_cancel": "sf_trading.purchase_order_cancel.before_cancel_require_remark",
	},
	"Purchase Receipt": {
		"before_validate": [_CC_HOOK, _PTT_HOOK, _BPL_HOOK],
		"validate": [_BRANCH_HOOK, _LH_HOOK, _BPL_GUARD, _SUPPLIER_GATE, _MASTER_GATE, _APPROVAL_GATE, "sf_trading.landed_cost.validate_charges", _GOODS_IN_GATE],
		"before_submit": "sf_trading.landed_cost.require_links",
		"on_submit": _ASSET_TRACKING_HOOK,
	},
	# every charge names the expense entry it absorbs, and may not take more than it booked
	"Landed Cost Voucher": {
		"validate": [
			"sf_trading.landed_cost.validate_charges",
			# a voucher for a draft invoice names that invoice alone and is locked once it is out for approval
			"sf_trading.landed_cost_with_invoice.validate_voucher",
		],
		"before_submit": "sf_trading.landed_cost.require_links",
		"on_trash": "sf_trading.landed_cost_with_invoice.guard_voucher_trash",
		"on_cancel": "sf_trading.landed_cost_with_invoice.remind_after_cancel",
	},
	# any entry the supporting-document rules cover, posted with nothing attached, says it is now
	# on the Pending Supporting Documents report (rule check first, so other doctypes cost nothing)
	"*": {
		"on_submit": "sf_trading.supporting_documents.remind_on_submit",
		# an alternative cost centre on a voucher is an enabled leaf of ANOTHER company -- see alternative_cost_center.py
		"validate": "sf_trading.alternative_cost_center.validate_voucher",
	},
	# a quotation is asked of a supplier before it is a supplier, so neither the completeness gate nor the
	# master gate runs here: the supplier is held to them on the Purchase Order made from the quotation
	"Supplier Quotation": {
		"before_validate": [_CC_HOOK, _PTT_HOOK, _BPL_HOOK],
		"validate": [_LH_HOOK, _BPL_GUARD],
	},
	# core logs the impersonation but drops the reason — put it back on the row
	"Activity Log": {
		"before_insert": "sf_trading.api.impersonation_log.capture_impersonation_reason",
	},
	# GS Issue 11: refresh the cached Customer/Supplier phone whenever the linked Contact changes.
	# Not on Customer/Supplier's own hooks -- a Contact created alongside a brand new party does
	# not exist yet at that party's own save time, only afterwards, on the Contact's own insert.
	"Contact": {
		# a phone added to a Saudi party's Contact has at least 10 digits (party_master_rules.py)
		"validate": "sf_trading.party_master_rules.validate_contact_phones",
		"on_update": "sf_trading.party_contact_cache.sync_from_contact",
		"on_trash": "sf_trading.party_contact_cache.clear_on_contact_trash",
	},
	# Same GS Issue 11 cache, from the other source: most of this bench's existing parties were
	# migrated with a phone on their Address and no Contact record at all -- see
	# party_contact_cache.py's module docstring.
	"Address": {
		# a Saudi B2B party's address is complete: Line 1, Building No, District, City, 5-digit Postal Code
		"validate": "sf_trading.party_master_rules.validate_b2b_address",
		"on_update": "sf_trading.party_contact_cache.sync_from_address",
		"on_trash": "sf_trading.party_contact_cache.clear_on_address_trash",
	},
	# GS Issue 14: a non-stock item auto-generates its own code (Stock Settings' existing
	# manual/Item-Code entry keeps governing stock items untouched -- see item_naming.py)
	"Item": {
		"autoname": "sf_trading.item_naming.autoname",
		# the Item master is kept in CAPITALS: before_insert runs before Frappe names the record, before_validate on every save
		# -- see sf_trading/item_case.py
		"before_insert": [
			"sf_trading.item_case.capitalise_item",
			# a new item starts as a Draft of the Item approval -- see master_approval.py
			"sf_trading.master_approval.set_initial_status",
		],
		# the Approval Status moves by the approval only
		"validate": "sf_trading.master_approval.validate_master",
		"before_validate": [
			"sf_trading.item_case.capitalise_item",
			# an item with no barcode gets its Item Code as one (CODE-128, stock UOM); after the line above so it is the capitalised code
			# -- see sf_trading/item_barcode.py
			"sf_trading.item_barcode.add_item_barcode",
		],
	},
	# a reply in a Ticket's comment box reaches everyone on the ticket. Both handlers return at
	# once for any other doctype, so a site that has not migrated the Ticket table is untouched.
	"Comment": {
		"after_insert": "sf_trading.ticket.on_comment",
	},
	# an assignment made or removed from a Ticket's sidebar reaches its Assigned To field
	"ToDo": {
		"on_update": "sf_trading.ticket.sync_assignee_from_todo",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"sf_trading.tasks.all"
# 	],
# 	"daily": [
# 		"sf_trading.tasks.daily"
# 	],
# 	"hourly": [
# 		"sf_trading.tasks.hourly"
# 	],
# 	"weekly": [
# 		"sf_trading.tasks.weekly"
# 	],
# 	"monthly": [
# 		"sf_trading.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "sf_trading.install.before_tests"

# Overriding Methods
# ------------------------------
#
override_whitelisted_methods = {
	"erpnext.controllers.queries.item_query": "sf_trading.api.item_search.search_items_with_stock_and_rate",
	# the return window refuses before the credit note is built — the one check no stale browser
	# and no re-painted toolbar can get past. See sf_trading/sales_return.py.
	"erpnext.accounts.doctype.sales_invoice.sales_invoice.make_sales_return": "sf_trading.sales_return.make_sales_return",
	# an invoice raised from an order nobody has received arrives with Update Stock ticked, so the
	# goods reach the ledger with the bill. See sf_trading/api/purchase_order_invoice.py.
	"erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice": "sf_trading.api.purchase_order_invoice.make_purchase_invoice",
	# "Add Column" on a report of more than ~5,000 rows sends every name in one query, which
	# frappe 15.114 hands to sqlparse and sqlparse refuses past 10,000 tokens. Read it in
	# batches. See sf_trading/api/query_report_columns.py.
	"frappe.desk.query_report.get_data_for_custom_field": "sf_trading.api.query_report_columns.get_data_for_custom_field",
	# ships sf_trading's extensions of other apps' reports with the report's own script, e.g. the
	# clickable counts on Customer Acquisition and Loyalty. See sf_trading/api/report_script.py.
	"frappe.desk.query_report.get_script": "sf_trading.api.report_script.get_script",
	# the Stock Reconciliation form fills the Company's own Difference Account, not Stock
	# Adjustment Account. See sf_trading/stock_reconciliation_account.py.
	"erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_difference_account": "sf_trading.stock_reconciliation_account.get_difference_account",
}
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# Every doctype a Payment Advice may be raised against gets the advice in its Connections,
# so "has this been sent for payment?" is answerable from the voucher itself. The list
# mirrors VALID_REFERENCE_DOCTYPES in the Payment Advice controller.
_ADVICE_DASHBOARD = "sf_trading.api.payment_advice_dashboard.get_dashboard_data"

override_doctype_dashboards = {
	"Quotation": ["sf_trading.api.quotation.get_dashboard_data"],
	"Sales Order": [_ADVICE_DASHBOARD],
	"Sales Invoice": [_ADVICE_DASHBOARD],
	"Purchase Order": [_ADVICE_DASHBOARD, "sf_trading.goods_in.purchase_order_dashboard"],
	"Purchase Invoice": [_ADVICE_DASHBOARD],
	"Journal Entry": [_ADVICE_DASHBOARD],
	"Dunning": [_ADVICE_DASHBOARD],
	# the payment raised from an advice, and the advice a payment was raised from, both ways
	"Payment Entry": ["sf_trading.api.payment_advice_dashboard.get_payment_entry_dashboard_data"],
	"Payment Advice": ["sf_trading.api.payment_advice_dashboard.get_payment_advice_dashboard_data"],
}

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
before_request = [
	"sf_trading.api.item_search.redirect_item_query_before_request",
	# the whitelist override above only covers the HTTP door; a saved custom report and an export
	# reach the same function by module attribute, so it is repointed here as well
	"sf_trading.api.query_report_columns.install",
	# Balance Sheet / Profit and Loss / Cash Flow read the ledger by account, not by walking the year
	"sf_trading.report_speed.install",
	# a price limited to some branches is priced only for those -- see sf_trading/item_price_branch.py
	"sf_trading.item_price_branch.install",
]
# a Prepared Report runs in a background job, which has no request
before_job = [
	"sf_trading.api.query_report_columns.install",
	"sf_trading.report_speed.install",
	"sf_trading.item_price_branch.install",
]
# after_request = ["sf_trading.utils.after_request"]

# Job Events
# ----------
# before_job = ["sf_trading.utils.before_job"]
# after_job = ["sf_trading.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"sf_trading.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Fixtures
# --------
fixtures = [
	{
		"doctype": "Role",
		"filters": [["name", "in", ("B2B Creator", "Ticket Manager", "Announcement Manager",
			# the approvers of the Customer / Supplier / Item approval -- see master_approval.py
			"Customer Approver", "Supplier Approver", "Item Master Approver")]],
	},
	{
		"doctype": "Custom DocPerm",
		"filters": [["parent", "in", ("Driver",)]],
	},
	{
		"doctype": "DocType",
		"filters": [["name", "in", ("Company Print Format",)]],
	},
	{
		"doctype": "Custom Field",
		"filters": [
			[
				"name",
				"in",
				(
					"Company-custom_print_formats",
					"Company-custom_delivery_note_print_format",
					"Company-custom_max_payment_write_off",
					# created by sf_trading.sdbnb.setup_sdbnb on migrate; listed
					# here so an export keeps them in step
					"Company-custom_enable_stock_delivered_but_not_billed",
					"Company-custom_stock_delivered_but_not_billed",
					"Company-custom_disable_sdbnb_in_sr",
					"Company-custom_enable_stock_billed_but_not_delivered",
					"Company-custom_stock_billed_but_not_delivered",
					"Sales Invoice Item-custom_sbnd_valuation_rate",
					"Customer Credit Limit-custom_credit_days",
					"Customer-custom_commercial_registration_number",
					"Sales Invoice-inter_company_branch",
					"Sales Invoice-custom_sale_type",
					"Purchase Receipt-custom_billing_approval_status",
					"Purchase Taxes and Charges Template-custom_for_foreign_currency",
					"Purchase Taxes and Charges Template-custom_for_no_tax_id_supplier",
					"Customer-custom_vat_registration_number",
					"Sales Invoice-custom_credit_limit",
					"Sales Invoice-custom_contact_expairy_date",
					"Sales Invoice-custom_vat__expairy_date",
					"Sales Invoice-custom_payment_mode",
					"Branch-custom_letter_head",
					"Material Request-custom_priority",
					"Customer-custom_company",
					"Customer-custom_branch_access",
					"Sales Invoice-custom_driver",
					"Driver-custom_branch",
					"Driver-custom_payment_days",
					"Driver-custom_cash_limit",
					"Driver-custom_branch_cash_limits",
					"Customer-custom_cash_limits_section",
					"Customer-custom_payment_days",
					"Customer-custom_cash_limit",
					"Customer-custom_branch_cash_limits",
					"Sales Person-custom_cash_limits_section",
					"Sales Person-custom_payment_days",
					"Sales Person-custom_cash_limit",
					"Sales Person-custom_branch_cash_limits",
					"Sales Invoice-custom_sales_person",
					"Quotation-branch",
					"Quotation-cost_center",
					"Quotation-project",
					"Quotation-set_warehouse",
					"Quotation Item-branch",
					"Quotation Item-cost_center",
					"Quotation Item-project",
					"Sales Invoice Item-custom_quotation",
					"Sales Invoice Item-custom_price_list",
					"Quotation Item-custom_price_list",
					"Sales Order Item-custom_price_list",
					"Item Group-custom_min_margin_pct",
					"Price List-custom_enforce_min_price",
					"Item Price-custom_branches_section",
					"Item Price-custom_applicable_branches",
					"Material Request Item-custom_source_mr",
					"Activity Log-custom_impersonation_reason",
				)
			]
		]
	},
	{
		"doctype": "Report",
		"filters": [
				[
					"name",
					"in",
					(
						"DCR Report",
						"DCR Detailed",
						"DCR Detail",
						# GS Issue 4
						"Duplicate Customers",
					)
				]
		]
	},
	{
		"doctype": "Property Setter",
		"filters": [["name", "in", (
			# Customer / Supplier creation: what is required, and what the quick entry shows
			# (sf_trading/party_documents.py, public/js/party_quick_entry.js)
			"Supplier-supplier_primary_address-reqd",
			"Supplier-primary_address-reqd",
			"Supplier-custom_mobile_no-reqd",
			"Supplier-tax_id-reqd",
			"Supplier-tax_id-mandatory_depends_on",
			"Supplier-tax_id-allow_in_quick_entry",
			"Supplier-payment_terms-allow_in_quick_entry",
			"Customer-payment_terms-allow_in_quick_entry",
			# Payment Terms are asked of a credit customer only (party_documents); this undoes a
			# hand-made "required for every customer" on production
			"Customer-payment_terms-reqd",
			"Supplier-supplier_group-allow_in_quick_entry",
			"Customer-custom_vat_registration_number-allow_in_quick_entry",
			"Customer-custom_commercial_registration_number-allow_in_quick_entry",
			"Customer-custom_commercial_registration_number-mandatory_depends_on",
			"Sales Invoice Item-barcode-in_list_view",
			# CODE-128 is the type every Item's own-code barcode carries (sf_trading/item_barcode.py); ERPNext's list lacks it and blanks
			# any type that is not in the list on every save
			"Item Barcode-barcode_type-options",
			"Sales Taxes and Charges-cost_center-ignore_user_permissions",
			"Sales Invoice Item-cost_center-ignore_user_permissions",
			"Purchase Invoice-is_paid-hidden",
			"Purchase Invoice-is_return-hidden",
			"Purchase Invoice-return_against-hidden",
			"Purchase Invoice-update_outstanding_for_self-hidden",
			"Purchase Invoice-update_billed_amount_in_purchase_order-hidden",
			"Purchase Invoice-update_billed_amount_in_purchase_receipt-hidden",
			"Purchase Invoice-apply_tds-hidden",
			"Purchase Invoice-amended_from-hidden",
			# default the two advance switches on, so the PO -> PI mapper fills the
			# advance table at creation; set_advance_allocation turns them back off on an
			# invoice that names no order
			"Purchase Invoice-allocate_advances_automatically-default",
			"Purchase Invoice-only_include_allocated_payments-default",
			"Purchase Invoice-set_posting_time-hidden",
			"Purchase Invoice-sec_warehouse-collapsible",
			"Purchase Invoice-total_net_weight-hidden",
			"Purchase Invoice-base_total-hidden",
			"Purchase Invoice-base_net_total-hidden",
			"Purchase Invoice-net_total-hidden",
			"Purchase Invoice-tax_category-hidden",
			"Purchase Invoice-shipping_rule-hidden",
			"Purchase Invoice-incoterm-hidden",
			"Purchase Invoice-named_place-hidden",
			"Purchase Invoice-base_taxes_and_charges_added-hidden",
			"Purchase Invoice-base_taxes_and_charges_deducted-hidden",
			"Purchase Invoice-taxes_and_charges_added-hidden",
			"Purchase Invoice-taxes_and_charges_deducted-hidden",
			"Purchase Invoice-total_taxes_and_charges-hidden",
			"Purchase Invoice-base_grand_total-hidden",
			"Purchase Invoice-base_rounding_adjustment-hidden",
			"Purchase Invoice-base_rounded_total-hidden",
			"Purchase Invoice-base_in_words-hidden",
			"Purchase Invoice-disable_rounded_total-hidden",
			"Purchase Invoice-apply_discount_on-hidden",
			"Purchase Invoice-base_discount_amount-hidden",
			"Purchase Invoice-additional_discount_percentage-hidden",
			"Purchase Invoice-discount_amount-hidden",
			"Purchase Invoice-tax_withheld_vouchers-hidden",
			"Purchase Invoice-other_charges_calculation-hidden",
			"Purchase Invoice-pricing_rules-hidden",
			"Purchase Invoice-main-field_order",
			"Purchase Invoice-section_break_49-collapsible",
			"Purchase Invoice-in_words-hidden",
			"Purchase Invoice-rounded_total-hidden",
			"Purchase Invoice-rounding_adjustment-hidden",
			"Purchase Invoice-use_company_roundoff_cost_center-hidden",
			"Quotation-pricing_rules-hidden",
			"Quotation-packed_items-hidden",
			"Quotation-other_charges_calculation-hidden",
			"Quotation-referral_sales_partner-hidden",
			"Quotation-discount_amount-hidden",
			"Quotation-additional_discount_percentage-hidden",
			"Quotation-coupon_code-hidden",
			"Quotation-base_discount_amount-hidden",
			"Quotation-apply_discount_on-hidden",
			"Quotation-disable_rounded_total-hidden",
			"Quotation-rounding_adjustment-hidden",
			"Quotation-grand_total-hidden",
			"Quotation-base_in_words-hidden",
			"Quotation-base_rounding_adjustment-hidden",
			"Quotation-total_taxes_and_charges-hidden",
			"Quotation-taxes-hidden",
			"Quotation-named_place-hidden",
			"Quotation-incoterm-hidden",
			"Quotation-shipping_rule-hidden",
			"Quotation-taxes_and_charges-hidden",
			"Quotation-tax_category-hidden",
			"Quotation-net_total-hidden",
			"Quotation-total-hidden",
			"Quotation-base_net_total-hidden",
			"Quotation-total_net_weight-hidden",
			"Quotation-last_scanned_warehouse-hidden",
			"Quotation-order_type-hidden",
			"Quotation-main-hidden",
			"Quotation-main-field_order",
			"Quotation-scan_barcode-hidden",
			"Quotation-in_words-print_hide",
			"Quotation-in_words-hidden",
			"Quotation-disable_rounded_total-default",
			"Quotation-rounded_total-print_hide",
			"Quotation-rounded_total-hidden",
			"Quotation-base_rounded_total-print_hide",
			"Quotation-base_rounded_total-hidden",
			"Purchase Receipt-grand_total-hidden",
			"Purchase Receipt-main-field_order",
			"Purchase Receipt-disable_rounded_total-hidden",
			"Purchase Receipt-in_words-hidden",
			"Purchase Receipt-rounded_total-hidden",
			"Purchase Receipt-rounding_adjustment-hidden",
			"Purchase Receipt-base_in_words-hidden",
			"Purchase Receipt-base_rounded_total-hidden",
			"Purchase Receipt-base_rounding_adjustment-hidden",
			"Purchase Receipt-total_taxes_and_charges-hidden",
			"Purchase Receipt-taxes_and_charges_deducted-hidden",
			"Purchase Receipt-taxes_and_charges_added-hidden",
			"Purchase Receipt-base_taxes_and_charges_deducted-hidden",
			"Purchase Receipt-base_taxes_and_charges_added-hidden",
			"Purchase Receipt-base_grand_total-hidden",
			"Purchase Receipt-taxes-hidden",
			"Purchase Receipt-named_place-hidden",
			"Purchase Receipt-incoterm-hidden",
			"Purchase Receipt-shipping_rule-hidden",
			"Purchase Receipt-taxes_and_charges-hidden",
			"Purchase Receipt-tax_category-hidden",
			"Purchase Receipt-net_total-hidden",
			"Purchase Receipt-total-hidden",
			"Purchase Receipt-total_net_weight-hidden",
			"Purchase Receipt-sec_warehouse-collapsible",
			"Purchase Receipt-return_against-hidden",
			"Purchase Receipt-is_return-hidden",
			"Purchase Receipt-apply_putaway_rule-hidden",
			"Purchase Receipt-set_posting_time-hidden",
			"Purchase Receipt-posting_time-hidden",
			"Sales Order-taxes-hidden",
			"Sales Order-pricing_rules-hidden",
			"Sales Order-packed_items-hidden",
			"Sales Order-other_charges_calculation-hidden",
			"Sales Order-discount_amount-hidden",
			"Sales Order-additional_discount_percentage-hidden",
			"Sales Order-coupon_code-hidden",
			"Sales Order-base_discount_amount-hidden",
			"Sales Order-apply_discount_on-hidden",
			"Sales Order-disable_rounded_total-hidden",
			"Sales Order-advance_paid-hidden",
			"Sales Order-in_words-hidden",
			"Sales Order-rounded_total-hidden",
			"Sales Order-rounding_adjustment-hidden",
			"Sales Order-grand_total-hidden",
			"Sales Order-base_in_words-hidden",
			"Sales Order-base_rounded_total-hidden",
			"Sales Order-total_taxes_and_charges-hidden",
			"Sales Order-named_place-hidden",
			"Sales Order-incoterm-hidden",
			"Sales Order-shipping_rule-hidden",
			"Sales Order-taxes_and_charges-hidden",
			"Sales Order-tax_category-hidden",
			"Sales Order-net_total-hidden",
			"Sales Order-total-hidden",
			"Sales Order-base_net_total-hidden",
			"Sales Order-total_net_weight-hidden",
			"Sales Order-main-field_order",
			"Sales Order-sec_warehouse-collapsible",
			"Sales Order-po_date-hidden",
			"Sales Order-po_no-hidden",
			"Stock Entry-from_warehouse-ignore_user_permissions",
			"Stock Entry-to_warehouse-ignore_user_permissions",
			"Stock Entry Detail-s_warehouse-ignore_user_permissions",
			"Stock Entry Detail-t_warehouse-ignore_user_permissions",
			# core hides its own Asset Location field on these two rows behind
			# "depends_on": "is_fixed_asset" -- widened so a custom_track_as_asset row can
			# offer it too, at the point of purchase, without touching the core doctype.
			# See sf_trading/asset_tracking_item.py._resolve_location.
			"Purchase Receipt Item-asset_location-depends_on",
			"Purchase Invoice Item-asset_location-depends_on",
		)]]
	},
	{
		"doctype": "Custom Field",
		"filters": [["name", "in", (
			"Payment Entry-custom_zatca_payment_means_code",
			# created by sf_trading.pdc_transfer / payment_entry_reference_date on migrate;
			# listed here so an export keeps them in step
			"Payment Entry-custom_pdc_source_payment_entry",
			"Payment Entry Reference-custom_reference_date",
			"Sales Order-custom_payment_mode",
			"Sales Order-custom_sales_person",
			"Sales Order-custom_driver",
			"Sales Invoice-custom_planned_refund_section",
			"Sales Invoice-custom_planned_payments",
			"Journal Entry-custom_loyalty_sales_invoice",
			"Journal Entry Account-custom_loyalty_sales_invoice",
			"Payment Entry-custom_payment_advice",
			"Supplier-custom_disable_auto_payment",
			# GS Issue 17: allow_on_submit -- set by cancel_sales_order_with_remark / the
			# before_cancel form event, both of which act on an already-submitted order
			"Sales Order-custom_cancellation_remark",
			# GS Issue 11: cached phone, kept in sync from the linked Contact
			"Customer-custom_mobile_no",
			"Supplier-custom_mobile_no",
			# GS Issue 25: allow_on_submit -- set by reject_pdc on an already-submitted cheque
			"Payment Entry-custom_pdc_rejection_date",
			# credit customer approval + document expiry
			"Customer-custom_credit_approval_section",
			"Customer-custom_approval_status",
			# Customer / Supplier / Item approval: status, who approved, and the admin bypass -- see master_approval.py
			"Customer-custom_approval_column",
			"Customer-custom_approved_by",
			"Customer-custom_approved_on",
			"Customer-custom_approval_bypassed",
			"Customer-custom_approval_bypassed_by",
			"Customer-custom_approval_bypassed_on",
			"Customer-custom_approval_bypass_reason",
			"Supplier-custom_approval_section",
			"Supplier-custom_approval_status",
			"Supplier-custom_approval_column",
			"Supplier-custom_approved_by",
			"Supplier-custom_approved_on",
			"Supplier-custom_approval_bypassed",
			"Supplier-custom_approval_bypassed_by",
			"Supplier-custom_approval_bypassed_on",
			"Supplier-custom_approval_bypass_reason",
			"Item-custom_approval_section",
			"Item-custom_approval_status",
			"Item-custom_approval_column",
			"Item-custom_approved_by",
			"Item-custom_approved_on",
			"Item-custom_approval_bypassed",
			"Item-custom_approval_bypassed_by",
			"Item-custom_approval_bypassed_on",
			"Item-custom_approval_bypass_reason",
			"Customer-custom_supporting_documents",
			"Supplier-custom_supporting_documents",
			# their own full-width section at the end of each master's Settings tab
			"Customer-custom_supporting_documents_section",
			"Supplier-custom_supporting_documents_section",
			# standardized Sales Return reason picker -- appends into the pre-existing
			# Sales Invoice-custom_return_reason field, fixtured by whichever app already owns
			# that field (not this one); see sf_trading/sales_return_reason.py
			"Sales Invoice-custom_return_reason_template",
			# the same picker on a debit note, and the free-text field it appends into (Purchase
			# Invoice has none of its own)
			"Purchase Invoice-custom_return_reason",
			"Purchase Invoice-custom_return_reason_template",
			# company-wise Salesperson
			"Sales Person-custom_companies",
			# Branch / Outdoor, which the Sales Performance reports split on; see
			# sf_trading/sales_target.py
			"Sales Person-custom_sales_person_type",
			# sf_trading.asset_tracking_item: shadow Asset (Movement tracking only, no
			# capitalization) for an item that is bought expensed, not fixed-asset
			"Item-custom_track_as_asset",
			"Asset-custom_source_purchase_invoice",
			"Asset-custom_source_row",
			# row-level mirror of the Item flag, fetched the same way core mirrors
			# Item.is_fixed_asset onto these same two rows -- lets the Property Setter
			# above reveal core's own Asset Location field for a tracked row too
			"Purchase Receipt Item-custom_track_as_asset",
			"Purchase Invoice Item-custom_track_as_asset",
		)]],
	},
	{
		"doctype": "Notification",
		"filters": [["name", "in", ("Supplier Payment Due Reminder",)]],
	},
	{
		"doctype": "Workspace",
		"filters": [["name", "in", ("Supplier Payments", "Open Items", "Credit & Collections")]],
	},
	{
		"doctype": "Number Card",
		"filters": [["name", "in", (
			"Pending Delivery Value",
			"Unbilled Deliveries Value",
			"Unbilled Receipts Value",
			"Pending Receipt Value",
		)]],
	},
]

# ─── Approval applicability (permission_manager extension point) ─────────────
# A PM Workflow is defined per doctype, and permission_manager is installed on every PM client, so
# it asks rather than assumes: this resolver tells it that a Sales Invoice workflow here governs
# returns above the configured threshold and nothing else. Without it, such a workflow would take
# the Submit button off every ordinary sale. Policy lives here, the machinery stays there.
pm_workflow_applicability = [
	"sf_trading.sales_return.workflow_applicability",
	# Sales Order / Quotation cancellation + Stock Reconciliation approval -- see sf_trading/approval_routing.py
	"sf_trading.approval_routing.workflow_applicability",
	# a system-generated journal (asset depreciation and the like) is governed by no workflow
	"sf_trading.system_entries.workflow_applicability",
	# Customer / Supplier / Item approval: only the masters the approval covers -- see master_approval.py
	"sf_trading.master_approval.workflow_applicability",
]


# ─── Scheduler ────────────────────────────────────────────────────────────────
scheduler_events = {
	"daily": [
		"sf_trading.api.overdue_notifications.notify_overdue_invoices",
		"sf_trading.document_expiry.check_expiring_documents",
		# a ticket that stayed Resolved for the wait closes by itself -- see ticket.py
		"sf_trading.ticket.close_resolved_tickets",
		# only today's "shown the motivation card" row is ever read; older ones are dropped -- see motivation.py
		"sf_trading.motivation.prune_seen",
	],
	# every tick: each Payment Automation Settings row names its own weekday + time,
	# and the engine fences itself with last_execution
	"all": [
		"sf_trading.api.payment_automation.run_due_automations",
		# a scheduled announcement goes out when its time comes; a published one leaves the feed at "Hide After"
		"sf_trading.announcements.run_schedule",
	],
}
