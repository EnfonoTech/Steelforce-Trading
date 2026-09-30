// Makes ERPNext's "Customer Acquisition and Loyalty" clickable: a count or revenue opens
// "Customer Acquisition and Loyalty Detail" for that month or territory, whose total row equals
// the clicked cell, and the Year/Month label opens every customer of that month. Appended to
// the report's own script by sf_trading.api.report_script, so it runs right after the report
// defines its settings; nothing in erpnext is edited.
(() => {
	const NAME = "Customer Acquisition and Loyalty";
	const DETAIL = "Customer Acquisition and Loyalty Detail";
	const LINKED = {
		new_customers: "New",
		new_customer_revenue: "New",
		repeat_customers: "Repeat",
		repeat_customer_revenue: "Repeat",
		total: "",
		total_revenue: "",
		// the row's own label: every customer of that month (Territory is already a Link)
		year: "",
		month: "",
	};
	// calendar.month_name on the server, always English
	const MONTHS = [
		"January", "February", "March", "April", "May", "June",
		"July", "August", "September", "October", "November", "December",
	];

	window.sfCALDrill = function (args) {
		args = JSON.parse(decodeURIComponent(args));
		const f = (k) => frappe.query_report.get_filter_value(k);
		let from_date = f("from_date");
		let to_date = f("to_date");
		if (args.year) {
			const start = moment([cint(args.year), MONTHS.indexOf(args.month), 1]);
			const fmt = "YYYY-MM-DD";
			from_date = moment.max(start, moment(from_date)).format(fmt);
			to_date = moment.min(start.clone().endOf("month"), moment(to_date)).format(fmt);
		}
		frappe.route_options = {
			company: f("company"),
			from_date,
			to_date,
			customer_type: args.customer_type,
			territory: args.territory || "",
		};
		frappe.set_route("query-report", DETAIL);
	};

	const settings = frappe.query_reports[NAME];
	if (!settings || settings.__sf_drill) return;
	const formatter = settings.formatter;
	settings.formatter = function (value, row, column, data, default_formatter) {
		value = formatter
			? formatter(value, row, column, data, default_formatter)
			: default_formatter(value, row, column, data);
		// no data object on the total row; nothing to list in a month without invoices
		if (!data || !(column.fieldname in LINKED) || !flt(data.total)) return value;
		if (!flt(data[column.fieldname]) && !["year", "month"].includes(column.fieldname)) return value;
		const args = encodeURIComponent(
			JSON.stringify({
				year: data.year,
				month: data.month,
				territory: data.territory,
				customer_type: LINKED[column.fieldname],
			})
		).replace(/'/g, "%27");
		// desk styles <a> like plain text, so colour it or nobody knows it is a link
		return `<a href="#" style="color: var(--blue-600)" onclick="sfCALDrill('${args}'); return false;">${value}</a>`;
	};
	settings.__sf_drill = true;
})();
