// sf_trading/sf_trading/report/pdc_report/pdc_report.js
frappe.query_reports["PDC Report"] = {
    filters: [
        {
            fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"),
        },
        { fieldname: "from_date", label: __("Cheque Date From"), fieldtype: "Date" },
        { fieldname: "to_date", label: __("Cheque Date To"), fieldtype: "Date" },
        {
            fieldname: "payment_type", label: __("Payment Type"), fieldtype: "Select",
            options: ["", "Receive", "Pay", "Internal Transfer"].join("\n"),
        },
        {
            fieldname: "party_type", label: __("Party Type"), fieldtype: "Link", options: "DocType",
            get_query: () => ({ filters: { name: ["in", ["Customer", "Supplier", "Employee"]] } }),
        },
        {
            fieldname: "party", label: __("Party"), fieldtype: "Dynamic Link",
            get_options: function () {
                return frappe.query_report.get_filter_value("party_type");
            },
        },
        {
            fieldname: "status", label: __("Status"), fieldtype: "Select",
            options: ["", "Pending", "Cleared"].join("\n"), default: "",
        },
        {
            fieldname: "transfer_status", label: __("Transfer Status"), fieldtype: "Select",
            options: ["", "Not Transferred", "Draft Transfer", "Transferred"].join("\n"), default: "",
        },
        { fieldname: "include_cancelled", label: __("Include Cancelled"), fieldtype: "Check", default: 0 },
    ],

    // Tick the cheques the bank has credited, then use Create Internal Transfer.
    get_datatable_options(options) {
        return Object.assign({}, options, { checkboxColumn: true });
    },

    onload: function (report) {
        report.page.add_inner_button(__("Create Internal Transfer"), function () {
            sf_pdc_transfer_selected(report);
        });
    },

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        // the cheque date is what the money and the reminder both hang on — always red
        if (column.fieldname === "cheque_date" && data.cheque_date) {
            value = `<span style="color:var(--red-500)"><b>${value}</b></span>`;
        }

        // reminder day: red once it has passed, orange on the day itself
        if (column.fieldname === "reminder_date" && data.days_to_reminder_anchor != null) {
            if (data.days_to_reminder_anchor < 3) value = `<span style="color:var(--red-500)">${value}</span>`;
            else if (data.days_to_reminder_anchor === 3) value = `<span style="color:var(--orange-500)"><b>${value}</b></span>`;
        }

        for (const field of ["days_to_posting_date", "days_to_cheque_date"]) {
            if (column.fieldname === field && data[field] != null) {
                if (data[field] < 0) value = `<span style="color:var(--red-500)">${value}</span>`;
                else if (data[field] === 0) value = `<span style="color:var(--orange-500)"><b>${value}</b></span>`;
            }
        }

        // the one column that answers "has this cheque been banked?"
        if (column.fieldname === "transfer_status") {
            if (data.transfer_status === "Transferred") {
                value = `<span style="color:var(--green-600)"><b>${value}</b></span>`;
            } else if (data.transfer_status === "Draft Transfer") {
                value = `<span style="color:var(--orange-500)">${value}</span>`;
            } else if (data.status === "Pending" && data.days_to_cheque_date != null && data.days_to_cheque_date < 0) {
                // cheque date has passed and nothing has been banked
                value = `<span style="color:var(--red-500)">${value}</span>`;
            }
        }

        return value;
    },
};

function sf_pdc_transfer_selected(report) {
    const checked = (report.get_checked_items && report.get_checked_items()) || [];
    const rows = checked.filter(function (row) {
        return row && row.payment_entry;
    });

    if (!rows.length) {
        frappe.msgprint({
            title: __("Nothing Selected"),
            message: __("Tick the cheques the bank has credited, then use Create Internal Transfer."),
            indicator: "orange",
        });
        return;
    }

    const already = rows.filter((row) => row.transfer_status !== "Not Transferred");
    if (already.length) {
        frappe.msgprint({
            title: __("Already Transferred"),
            message: __("{0} of the selected cheques already have an internal transfer: {1}", [
                already.length,
                already.map((row) => row.payment_entry).join(", "),
            ]),
            indicator: "red",
        });
        return;
    }

    const companies = Array.from(new Set(rows.map((row) => row.company)));
    if (companies.length > 1) {
        frappe.msgprint({
            title: __("One Company at a Time"),
            message: __("The selected cheques belong to more than one company."),
            indicator: "red",
        });
        return;
    }

    const total = rows.reduce((sum, row) => sum + flt(row.amount), 0);
    const d = new frappe.ui.Dialog({
        title: __("Create Internal Transfer"),
        fields: [
            {
                fieldname: "summary",
                fieldtype: "HTML",
                options: `<p>${__("Banking {0} cheque(s), {1} in total, out of the cheque account.", [
                    rows.length,
                    format_currency(total, rows[0].currency),
                ])}</p>`,
            },
            {
                fieldname: "to_account",
                fieldtype: "Link",
                options: "Account",
                label: __("Credited To (Bank Account)"),
                reqd: 1,
                get_query: function () {
                    return {
                        filters: {
                            company: companies[0],
                            is_group: 0,
                            account_type: ["in", ["Bank", "Cash"]],
                        },
                    };
                },
            },
            {
                fieldname: "posting_date",
                fieldtype: "Date",
                label: __("Transfer Date"),
                default: frappe.datetime.get_today(),
                reqd: 1,
            },
            {
                fieldname: "submit_transfer",
                fieldtype: "Check",
                label: __("Submit the transfer"),
                default: 1,
                description: __("Leave unticked to keep the transfer as a draft for approval."),
            },
        ],
        primary_action_label: __("Create"),
        primary_action: function (values) {
            d.hide();
            frappe.call({
                method: "sf_trading.pdc_transfer.create_internal_transfers",
                args: {
                    payment_entries: JSON.stringify(rows.map((row) => row.payment_entry)),
                    to_account: values.to_account,
                    posting_date: values.posting_date,
                    submit: values.submit_transfer ? 1 : 0,
                },
                freeze: true,
                freeze_message: __("Creating internal transfers..."),
                callback: function (r) {
                    const result = (r && r.message) || {};
                    if ((result.created || []).length) {
                        frappe.show_alert(
                            {
                                message: __("Created {0} internal transfer(s)", [result.created.length]),
                                indicator: "green",
                            },
                            6
                        );
                    }
                    if ((result.failed || []).length) {
                        frappe.msgprint({
                            title: __("Some Cheques Were Not Transferred"),
                            message: result.failed
                                .map((row) => `<b>${row.payment_entry}</b>: ${row.error}`)
                                .join("<br>"),
                            indicator: "red",
                        });
                    }
                    report.refresh();
                },
            });
        },
    });
    d.show();
}
