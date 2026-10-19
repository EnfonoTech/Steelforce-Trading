// sf_trading/public/js/report_number_search.js
// The search bar finds a report by its number (sf_trading/report_numbering.py) as well as by its name:
// "RPT-0042", "rpt 42", "rpt42" and a bare "42" all offer RPT-0042; "rpt-004" offers RPT-0040 to RPT-0049.
// The numbers come with the boot (each entry of frappe.boot.user.all_reports carries `report_no`), so
// nothing is asked of the server while typing, and only reports the user may open can be offered.
// UI only: opening the report is still checked by the report itself.

frappe.sf_report_number_score = function (keywords, number) {
	const typed = String(keywords || "").toLowerCase().replace(/[\s-]+/g, "");
	const match = typed.match(/^(rpt)?(\d{1,4})$/);
	const have = String(number || "").slice(-4);
	if (!match || !/^\d{4}$/.test(have)) return 0;
	const digits = match[2];
	// a bare "4" is too common a thing to type to mean a report
	if (!match[1] && digits.length < 2) return 0;
	if (have === digits.padStart(4, "0")) return 1e6;
	// "rpt-004" lists the forties: a prefix of two or three digits, and only when RPT is typed
	if (match[1] && digits.length >= 2 && digits.length < 4 && have.startsWith(digits)) return 1e3;
	return 0;
};

frappe.sf_report_number_results = function (keywords, all_reports) {
	const found = [];
	Object.keys(all_reports || {}).forEach((name) => {
		const report = all_reports[name];
		const score = report && report.report_no ? frappe.sf_report_number_score(keywords, report.report_no) : 0;
		if (!score) return;
		found.push({
			type: "Report",
			label: __("Report {0}", [`<mark>${frappe.utils.escape_html(report.report_no)}</mark> ${frappe.utils.escape_html(__(name))}`]),
			value: __("Report {0}", [__(name)]),
			index: score,
			route: report.report_type == "Report Builder" ? ["List", report.ref_doctype, "Report", name] : ["query-report", name],
			sort_no: report.report_no,
		});
	});
	// a prefix can match a hundred reports: the nearest ten, in number order
	return found.sort((a, b) => b.index - a.index || (a.sort_no < b.sort_no ? -1 : 1)).slice(0, 10);
};

frappe.sf_install_report_number_search = function () {
	const utils = frappe.search && frappe.search.utils;
	if (!utils || utils.sf_report_number_search) return;
	const by_name = utils.get_reports;
	utils.get_reports = function (keywords) {
		const out = by_name.call(this, keywords) || [];
		const seen = new Set(out.map((d) => (d.route || []).join("/")));
		frappe.sf_report_number_results(keywords, frappe.boot.user && frappe.boot.user.all_reports).forEach((d) => {
			// a report found by its name and its number is listed once, as the number match
			const key = d.route.join("/");
			if (seen.has(key)) {
				const at = out.findIndex((x) => (x.route || []).join("/") === key);
				out.splice(at, 1);
			}
			out.push(d);
		});
		return out;
	};
	utils.sf_report_number_search = true;
};

frappe.sf_install_report_number_search();
$(document).on("startup", () => frappe.sf_install_report_number_search());
