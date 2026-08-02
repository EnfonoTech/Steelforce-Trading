# apps/sf_trading/sf_trading/report/cash_flow_money_in_vs_money_out/cash_flow_money_in_vs_money_out.py
"""Cash Flow Money In vs Money Out.

    Money In        = every debit posted to a Cash or Bank account
    Money Out       = every credit posted to one
    Net Movement    = Money In - Money Out
    Running Balance = opening balance + every net movement up to that row

Read from the ledger, not from Payment Entries. Money reaches these accounts by several routes -
payment entries, journals, POS settlements that never create a payment entry at all - and over a
thousand payment entries on this site carry no mode of payment. Anything built by summing
payment documents would be short by whatever those moved; the ledger has all of it.

A transfer between two of the company's own accounts shows as money out of one and into the
other, and nets to nothing. That is deliberate: moving cash from the bank to the till is neither
income nor spending, and the closing balance must not move because of it. Tick "Exclude internal
transfers" to leave those vouchers out of the In and Out columns and see only money that crossed
the company boundary.

The closing balance on the last row equals the sum of the Cash and Bank account balances on the
To Date, so this report can be checked against the Trial Balance without interpretation.
"""

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, nowdate

PERIODS = {
    "Daily": "%Y-%m-%d",
    "Weekly": "%x-W%v",
    "Monthly": "%Y-%m",
    "Yearly": "%Y",
}


def execute(filters=None):
    filters = frappe._dict(filters or {})
    company = filters.company or frappe.defaults.get_user_default("Company")
    if not company:
        frappe.throw(_("Please choose a company."))

    to_date = getdate(filters.to_date or nowdate())
    from_date = getdate(filters.from_date or add_months(to_date, -6))
    if from_date > to_date:
        frappe.throw(_("From Date is after To Date."))

    accounts = _money_accounts(company, filters.get("account"))
    if not accounts:
        frappe.msgprint(_("No Cash or Bank accounts found for {0}.").format(company))
        return _columns(company), []

    opening = _opening(company, accounts, from_date)
    view = filters.get("view") or "Summary by Period"

    if view == "Transactions":
        return _transactions(company, accounts, from_date, to_date, filters, opening)
    if view.startswith("By "):
        return _grouped(company, accounts, from_date, to_date, filters, view)

    rows = _movements(company, accounts, from_date, to_date, filters)

    data, running = [], opening
    total_in = total_out = 0.0

    data.append({
        "period": _("Opening Balance"),
        "money_in": None, "money_out": None, "net": None,
        "running": opening, "is_opening": 1,
    })

    for r in rows:
        net = flt(flt(r.money_in) - flt(r.money_out), 3)
        running = flt(running + net, 3)
        total_in += flt(r.money_in)
        total_out += flt(r.money_out)
        data.append({
            "period": r.label,
            "money_in": flt(r.money_in, 3),
            "money_out": flt(r.money_out, 3),
            "net": net,
            "running": running,
        })

    data.append({
        "period": _("Total"),
        "money_in": flt(total_in, 3),
        "money_out": flt(total_out, 3),
        "net": flt(total_in - total_out, 3),
        "running": running,
        "is_total": 1,
    })

    summary = [
        {"label": _("Opening Balance"), "value": opening, "datatype": "Currency",
         "indicator": "Blue"},
        {"label": _("Money In"), "value": flt(total_in, 3), "datatype": "Currency",
         "indicator": "Green"},
        {"label": _("Money Out"), "value": flt(total_out, 3), "datatype": "Currency",
         "indicator": "Red"},
        {"label": _("Net Movement"), "value": flt(total_in - total_out, 3),
         "datatype": "Currency", "indicator": "Green" if total_in >= total_out else "Red"},
        {"label": _("Closing Balance"), "value": running, "datatype": "Currency",
         "indicator": "Blue" if running >= 0 else "Red"},
    ]

    return _columns(company), data, _message(accounts, filters), _chart(data), summary


# ── the money accounts ────────────────────────────────────────────────────────

def _money_accounts(company, account=None):
    if account:
        row = frappe.db.get_value(
            "Account", account, ["name", "company", "is_group"], as_dict=True
        )
        if not row or row.company != company or cint(row.is_group):
            frappe.throw(_("{0} is not a usable account for {1}.").format(account, company))
        return [row.name]
    return frappe.get_all(
        "Account",
        filters={"company": company, "is_group": 0, "account_type": ["in", ["Cash", "Bank"]]},
        pluck="name",
    )


def _opening(company, accounts, from_date):
    return flt(
        frappe.db.sql(
            """SELECT COALESCE(SUM(debit - credit), 0) FROM `tabGL Entry`
               WHERE company = %(company)s AND is_cancelled = 0
                 AND account IN %(accounts)s AND posting_date < %(from_date)s""",
            {"company": company, "accounts": tuple(accounts), "from_date": from_date},
        )[0][0],
        3,
    )


def _movements(company, accounts, from_date, to_date, filters):
    """Per-period totals. Every fragment added to the statement below is a fixed string chosen
    here; every value the user supplied travels as a bound parameter."""
    periodicity = filters.get("periodicity") or "Monthly"
    params = {
        "company": company,
        "accounts": tuple(accounts),
        "from_date": from_date,
        "to_date": to_date,
    }

    if periodicity == "Quarterly":
        label_select = "CONCAT(YEAR(gle.posting_date), '-Q', QUARTER(gle.posting_date))"
    else:
        params["fmt"] = PERIODS.get(periodicity, PERIODS["Monthly"])
        label_select = "DATE_FORMAT(gle.posting_date, %(fmt)s)"

    internal = ""
    if cint(filters.get("exclude_internal_transfers")):
        # A voucher touching two money accounts moved money between them. Dropping those
        # vouchers leaves only money that actually entered or left the business.
        internal = (
            " AND gle.voucher_no NOT IN ("
            "   SELECT x.voucher_no FROM `tabGL Entry` x"
            "   WHERE x.company = %(company)s AND x.is_cancelled = 0"
            "     AND x.account IN %(accounts)s"
            "     AND x.posting_date BETWEEN %(from_date)s AND %(to_date)s"
            "   GROUP BY x.voucher_no HAVING COUNT(DISTINCT x.account) > 1)"
        )

    mop = ""
    if filters.get("mode_of_payment"):
        vouchers = frappe.get_all(
            "Payment Entry",
            filters={"company": company, "docstatus": 1,
                     "mode_of_payment": filters.get("mode_of_payment")},
            pluck="name",
        )
        if not vouchers:
            return []
        params["vouchers"] = tuple(vouchers)
        mop = " AND gle.voucher_no IN %(vouchers)s"

    return frappe.db.sql(
        "SELECT " + label_select + " AS label,"
        "       MIN(gle.posting_date) AS starts,"
        "       ROUND(SUM(gle.debit), 3)  AS money_in,"
        "       ROUND(SUM(gle.credit), 3) AS money_out"
        " FROM `tabGL Entry` gle"
        " WHERE gle.company = %(company)s AND gle.is_cancelled = 0"
        "   AND gle.account IN %(accounts)s"
        "   AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s"
        + internal + mop +
        " GROUP BY label ORDER BY starts",
        params,
        as_dict=True,
    )


# ── presentation ──────────────────────────────────────────────────────────────

def _columns(company):
    currency = frappe.db.get_value("Company", company, "default_currency")
    return [
        {"label": _("Period"), "fieldname": "period", "fieldtype": "Data", "width": 160},
        {"label": _("Money In"), "fieldname": "money_in", "fieldtype": "Currency",
         "options": "currency", "width": 150},
        {"label": _("Money Out"), "fieldname": "money_out", "fieldtype": "Currency",
         "options": "currency", "width": 150},
        {"label": _("Net Movement"), "fieldname": "net", "fieldtype": "Currency",
         "options": "currency", "width": 150},
        {"label": _("Running Balance"), "fieldname": "running", "fieldtype": "Currency",
         "options": "currency", "width": 170},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data",
         "width": 1, "hidden": 1, "default": currency},
    ]


def _message(accounts, filters):
    bits = [
        _("<b>Money In</b> is every debit to a Cash or Bank account, <b>Money Out</b> every "
          "credit. Running Balance is the opening balance plus each net movement, so the last "
          "row equals those accounts' balance on the To Date."),
        _("Reading {0} account(s).").format(len(accounts)),
    ]
    if cint(filters.get("exclude_internal_transfers")):
        bits.append(_("Transfers between your own cash and bank accounts are excluded."))
    else:
        bits.append(_("Transfers between your own accounts appear on both sides and net to nil."))
    return ("<div style='padding:8px 10px;border-left:3px solid #1f6f54;background:#f4f9f7'>"
            + "<br>".join(bits) + "</div>")


def _chart(data):
    points = [d for d in data if not d.get("is_opening") and not d.get("is_total")]
    if not points:
        return None
    return {
        "data": {
            "labels": [d["period"] for d in points],
            "datasets": [
                {"name": _("Money In"), "values": [flt(d["money_in"]) for d in points]},
                {"name": _("Money Out"), "values": [flt(d["money_out"]) for d in points]},
                {"name": _("Running Balance"), "values": [flt(d["running"]) for d in points]},
            ],
        },
        "type": "axis-mixed",
        "colors": ["#2e7d32", "#c62828", "#1565c0"],
        "fieldtype": "Currency",
    }


# ── voucher-level detail ──────────────────────────────────────────────────────

ROW_CAP = 5000


def _transactions(company, accounts, from_date, to_date, filters, opening):
    """Every ledger movement on a money account, with a running balance.

    Capped: a full year on this company is over thirty thousand rows and the browser will not
    thank anybody for that. When the cap bites the report says so and names the figure, rather
    than quietly showing a partial answer that looks complete.
    """
    params = {
        "company": company,
        "accounts": tuple(accounts),
        "from_date": from_date,
        "to_date": to_date,
        "cap": ROW_CAP + 1,
    }
    where, params = _voucher_filters(filters, params)

    rows = frappe.db.sql(
        "SELECT gle.posting_date, gle.voucher_type, gle.voucher_no, gle.account,"
        "       gle.party_type, gle.party, gle.against, gle.remarks, gle.cost_center,"
        "       gle.debit AS money_in, gle.credit AS money_out"
        " FROM `tabGL Entry` gle"
        " WHERE gle.company = %(company)s AND gle.is_cancelled = 0"
        "   AND gle.account IN %(accounts)s"
        "   AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s"
        + where +
        " ORDER BY gle.posting_date, gle.creation LIMIT %(cap)s",
        params,
        as_dict=True,
    )

    truncated = len(rows) > ROW_CAP
    rows = rows[:ROW_CAP]

    modes = _modes_for(rows)
    running, total_in, total_out = opening, 0.0, 0.0
    data = [{
        "period": _("Opening Balance"), "running": opening, "is_opening": 1,
        "money_in": None, "money_out": None, "net": None,
    }]
    for r in rows:
        running = flt(running + flt(r.money_in) - flt(r.money_out), 3)
        total_in += flt(r.money_in)
        total_out += flt(r.money_out)
        data.append({
            "posting_date": r.posting_date,
            "voucher_type": r.voucher_type,
            "voucher_no": r.voucher_no,
            "account": r.account,
            "party": r.party or "",
            "against": r.against or "",
            "mode_of_payment": modes.get(r.voucher_no, ""),
            "remarks": (r.remarks or "")[:140],
            "money_in": flt(r.money_in, 3),
            "money_out": flt(r.money_out, 3),
            "net": flt(flt(r.money_in) - flt(r.money_out), 3),
            "running": running,
        })

    data.append({
        "period": _("Total"), "voucher_type": _("Total"),
        "money_in": flt(total_in, 3), "money_out": flt(total_out, 3),
        "net": flt(total_in - total_out, 3), "running": running, "is_total": 1,
    })

    msg = _message(accounts, filters)
    if truncated:
        msg += (
            "<div style='margin-top:6px;padding:8px 10px;border-left:3px solid #b71c1c;"
            "background:#fdecea'>"
            + _("Showing the first {0} transactions of this period; there are more. Narrow the "
                "dates, pick a single account, or use one of the By views to see everything "
                "summarised.").format(ROW_CAP)
            + "</div>"
        )

    summary = [
        {"label": _("Opening Balance"), "value": opening, "datatype": "Currency", "indicator": "Blue"},
        {"label": _("Money In"), "value": flt(total_in, 3), "datatype": "Currency", "indicator": "Green"},
        {"label": _("Money Out"), "value": flt(total_out, 3), "datatype": "Currency", "indicator": "Red"},
        {"label": _("Transactions"), "value": len(rows), "datatype": "Int"},
        {"label": _("Closing Balance"), "value": running, "datatype": "Currency",
         "indicator": "Blue" if running >= 0 else "Red"},
    ]
    return _txn_columns(company), data, msg, None, summary


def _grouped(company, accounts, from_date, to_date, filters, view):
    """The same money, totalled by whatever the reader is asking about."""
    field = {
        "By Party": "gle.party",
        "By Voucher Type": "gle.voucher_type",
        "By Account": "gle.account",
    }.get(view)

    params = {"company": company, "accounts": tuple(accounts),
              "from_date": from_date, "to_date": to_date}
    where, params = _voucher_filters(filters, params)

    if view == "By Mode of Payment":
        return _by_mode(company, accounts, from_date, to_date, filters, params, where)

    rows = frappe.db.sql(
        "SELECT COALESCE(NULLIF(" + field + ", ''), '" + _("(not set)") + "') AS grp,"
        "       ROUND(SUM(gle.debit), 3) AS money_in,"
        "       ROUND(SUM(gle.credit), 3) AS money_out,"
        "       COUNT(*) AS txns"
        " FROM `tabGL Entry` gle"
        " WHERE gle.company = %(company)s AND gle.is_cancelled = 0"
        "   AND gle.account IN %(accounts)s"
        "   AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s"
        + where +
        " GROUP BY grp ORDER BY (SUM(gle.debit) + SUM(gle.credit)) DESC",
        params,
        as_dict=True,
    )
    return _finish_grouped(company, rows, view, accounts, filters)


def _by_mode(company, accounts, from_date, to_date, filters, params, where):
    """Mode of payment lives on the Payment Entry, not on the ledger row, so it is joined in.

    Everything that moved money by another route - journals, POS settlements - lands under
    'No payment entry', which on this site is a large number and worth seeing rather than
    hiding.
    """
    rows = frappe.db.sql(
        "SELECT COALESCE(NULLIF(pe.mode_of_payment, ''), '" + _("No payment entry") + "') AS grp,"
        "       ROUND(SUM(gle.debit), 3) AS money_in,"
        "       ROUND(SUM(gle.credit), 3) AS money_out,"
        "       COUNT(*) AS txns"
        " FROM `tabGL Entry` gle"
        " LEFT JOIN `tabPayment Entry` pe"
        "        ON pe.name = gle.voucher_no AND gle.voucher_type = 'Payment Entry'"
        " WHERE gle.company = %(company)s AND gle.is_cancelled = 0"
        "   AND gle.account IN %(accounts)s"
        "   AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s"
        + where +
        " GROUP BY grp ORDER BY (SUM(gle.debit) + SUM(gle.credit)) DESC",
        params,
        as_dict=True,
    )
    return _finish_grouped(company, rows, "By Mode of Payment", accounts, filters)


def _finish_grouped(company, rows, view, accounts, filters):
    data, total_in, total_out = [], 0.0, 0.0
    for r in rows:
        total_in += flt(r.money_in)
        total_out += flt(r.money_out)
        data.append({
            "group": r.grp,
            "txns": cint(r.txns),
            "money_in": flt(r.money_in, 3),
            "money_out": flt(r.money_out, 3),
            "net": flt(flt(r.money_in) - flt(r.money_out), 3),
        })
    data.append({
        "group": _("Total"), "txns": sum(cint(r.txns) for r in rows),
        "money_in": flt(total_in, 3), "money_out": flt(total_out, 3),
        "net": flt(total_in - total_out, 3), "is_total": 1,
    })

    chart = {
        "data": {
            "labels": [d["group"] for d in data[:-1]][:12],
            "datasets": [
                {"name": _("Money In"), "values": [flt(d["money_in"]) for d in data[:-1]][:12]},
                {"name": _("Money Out"), "values": [flt(d["money_out"]) for d in data[:-1]][:12]},
            ],
        },
        "type": "bar",
        "colors": ["#2e7d32", "#c62828"],
        "fieldtype": "Currency",
    } if len(data) > 1 else None

    summary = [
        {"label": _("Money In"), "value": flt(total_in, 3), "datatype": "Currency", "indicator": "Green"},
        {"label": _("Money Out"), "value": flt(total_out, 3), "datatype": "Currency", "indicator": "Red"},
        {"label": _("Net Movement"), "value": flt(total_in - total_out, 3), "datatype": "Currency",
         "indicator": "Green" if total_in >= total_out else "Red"},
        {"label": _("Groups"), "value": len(rows), "datatype": "Int"},
    ]
    return _grouped_columns(company, view), data, _message(accounts, filters), chart, summary


def _voucher_filters(filters, params):
    """The account / mode-of-payment / internal-transfer filters, shared by every view."""
    where = ""
    if cint(filters.get("exclude_internal_transfers")):
        where += (
            " AND gle.voucher_no NOT IN ("
            "   SELECT x.voucher_no FROM `tabGL Entry` x"
            "   WHERE x.company = %(company)s AND x.is_cancelled = 0"
            "     AND x.account IN %(accounts)s"
            "     AND x.posting_date BETWEEN %(from_date)s AND %(to_date)s"
            "   GROUP BY x.voucher_no HAVING COUNT(DISTINCT x.account) > 1)"
        )
    if filters.get("mode_of_payment"):
        vouchers = frappe.get_all(
            "Payment Entry",
            filters={"company": params["company"], "docstatus": 1,
                     "mode_of_payment": filters.get("mode_of_payment")},
            pluck="name",
        )
        params["vouchers"] = tuple(vouchers) if vouchers else ("__none__",)
        where += " AND gle.voucher_no IN %(vouchers)s"
    return where, params


def _modes_for(rows):
    """Mode of payment for the payment entries among these rows, in one query."""
    names = list({r.voucher_no for r in rows if r.voucher_type == "Payment Entry"})
    if not names:
        return {}
    return {
        r.name: r.mode_of_payment
        for r in frappe.get_all("Payment Entry", filters={"name": ["in", names]},
                                fields=["name", "mode_of_payment"])
    }


def _txn_columns(company):
    currency = frappe.db.get_value("Company", company, "default_currency")
    return [
        {"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
        {"label": _("Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 120},
        {"label": _("Voucher"), "fieldname": "voucher_no", "fieldtype": "Dynamic Link",
         "options": "voucher_type", "width": 175},
        {"label": _("Money Account"), "fieldname": "account", "fieldtype": "Link",
         "options": "Account", "width": 180},
        {"label": _("Party"), "fieldname": "party", "fieldtype": "Data", "width": 150},
        {"label": _("Against"), "fieldname": "against", "fieldtype": "Data", "width": 180},
        {"label": _("Mode"), "fieldname": "mode_of_payment", "fieldtype": "Data", "width": 110},
        {"label": _("Money In"), "fieldname": "money_in", "fieldtype": "Currency",
         "options": "currency", "width": 120},
        {"label": _("Money Out"), "fieldname": "money_out", "fieldtype": "Currency",
         "options": "currency", "width": 120},
        {"label": _("Running Balance"), "fieldname": "running", "fieldtype": "Currency",
         "options": "currency", "width": 140},
        {"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 240},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 1,
         "hidden": 1, "default": currency},
    ]


def _grouped_columns(company, view):
    currency = frappe.db.get_value("Company", company, "default_currency")
    label = {"By Party": _("Party"), "By Voucher Type": _("Voucher Type"),
             "By Account": _("Money Account"), "By Mode of Payment": _("Mode of Payment")}.get(
        view, _("Group"))
    return [
        {"label": label, "fieldname": "group", "fieldtype": "Data", "width": 280},
        {"label": _("Transactions"), "fieldname": "txns", "fieldtype": "Int", "width": 110},
        {"label": _("Money In"), "fieldname": "money_in", "fieldtype": "Currency",
         "options": "currency", "width": 150},
        {"label": _("Money Out"), "fieldname": "money_out", "fieldtype": "Currency",
         "options": "currency", "width": 150},
        {"label": _("Net"), "fieldname": "net", "fieldtype": "Currency",
         "options": "currency", "width": 150},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 1,
         "hidden": 1, "default": currency},
    ]
