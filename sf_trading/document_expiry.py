# sf_trading/document_expiry.py
"""Expiry reminders for Customer / Supplier supporting documents.

Each Customer Supporting Document row (see fixtures/custom_field.json's
custom_supporting_documents on Customer and Supplier) carries its own expiry date. This
walks both parties daily, in a window either side of that date -- the row's own Days Ahead
and Grace Days, else SF Trading Settings' Remind Before / Keep Reminding After -- for rows
that validate their expiry, and reminds once a day per row via the
same two guaranteed channels api/overdue_notifications.py uses -- the desk bell always,
email only when the site actually has an outgoing account. A row already notified today is
skipped, so re-running the scheduler (or the report's own "check now") never double-sends.
"""

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import add_days, cint, date_diff, escape_html, formatdate, get_url, getdate, nowdate

SETTINGS = "SF Trading Settings"
NOTIFY_ROLES = ("Credit Approval Officer", "Accounts Manager")
REPORT = "Document Expiry Report"
REPORT_ROUTE = "/app/query-report/Document%20Expiry%20Report"
SKIP_USERS = ("Administrator", "Guest")

PARTY_NAME_FIELD = {"Customer": "customer_name", "Supplier": "supplier_name"}


def _setting(fieldname, default=None):
	try:
		return frappe.get_cached_doc(SETTINGS).get(fieldname)
	except frappe.DoesNotExistError:
		return default


def _before_days() -> int:
	return cint(_setting("document_expiry_before_days")) or 30


def _after_days() -> int:
	return cint(_setting("document_expiry_after_days")) or 7


#: how far either side of today a row's own Days Ahead / Grace Days may reach
_WIDEST_WINDOW = 366


def _due_rows(parenttype: str) -> list:
	"""Every supporting-document row for this party type due a reminder today.

	A row that validates its expiry is reminded from its own Days Ahead before expiry to its own
	Grace Days after (each falling back to SF Trading Settings when 0). A row that does not
	validate its expiry is never reminded.
	"""
	today = getdate(nowdate())
	rows = frappe.get_all(
		"Customer Supporting Document",
		filters={
			"parenttype": parenttype,
			"validate_expiry": 1,
			"expiry_date": ["between", [add_days(today, -_WIDEST_WINDOW), add_days(today, _WIDEST_WINDOW)]],
		},
		fields=["name", "parent", "document_type", "document_number", "expiry_date", "last_notified_on",
			"days_ahead", "grace_days"],
	)
	rows = [
		r for r in rows
		if (not r.last_notified_on or getdate(r.last_notified_on) != today)
		and add_days(today, -(cint(r.grace_days) or _after_days())) <= getdate(r.expiry_date)
		<= add_days(today, cint(r.days_ahead) or _before_days())
	]

	party_field = PARTY_NAME_FIELD[parenttype]
	parents = list({r.parent for r in rows})
	names = {}
	if parents:
		for p in frappe.get_all(parenttype, filters={"name": ["in", parents]}, fields=["name", party_field]):
			names[p.name] = p.get(party_field) or p.name

	for r in rows:
		r["parenttype"] = parenttype
		r["days_to_expiry"] = date_diff(r.expiry_date, today)
		r["party_name"] = names.get(r.parent, r.parent)
	return rows


def _recipients() -> set:
	users = set()
	for role in NOTIFY_ROLES:
		holders = frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")
		if not holders:
			continue
		enabled = frappe.get_all(
			"User",
			filters={"name": ["in", holders], "enabled": 1, "user_type": "System User"},
			pluck="name",
		)
		users.update(u for u in enabled if u not in SKIP_USERS)
	return users


def _digest_html(expiring: list, expired: list) -> str:
	html = ["<p>", escape_html(_("Supporting documents needing attention:")), "</p>"]
	for label, rows in ((_("Expiring soon"), expiring), (_("Already expired"), expired)):
		if not rows:
			continue
		html += ["<p><b>", escape_html(label), "</b></p>",
		         '<table border="1" cellpadding="6" cellspacing="0" '
		         'style="border-collapse:collapse;font-size:13px">',
		         '<tr style="background:#f4f5f6">',
		         "<th>", escape_html(_("Party")), "</th>",
		         "<th>", escape_html(_("Document")), "</th>",
		         "<th>", escape_html(_("Number")), "</th>",
		         "<th>", escape_html(_("Expiry Date")), "</th>",
		         "<th>", escape_html(_("Days")), "</th>",
		         "</tr>"]
		for r in rows:
			html += ["<tr>",
			         "<td>", escape_html(r.party_name), "</td>",
			         "<td>", escape_html(r.document_type), "</td>",
			         "<td>", escape_html(r.document_number or ""), "</td>",
			         "<td>", escape_html(formatdate(r.expiry_date)), "</td>",
			         '<td align="right">', escape_html(str(r.days_to_expiry)), "</td>",
			         "</tr>"]
		html.append("</table>")
	html += ['<p><a href="', get_url(REPORT_ROUTE), '">',
	         escape_html(_("Open the Document Expiry Report")), "</a></p>"]
	return "".join(html)


def _send_email(user: str, expiring: list, expired: list) -> bool:
	recipient = frappe.db.get_value("User", user, "email") or user
	if not recipient:
		return False
	subject = _("Document expiry: %(soon)s expiring soon, %(gone)s already expired") % {
		"soon": len(expiring), "gone": len(expired),
	}
	try:
		frappe.sendmail(
			recipients=[recipient], subject=subject, message=_digest_html(expiring, expired),
			reference_doctype="Report", reference_name=REPORT,
		)
		return True
	except frappe.OutgoingEmailError:
		return False
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sf_trading: document expiry email failed")
		return False


def check_expiring_documents():
	"""Daily scheduler entry -- remind about supporting documents nearing or past expiry."""
	rows = _due_rows("Customer") + _due_rows("Supplier")
	if not rows:
		return

	today = getdate(nowdate())
	for r in rows:
		frappe.db.set_value(
			"Customer Supporting Document", r.name, "last_notified_on", today, update_modified=False
		)

	recipients = _recipients()
	if not recipients:
		return

	expiring = [r for r in rows if r.days_to_expiry >= 0]
	expired = [r for r in rows if r.days_to_expiry < 0]
	subject = _("%(soon)s document(s) expiring soon, %(gone)s already expired") % {
		"soon": len(expiring), "gone": len(expired),
	}
	email_allowed = bool(frappe.db.exists("Email Account", {"enable_outgoing": 1}))

	for user in recipients:
		try:
			enqueue_create_notification(
				[user],
				{
					"type": "Alert",
					"subject": subject,
					"document_type": "Report",
					"document_name": REPORT,
					"link": REPORT_ROUTE,
					"from_user": frappe.session.user or "Administrator",
				},
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "sf_trading: document expiry notification failed")

		if email_allowed:
			_send_email(user, expiring, expired)
