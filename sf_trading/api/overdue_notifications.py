# apps/sf_trading/sf_trading/api/overdue_notifications.py
"""Overdue-invoice alerts — daily chime + toast for AR/AP staff.

Fires the ``sf_invoice_overdue_alert`` realtime event, which
``sf_trading/public/js/sf_overdue_alert.js`` turns into a two-tone chime, a desk
toast and (where the browser permits) a desktop notification that opens the
"Invoice Due & Overdue Report". Same delivery pattern as permission_manager's
approval chime, but with its own event so wording and routing are correct.

Recipients are resolved by role, each user notified once, scoped to what they own:
sales-only, purchase-only, or both.

No SQL is built here — every read goes through frappe.get_all with dict filters.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

EVENT = "sf_invoice_overdue_alert"
REPORT = "Invoice Due & Overdue Report"

# role -> what that role should be told about
ROLE_SCOPE = {
    "Accounts Manager": "both",
    "Accountant": "both",
    "Finance Manager": "both",
    "HO Accounts": "both",
    "Branch Head": "both",
    "Purchase Manager": "purchase",
    "Purchase Assistant": "purchase",
    "Sales Manager": "sales",
}

SKIP_USERS = ("Administrator", "Guest")


def _overdue_summary(company=None, as_on=None):
    """Count + outstanding total of overdue Sales / Purchase Invoices.

    ``outstanding_amount`` is already in company currency (party_account_currency),
    so it is summed as-is — never multiplied by conversion_rate.
    """
    as_on = getdate(as_on or nowdate())
    summary = {}

    for scope, doctype in (("sales", "Sales Invoice"), ("purchase", "Purchase Invoice")):
        filters = {
            "docstatus": 1,
            "outstanding_amount": [">", 0],
            "due_date": ["<", as_on],
        }
        if company:
            filters["company"] = company

        rows = frappe.get_all(
            doctype,
            filters=filters,
            fields=["name", "outstanding_amount"],
            ignore_permissions=True,
        )
        summary[scope] = {
            "count": len(rows),
            "outstanding": flt(sum(flt(r.outstanding_amount) for r in rows), 3),
        }

    return summary


def _users_by_scope():
    """Map user -> scope ("both" / "sales" / "purchase") from enabled role holders."""
    scopes = {}
    rank = {"sales": 1, "purchase": 1, "both": 2}

    for role, scope in ROLE_SCOPE.items():
        holders = frappe.get_all(
            "Has Role",
            filters={"role": role, "parenttype": "User"},
            pluck="parent",
        )
        if not holders:
            continue

        enabled = frappe.get_all(
            "User",
            filters={"name": ["in", holders], "enabled": 1, "user_type": "System User"},
            pluck="name",
        )
        for user in enabled:
            if user in SKIP_USERS:
                continue
            current = scopes.get(user)
            # a user holding both a sales and a purchase role sees both
            if current and current != scope:
                scopes[user] = "both"
            elif not current or rank[scope] > rank[current]:
                scopes[user] = scope

    return scopes


def _payload(summary, scope):
    """Build the realtime payload for one scope, or None when nothing is overdue."""
    parts = []
    count = 0
    outstanding = 0.0

    for key, label in (("sales", _("sales")), ("purchase", _("purchase"))):
        if scope not in ("both", key):
            continue
        bucket = summary.get(key) or {}
        if not bucket.get("count"):
            continue
        count += bucket["count"]
        outstanding += flt(bucket["outstanding"])
        parts.append("%s %s" % (bucket["count"], label))

    if not count:
        return None

    currency = frappe.db.get_default("currency") or ""
    message = _("%(count)s overdue (%(detail)s) — %(amount)s %(currency)s outstanding") % {
        "count": count,
        "detail": " + ".join(parts),
        "amount": frappe.utils.fmt_money(outstanding, precision=3, currency=currency),
        "currency": "",
    }

    return {
        "title": _("Overdue invoices"),
        "message": message.replace("  ", " ").strip(),
        "count": count,
        "outstanding": flt(outstanding, 3),
        "currency": currency,
        "report": REPORT,
    }


def _push(user, payload):
    try:
        frappe.publish_realtime(
            event=EVENT,
            message=payload,
            user=user,
            after_commit=True,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "sf_trading: overdue alert push failed")


def notify_overdue_invoices():
    """Daily scheduler entry — alert every AR/AP role holder about overdue invoices."""
    summary = _overdue_summary()
    if not (summary["sales"]["count"] or summary["purchase"]["count"]):
        return

    for user, scope in _users_by_scope().items():
        payload = _payload(summary, scope)
        if payload:
            _push(user, payload)


@frappe.whitelist()
def check_overdue_now(company=None):
    """Re-run the check for the calling user only (the report's "Notify Me Now" button).

    Read access to either invoice type is enough — the returned summary is aggregate,
    and the report itself is role-gated.
    """
    if not (
        frappe.has_permission("Sales Invoice", "read")
        or frappe.has_permission("Purchase Invoice", "read")
    ):
        frappe.throw(_("Not permitted to read invoices"), frappe.PermissionError)

    summary = _overdue_summary(company=company)
    scope = _users_by_scope().get(frappe.session.user, "both")
    payload = _payload(summary, scope)

    if payload:
        _push(frappe.session.user, payload)
        return payload

    return {
        "count": 0,
        "outstanding": 0.0,
        "currency": frappe.db.get_default("currency") or "",
    }
