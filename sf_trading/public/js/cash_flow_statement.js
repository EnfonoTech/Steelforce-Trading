// sf_trading/public/js/cash_flow_statement.js
// Formatting and drill-down of the Cash Flow Statement. Loaded on every desk page (app_include_js)
// because a query report's own script is loaded with the report and this one is also used by the
// click handler. UI only: the figures and the check that they tie to the ledger are the server's.
//
// Every figure is a link. It opens the General Ledger for the accounts behind that row, for the dates of
// the column clicked, with the company and cost centre of the report. A line row carries its accounts;
// a group, section or total row carries the rows it adds up (`lines`), whose accounts are joined here.

frappe.provide("sf_trading.cash_flow_statement");

(function (cfs) {
	cfs.accounts_of = function (data, row) {
		const by_rid = {};
		data.forEach((d) => (by_rid[d.rid] = d));
		const seen = new Set();
		const walk = (r) => {
			(r.accounts || []).forEach((a) => seen.add(a));
			(r.lines || []).forEach((rid) => by_rid[rid] && walk(by_rid[rid]));
		};
		walk(row);
		return Array.from(seen);
	};

	// the dates of a column: the server puts them on the first row
	cfs.range_of = function (report, fieldname) {
		const meta = (report.data || []).find((d) => d.periods);
		const known = meta && meta.periods[fieldname];
		if (known) return known;
		const filters = report.get_filter_values();
		return { from_date: filters.from_date, to_date: filters.to_date };
	};

	cfs.open_ledger = function (report, rid, fieldname) {
		const row = (report.data || []).find((d) => d.rid === rid);
		if (!row) return;
		const accounts = cfs.accounts_of(report.data, row);
		if (!accounts.length) {
			frappe.show_alert({ message: __("Nothing to open behind this figure."), indicator: "orange" });
			return;
		}
		const filters = report.get_filter_values();
		const range = cfs.range_of(report, fieldname);
		const route = {
			company: filters.company,
			from_date: range.from_date,
			to_date: range.to_date,
			account: accounts,
			group_by: "Group by Voucher (Consolidated)",
		};
		if (filters.cost_center) route.cost_center = [filters.cost_center];
		frappe.route_options = route;
		frappe.set_route("query-report", "General Ledger");
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
		const drillable = data.is_line || data.is_account || data.is_group || data.is_total;
		if (drillable && (data.accounts || data.lines)) {
			value = `<a class="sf-cfs-drill" data-rid="${data.rid}" data-col="${column.fieldname}" style="cursor:pointer">${value}</a>`;
		}
		return data.bold ? `<b>${value}</b>` : value;
	};

	cfs.onload = function (report) {
		// the report's own wrapper is replaced on every refresh, so the handler is bound on the page
		report.page.main.off("click.sfcfs").on("click.sfcfs", "a.sf-cfs-drill", function (e) {
			e.preventDefault();
			cfs.open_ledger(report, cint($(this).attr("data-rid")), $(this).attr("data-col"));
		});
	};
})(sf_trading.cash_flow_statement);
