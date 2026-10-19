// sf_trading/public/js/mis_profit_loss.js
// Formatting and drill-down of the MIS Profit and Loss and of its detail report (the vouchers behind a figure).
// Loaded on every desk page (app_include_js): the click handler is bound on the page and a query report's own
// script is loaded only with the report. UI only: the figures, the bridge to the books and the check that the
// vouchers add up to the figure clicked are the server's.
//
// Every amount of the profit and loss is a link to "MIS Profit and Loss Detail" for that figure: its section
// or subtotal, the dates (or the cost centre) of the column clicked, its origin (own books, charged in from
// another company, charged out to one), and for a line its account and source company.

frappe.provide("sf_trading.mis_pl");

(function (pl) {
	const ORIGIN = { own: "Own books", in: "Charged in", out: "Charged out" };

	// ─── the period filters, as on the Profit and Loss Statement ───────────────────────────────
	// Filter Based On: Date Range (From Date, To Date) or Fiscal Year (Start Year, End Year); the Quick Range
	// fills the dates for the usual ranges. The server resolves them (mis_profit_loss.resolve_period).

	const QUICK_RANGES = [
		"",
		"Today",
		"This Month",
		"Last Month",
		"This Quarter",
		"Last Quarter",
		"This Year",
		"Last Year",
		"Last 12 Months",
	];

	pl.quick_range_dates = function (name) {
		const d = frappe.datetime;
		const today = d.get_today();
		const before = (date) => d.add_days(date, -1);
		switch (name) {
			case "Today":
				return [today, today];
			case "This Month":
				return [d.month_start(), d.month_end()];
			case "Last Month":
				return [d.add_months(d.month_start(), -1), before(d.month_start())];
			case "This Quarter":
				return [d.quarter_start(), d.quarter_end()];
			case "Last Quarter":
				return [d.add_months(d.quarter_start(), -3), before(d.quarter_start())];
			case "This Year":
				return [d.year_start(), d.year_end()];
			case "Last Year":
				return [d.add_months(d.year_start(), -12), before(d.year_start())];
			case "Last 12 Months":
				return [d.add_days(d.add_months(today, -12), 1), today];
			default:
				return null;
		}
	};

	pl.current_fiscal_year = () => {
		const fy = frappe.boot && frappe.boot.current_fiscal_year;
		return fy && fy.length >= 3 ? { name: fy[0], start: fy[1], end: fy[2] } : null;
	};

	pl.period_filters = function () {
		const fy = pl.current_fiscal_year();
		const by_dates = "eval:doc.filter_based_on != 'Fiscal Year'";
		const by_year = "eval:doc.filter_based_on == 'Fiscal Year'";
		return [
			{
				fieldname: "filter_based_on",
				label: __("Filter Based On"),
				fieldtype: "Select",
				options: ["Date Range", "Fiscal Year"].join("\n"),
				default: "Date Range",
				on_change: () => frappe.query_report.refresh(),
			},
			{
				fieldname: "quick_range",
				label: __("Quick Range"),
				fieldtype: "Select",
				options: QUICK_RANGES.join("\n"),
				depends_on: by_dates,
				on_change: () => {
					const range = pl.quick_range_dates(frappe.query_report.get_filter_value("quick_range"));
					if (range) {
						frappe.query_report.set_filter_value({ from_date: range[0], to_date: range[1] });
					}
				},
			},
			{
				fieldname: "from_date",
				label: __("From Date"),
				fieldtype: "Date",
				default: fy ? fy.start : frappe.datetime.year_start(),
				depends_on: by_dates,
				mandatory_depends_on: by_dates,
			},
			{
				fieldname: "to_date",
				label: __("To Date"),
				fieldtype: "Date",
				default: frappe.datetime.get_today(),
				depends_on: by_dates,
				mandatory_depends_on: by_dates,
			},
			{
				fieldname: "from_fiscal_year",
				label: __("Start Year"),
				fieldtype: "Link",
				options: "Fiscal Year",
				default: fy ? fy.name : undefined,
				depends_on: by_year,
				mandatory_depends_on: by_year,
			},
			{
				fieldname: "to_fiscal_year",
				label: __("End Year"),
				fieldtype: "Link",
				options: "Fiscal Year",
				default: fy ? fy.name : undefined,
				depends_on: by_year,
				mandatory_depends_on: by_year,
			},
		];
	};

	pl.column_scope = function (report, fieldname) {
		const meta = (report.data || []).find((d) => d.columns_meta);
		const known = meta && meta.columns_meta[fieldname];
		return known || {};
	};

	pl.detail_route = function (report, row, fieldname) {
		const filters = report.get_filter_values();
		const scope = pl.column_scope(report, fieldname);
		// the dates of the column clicked, as dates: a Fiscal Year choice left on the detail report would ignore them
		const route = {
			company: filters.company,
			filter_based_on: "Date Range",
			from_date: scope.from_date || filters.from_date,
			to_date: scope.to_date || filters.to_date,
			group_by: row.is_line ? "Vouchers" : "Voucher Type",
		};
		if (row.drill_title && !String(row.drill_title).startsWith("charged")) route.line = row.drill_title;
		if (row.origin && ORIGIN[row.origin]) route.origin = ORIGIN[row.origin];
		if (row.is_line && row.account) route.account = [row.account];
		if (row.source_company) route.source_company = row.source_company;
		const cost_center = scope.cost_center !== undefined ? scope.cost_center : filters.cost_center;
		if (cost_center) route.cost_center = cost_center;
		if (filters.branch && filters.branch.length) route.branch = filters.branch;
		return route;
	};

	pl.drillable = (row) => !!(row.drill_title || (row.is_bridge && ORIGIN[row.origin]) || (row.is_line && row.origin));

	pl.open_detail = function (report, rid, fieldname) {
		const row = (report.data || []).find((d) => d.rid === rid);
		if (!row || !pl.drillable(row)) {
			frappe.show_alert({ message: __("Nothing to open behind this figure."), indicator: "orange" });
			return;
		}
		frappe.route_options = pl.detail_route(report, row, fieldname);
		frappe.set_route("query-report", "MIS Profit and Loss Detail");
	};

	pl.formatter = function (value, row, column, data, default_formatter) {
		if (data && data.is_ratio && /^(p\d+|total|pct\d+)$/.test(column.fieldname)) {
			const text = value === null || value === undefined ? "" : `${flt(value).toFixed(1)}%`;
			return `<i style="color:#6b7280">${text}</i>`;
		}
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "label") {
			if (data.is_ratio) return `<i style="color:#6b7280">${value}</i>`;
			return data.bold || data.is_header || data.is_total || data.is_section ? `<b>${value}</b>` : value;
		}
		const is_amount = /^(p\d+|total|change)$/.test(column.fieldname);
		if (!is_amount) return value;
		if (data.is_header) return "";
		const number = flt(data[column.fieldname]);
		if (number < 0) value = `<span style="color:#c62828">${value}</span>`;
		if (data.is_difference) {
			return Math.abs(number) >= 0.0005 ? `<b style="color:#c62828">${value}</b>` : `<b style="color:#2e7d32">${value}</b>`;
		}
		if (column.fieldname !== "change" && pl.drillable(data)) {
			value = `<a class="sf-mis-drill" data-rid="${data.rid}" data-col="${column.fieldname}" style="cursor:pointer" title="${__("Show the vouchers")}">${value}</a>`;
		}
		return data.bold ? `<b>${value}</b>` : value;
	};

	pl.onload = function (report) {
		report.page.main.off("click.sfmis").on("click.sfmis", "a.sf-mis-drill", function (e) {
			e.preventDefault();
			pl.open_detail(report, cint($(this).attr("data-rid")), $(this).attr("data-col"));
		});
	};

	// ─── the detail report ──────────────────────────────────────────────────────────────────────

	pl.detail_formatter = function (value, row, column, data, default_formatter) {
		if (data && column.fieldname === "label" && data.is_voucher) {
			return frappe.utils.get_form_link(data.voucher_type, data.voucher_no, true, data.voucher_no);
		}
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "amount" && flt(data.amount) < 0) value = `<span style="color:#c62828">${value}</span>`;
		if (data.is_pooled) value = `<i>${value}</i>`;
		return data.bold || data.is_total || data.is_group ? `<b>${value}</b>` : value;
	};

	pl.statement_route = function (filters) {
		// the period as chosen on the detail report: its dates, or its fiscal years
		const route = {
			company: filters.company,
			filter_based_on: filters.filter_based_on || "Date Range",
			from_date: filters.from_date,
			to_date: filters.to_date,
		};
		if (filters.from_fiscal_year) route.from_fiscal_year = filters.from_fiscal_year;
		if (filters.to_fiscal_year) route.to_fiscal_year = filters.to_fiscal_year;
		if (filters.cost_center) route.cost_center = filters.cost_center;
		if (filters.branch && filters.branch.length) route.branch = filters.branch;
		return route;
	};

	pl.detail_onload = function (report) {
		report.page.add_inner_button(__("MIS Profit and Loss"), () => {
			frappe.route_options = pl.statement_route(report.get_filter_values());
			frappe.set_route("query-report", "MIS Profit and Loss");
		});
	};
})(sf_trading.mis_pl);
