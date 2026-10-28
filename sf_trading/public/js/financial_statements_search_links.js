// Keeps the account drill-down link on Trial Balance, Balance Sheet, P&L and Cash Flow
// while the column search is in use.
//
// The datatable's column search calls the formatter with the search text for every row
// that has not been drawn yet. ERPNext's formatter then returns the bare account name
// (no link) when that name does not contain the search text, and the datatable caches
// that HTML on the cell. When the row is shown later (a tree node is opened, the search
// is cleared or changed) it is drawn from the cache and the account is not clickable
// until the report is reloaded.
//
// Dropping the search text means the formatter always builds the link. Filtering is not
// affected: the datatable still matches the search against the account and its text.
// SF Trading Settings > Report Search turns it off (see financial_statements_search_links.py).

(function () {
	if (!window.erpnext || !erpnext.financial_statements) return;
	if (!frappe.boot.sf_keep_report_links) return;

	const formatter = erpnext.financial_statements.formatter;
	erpnext.financial_statements.formatter = function (value, row, column, data, default_formatter) {
		return formatter.call(this, value, row, column, data, default_formatter);
	};
})();
