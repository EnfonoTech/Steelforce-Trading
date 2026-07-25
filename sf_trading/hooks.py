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
app_include_js = [
	# ── Multi-doctype: selling (Sales Invoice, Sales Order, Quotation, Delivery Note) ──
	f"/assets/sf_trading/js/last_selling_rate.js?{_v}",
	f"/assets/sf_trading/js/quick_entry.js?{_v}",
	f"/assets/sf_trading/js/create_customer.js?{_v}",
	f"/assets/sf_trading/js/selling_price_realtime.js?{_v}",
	# ── Multi-doctype: purchasing (Purchase Invoice, Purchase Order, Purchase Receipt, …) ──
	f"/assets/sf_trading/js/last_purchase_rate.js?{_v}",
	f"/assets/sf_trading/js/create_supplier.js?{_v}",
	# ── Multi-doctype: cross-selling + purchasing ──
	f"/assets/sf_trading/js/return_qty_autofix.js?{_v}",
	f"/assets/sf_trading/js/accounting_dimension_sync.js?{_v}",
	f"/assets/sf_trading/js/sales_invoice_item_search.js?{_v}",
	f"/assets/sf_trading/js/purchase_invoice_cost_center.js?{_v}",
	f"/assets/sf_trading/js/stock_availability.js?{_v}",
	# ── Global (all pages / all item_code fields / print patches) ──
	f"/assets/sf_trading/js/workflow_approval_shortcut.js?{_v}",
	f"/assets/sf_trading/js/item_search_cache_buster.js?{_v}",
	f"/assets/sf_trading/js/company_print_format.js?{_v}",
	# ── Overdue-invoice alert: chime + toast + desktop notification ──
	f"/assets/sf_trading/js/sf_overdue_alert.js?{_v}",
]

# doctype_js: loaded only when that specific doctype form opens
doctype_js = {
	"Sales Invoice":    "public/js/sales_invoice.js",
	"Stock Entry":      "public/js/stock_entry.js",
	"Material Request": "public/js/material_request.js",
	"Customer":         "public/js/customer_company.js",
	"Quotation":        "public/js/quotation.js",
	"Supplier Quotation": "public/js/purchase_tax_template.js",
	"Purchase Order":     "public/js/purchase_tax_template.js",
	"Purchase Receipt":   "public/js/purchase_tax_template.js",
	"Purchase Invoice":   "public/js/purchase_tax_template.js",
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
	"Purchase Invoice": "public/js/purchase_invoice_list.js",
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
# jinja = {
# 	"methods": "sf_trading.utils.jinja_methods",
# 	"filters": "sf_trading.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "sf_trading.install.before_install"
# after_install = "sf_trading.install.after_install"

after_migrate = ["sf_trading.inter_branch.ensure_branch_accounting_dimension"]

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
}
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	"Sales Invoice": "sf_trading.overrides.sales_invoice_class.CustomSalesInvoice"
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

# Picks the purchase tax template matching the document currency (default
# template for company-currency docs, "Import VAT 0%" otherwise).
_PTT_HOOK = "sf_trading.purchase_tax_template.set_template_by_currency"

doc_events = {
	"Payment Entry": {
		"validate": "sf_trading.api.payment_advice_hooks.validate_payment_entry_advice",
		"on_submit": "sf_trading.api.payment_advice_hooks.on_payment_entry_submit",
		"on_cancel": "sf_trading.api.payment_advice_hooks.on_payment_entry_cancel",
	},
	"Customer": {
		"validate": [
			"sf_trading.api.customer_override.validate",
			"sf_trading.customer_permission.validate_credit_branch_access",
			"sf_trading.party_accounts.apply_title_case",
		],
		"before_save": [
			"sf_trading.customer_permission.auto_add_branch_on_credit_limit",
			"sf_trading.party_accounts.create_customer_receivable_account",
		],
	},
	"Supplier": {
		"validate": "sf_trading.party_accounts.apply_title_case",
		"before_save": "sf_trading.party_accounts.create_supplier_payable_account",
	},
	"Sales Invoice": {
		"before_validate": [
			"sf_trading.api.sales_invoice_override.before_validate",
			_CC_HOOK,
			"sf_trading.branch_defaults.override_payment_accounts_from_branch",
		],
		"validate": [
			"sf_trading.api.sales_invoice_override.validate",
			"sf_trading.api.sales_invoice_override.validate_driver_payment",
			_BRANCH_HOOK,
			_LH_HOOK,
			_SP_HOOK,
		],
		"on_submit": [
			"sf_trading.inter_company.sales_invoice_on_submit",
			"sf_trading.api.quotation.update_quotation_status_from_invoice",
		],
		"on_cancel": "sf_trading.api.quotation.update_quotation_status_from_invoice",
	},
	"Sales Order": {
		"before_validate": _CC_HOOK,
		"validate": [_LH_HOOK, _SP_HOOK],
	},
	"Quotation": {
		"before_validate": _CC_HOOK,
		"validate": [_LH_HOOK, _SP_HOOK],
	},
	"Delivery Note": {
		"before_validate": _CC_HOOK,
		"validate": [_BRANCH_HOOK, _LH_HOOK, _SP_HOOK],
	},
	"Purchase Invoice": {
		"before_validate": [
			"sf_trading.inter_company.purchase_invoice_before_validate",
			_CC_HOOK,
			_PTT_HOOK,
		],
		"validate": [
			"sf_trading.overrides.purchase_invoice.validate",
			_BRANCH_HOOK,
			_LH_HOOK,
		],
		"on_save": "sf_trading.overrides.purchase_invoice.on_save",
		"on_submit": "sf_trading.api.purchase_return.auto_create_pr_return",
	},
	"Purchase Order": {
		"before_validate": [_CC_HOOK, _PTT_HOOK],
		"validate": _LH_HOOK,
	},
	"Purchase Receipt": {
		"before_validate": [_CC_HOOK, _PTT_HOOK],
		"validate": [_BRANCH_HOOK, _LH_HOOK],
	},
	"Supplier Quotation": {
		"before_validate": [_CC_HOOK, _PTT_HOOK],
		"validate": _LH_HOOK,
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
}
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
override_doctype_dashboards = {
	"Quotation": ["sf_trading.api.quotation.get_dashboard_data"]
}

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
before_request = ["sf_trading.api.item_search.redirect_item_query_before_request"]
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
		"filters": [["name", "in", ("B2B Creator",)]],
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
					"Customer Credit Limit-custom_credit_days",
					"Customer-custom_commercial_registration_number",
					"Sales Invoice-inter_company_branch",
					"Sales Invoice-custom_sale_type",
					"Purchase Receipt-custom_billing_approval_status",
					"Purchase Taxes and Charges Template-custom_for_foreign_currency",
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
					"Item Group-custom_min_margin_pct",
					"Price List-custom_enforce_min_price",
					"Material Request Item-custom_source_mr",
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
					)
				]
		]
	},
	{
		"doctype": "Property Setter",
		"filters": [["name", "in", (
			"Sales Invoice Item-barcode-in_list_view",
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
		)]]
	},
	{
		"doctype": "Custom Field",
		"filters": [["name", "in", (
			"Payment Entry-custom_zatca_payment_means_code",
			"Journal Entry-custom_loyalty_sales_invoice",
			"Payment Entry-custom_payment_advice",
			"Supplier-custom_disable_auto_payment",
		)]],
	},
	{
		"doctype": "Notification",
		"filters": [["name", "in", ("Supplier Payment Due Reminder",)]],
	},
]

# ─── Scheduler ────────────────────────────────────────────────────────────────
scheduler_events = {
	"daily": [
		"sf_trading.api.overdue_notifications.notify_overdue_invoices",
	],
	# every tick: each Payment Automation Settings row names its own weekday + time,
	# and the engine fences itself with last_execution
	"all": [
		"sf_trading.api.payment_automation.run_due_automations",
	],
}
