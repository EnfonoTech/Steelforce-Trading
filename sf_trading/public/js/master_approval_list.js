// sf_trading/public/js/master_approval_list.js
// Actions > Approve and Actions > Bypass Approval for the selected Customers / Suppliers / Items
// (sf_trading/master_approval.py) -- the way through for the older masters Strict Validation brings in.
// Added to whatever list settings the doctype already has; the server re-checks who may do either.

(function () {
	// mirrors master_approval.APPROVER_ROLES
	const APPROVERS = {
		Customer: ["Customer Approver", "Credit Approval Officer", "System Manager"],
		Supplier: ["Supplier Approver", "System Manager"],
		Item: ["Item Master Approver", "System Manager"],
	};

	function selected(listview) {
		return listview.get_checked_items(true);
	}

	function report(title, r) {
		const out = r.message || {};
		const done = out.approved || out.bypassed || [];
		const skipped = out.skipped || [];
		let html = "<p>" + __("{0} done.", [done.length]) + "</p>";
		if (skipped.length) {
			html += "<p>" + __("Skipped:") + "</p><ul>" +
				skipped.map((s) => "<li><b>" + frappe.utils.escape_html(s.name) + "</b>: " + s.reason + "</li>").join("") + "</ul>";
		}
		frappe.msgprint({ title: title, message: html, indicator: skipped.length ? "orange" : "green" });
	}

	function add_actions(listview) {
		const dt = listview.doctype;
		if (listview.__sf_master_approval_actions) return;
		listview.__sf_master_approval_actions = true;

		if (frappe.user.has_role(APPROVERS[dt] || [])) {
			listview.page.add_actions_menu_item(__("Approve"), () => {
				const names = selected(listview);
				if (!names.length) return;
				frappe.call({
					method: "sf_trading.master_approval.approve_masters",
					args: { doctype: dt, names: names },
					freeze: true,
					callback: (r) => {
						report(__("Approve"), r);
						listview.refresh();
					},
				});
			}, false);
		}
		// System Manager here; a role or user on the bypass roster gets a refusal it can read
		if (frappe.user.has_role("System Manager")) {
			listview.page.add_actions_menu_item(__("Bypass Approval"), () => {
				const names = selected(listview);
				if (!names.length) return;
				frappe.prompt(
					{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
					(values) =>
						frappe.call({
							method: "sf_trading.master_approval.bypass_approval",
							args: { doctype: dt, names: names, reason: values.reason },
							freeze: true,
							callback: (r) => {
								report(__("Bypass Approval"), r);
								listview.refresh();
							},
						}),
					__("Bypass Approval for {0} selected", [names.length]),
					__("Bypass")
				);
			}, false);
		}
	}

	["Customer", "Supplier", "Item"].forEach((dt) => {
		const settings = frappe.listview_settings[dt] || (frappe.listview_settings[dt] = {});
		if (settings.__sf_master_approval) return;
		settings.__sf_master_approval = true;
		const onload = settings.onload;
		settings.onload = function (listview) {
			if (onload) onload.apply(this, arguments);
			add_actions(listview);
		};
	});
})();
