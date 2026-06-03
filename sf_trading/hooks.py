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
app_include_js = [
	"/assets/sf_trading/js/warehouse_stock_popup.js",
	"/assets/sf_trading/js/last_selling_rate.js",
	"/assets/sf_trading/js/quick_entry.js",
	"/assets/sf_trading/js/create_customer.js",
	"/assets/sf_trading/js/sales_invoice_barcode.js",
	"/assets/sf_trading/js/sales_invoice_inter_company.js",
	"/assets/sf_trading/js/sales_invoice_pos_total_popup.js",
    "/assets/sf_trading/js/work_flow_rejection.js",
    "/assets/sf_trading/js/workflow_approval_shortcut.js",
]

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
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
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

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Sales Invoice": {
		"before_validate": "sf_trading.sales_invoice_override.before_validate",
		"on_submit": "sf_trading.inter_company.sales_invoice_on_submit",
	},
	"Purchase Invoice": {
		"before_validate": "sf_trading.inter_company.purchase_invoice_before_validate",
		"validate": "sf_trading.overrides.purchase_invoice.validate",
		"on_save": "sf_trading.overrides.purchase_invoice.on_save",
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
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "sf_trading.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "sf_trading.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["sf_trading.utils.before_request"]
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
		"doctype": "Custom Field",
		"filters": [
			[
				"name",
				"in",
				(
					"Customer-custom_commercial_registration_number",
					"Sales Invoice-inter_company_branch",
					"Sales Invoice-custom_sale_type",
					"Purchase Receipt-custom_billing_approval_status",
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
		"filters": [["name", "=", "Sales Invoice Item-barcode-in_list_view"]]
	}
]