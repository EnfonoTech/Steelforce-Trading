// sf_trading/public/js/cash_flow_statement.js
// Formatting and drill-down of the Cash Flow Statement and of its detail report (the vouchers behind a figure).
// Loaded on every desk page (app_include_js) because a query report's own script is loaded with the report and
// the click handler is bound on the page. UI only: the figures, and the check that the vouchers add up to the
// figure clicked, are the server's.
//
// Every figure of the statement is a link. It opens "Cash Flow Statement Detail" for that figure: the line
// (a line, a group, a section total or the whole statement, by its title), the dates of the column clicked, the
// branch and cost centre of the report, and for an account row the account(s). A voucher's number there
// opens the voucher; the General Ledger of the same accounts is a button.

frappe.provide("sf_trading.cash_flow_statement");

(function (cfs) {
	// the dates of a column: the server puts them on the first row
	cfs.range_of = function (report, fieldname) {
		const meta = (report.data || []).find((d) => d.periods);
		const known = meta && meta.periods[fieldname];
		if (known) return known;
		const filters = report.get_filter_values();
		return { from_date: filters.from_date, to_date: filters.to_date };
	};

	// what the detail report is told about a figure
	cfs.detail_route = function (report, row, fieldname) {
		const filters = report.get_filter_values();
		const range = cfs.range_of(report, fieldname);
		const route = {
			company: filters.company,
			from_date: range.from_date,
			to_date: range.to_date,
			method: filters.method,
			line: row.drill_line,
			// an account is opened voucher by voucher; a line by the kind of voucher first
			group_by: row.is_account ? "Vouchers" : "Voucher Type",
		};
		if (row.is_account) route.account = row.accounts;
		if (filters.cost_center) route.cost_center = filters.cost_center;
		if (filters.branch && filters.branch.length) route.branch = filters.branch;
		return route;
	};

	cfs.open_detail = function (report, rid, fieldname) {
		const row = (report.data || []).find((d) => d.rid === rid);
		if (!row || !row.drill_line) {
			frappe.show_alert({ message: __("Nothing to open behind this figure."), indicator: "orange" });
			return;
		}
		frappe.route_options = cfs.detail_route(report, row, fieldname);
		frappe.set_route("query-report", "Cash Flow Statement Detail");
	};

	cfs.formatter = function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		const is_amount = /^(p\d+|total)$/.test(column.fieldname);
		if (column.fieldname === "label") {
			return data.bold || data.is_header || data.is_total ? `<b>${value}</b>` : value;
		}
		if (!is_amount) return value;
		if (data.is_header) return "";
		const number = flt(data[column.fieldname]);
		if (number < 0) value = `<span style="color:#c62828">${value}</span>`;
		if (data.is_difference) {
			return Math.abs(number) >= 0.0005 ? `<b style="color:#c62828">${value}</b>` : `<b style="color:#2e7d32">${value}</b>`;
		}
		if (data.drill_line) {
			value = `<a class="sf-cfs-drill" data-rid="${data.rid}" data-col="${column.fieldname}" style="cursor:pointer" title="${__("Show the vouchers")}">${value}</a>`;
		}
		return data.bold ? `<b>${value}</b>` : value;
	};

	cfs.onload = function (report) {
		// the report's own wrapper is replaced on every refresh, so the handler is bound on the page
		report.page.main.off("click.sfcfs").on("click.sfcfs", "a.sf-cfs-drill", function (e) {
			e.preventDefault();
			cfs.open_detail(report, cint($(this).attr("data-rid")), $(this).attr("data-col"));
		});
	};

	// ─── the detail report ───────────────────────────────────────────────────────────────────────

	cfs.detail_formatter = function (value, row, column, data, default_formatter) {
		if (data && column.fieldname === "label" && data.is_voucher) {
			// the voucher's number opens the voucher
			return frappe.utils.get_form_link(data.voucher_type, data.voucher_no, true, data.voucher_no);
		}
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "amount" && flt(data.amount) < 0) value = `<span style="color:#c62828">${value}</span>`;
		if (data.is_pooled) value = `<i>${value}</i>`;
		return data.bold || data.is_total || data.is_group ? `<b>${value}</b>` : value;
	};

	cfs.statement_route = function (filters) {
		const route = {
			company: filters.company,
			from_date: filters.from_date,
			to_date: filters.to_date,
			method: filters.method,
		};
		if (filters.cost_center) route.cost_center = filters.cost_center;
		if (filters.branch && filters.branch.length) route.branch = filters.branch;
		return route;
	};

	cfs.ledger_route = function (filters, accounts) {
		const route = {
			company: filters.company,
			from_date: filters.from_date,
			to_date: filters.to_date,
			account: accounts,
			group_by: "Group by Voucher (Consolidated)",
		};
		if (filters.cost_center) route.cost_center = [filters.cost_center];
		return route;
	};

	cfs.detail_onload = function (report) {
		report.page.add_inner_button(__("Cash Flow Statement"), () => {
			frappe.route_options = cfs.statement_route(report.get_filter_values());
			frappe.set_route("query-report", "Cash Flow Statement");
		});
		report.page.add_inner_button(__("General Ledger"), () => {
			const total = (report.data || []).find((d) => d.is_total);
			const accounts = (total && total.accounts) || [];
			if (!accounts.length) {
				frappe.show_alert({ message: __("No accounts to open."), indicator: "orange" });
				return;
			}
			frappe.route_options = cfs.ledger_route(report.get_filter_values(), accounts);
			frappe.set_route("query-report", "General Ledger");
		});
	};
})(sf_trading.cash_flow_statement);
