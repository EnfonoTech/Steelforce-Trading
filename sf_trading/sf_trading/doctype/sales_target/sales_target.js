// sf_trading/sf_trading/doctype/sales_target/sales_target.js
//
// Twelve rows typed by hand is how a target sheet gets abandoned, so the form fills them from
// one annual figure and lets any month be adjusted afterwards. The remainder lands on the last
// month rather than being spread as a fraction of a fils.

frappe.ui.form.on("Sales Target", {
	setup(frm) {
		frm.set_query("dimension_value", function () {
			if (frm.doc.dimension_type === "Sales Person") {
				return { filters: { is_group: 0, enabled: 1 } };
			}
			return {};
		});
		frm.set_query("branch", function () {
			return { filters: { company: frm.doc.company } };
		});
	},

	onload(frm) {
		if (frm.is_new()) {
			if (!frm.doc.company) frm.set_value("company", frappe.defaults.get_user_default("Company"));
			if (!frm.doc.fiscal_year) {
				frappe.db.get_value("Fiscal Year", { disabled: 0 }, "name").then((r) => {
					if (r && r.message && !frm.doc.fiscal_year) {
						frm.set_value("fiscal_year", r.message.name);
					}
				});
			}
		}
	},

	refresh(frm) {
		frm.add_custom_button(__("Distribute Evenly"), () => sf_distribute(frm));
		frm.add_custom_button(__("Show Achievement"), () => {
			frappe.set_route("query-report",
				frm.doc.dimension_type === "Branch"
					? "Branch Sales Target vs Actual"
					: "Sales Person Target vs Actual",
				{ company: frm.doc.company, fiscal_year: frm.doc.fiscal_year, basis: frm.doc.basis });
		});
	},

	dimension_type(frm) {
		frm.set_value("dimension_value", null);
		if (frm.doc.dimension_type === "Branch") frm.set_value("branch", null);
	},

	annual_target(frm) {
		if (frm.doc.annual_target && !(frm.doc.targets || []).length) sf_distribute(frm);
	},
});

frappe.ui.form.on("Sales Target Month", {
	target_amount: (frm) => sf_total(frm),
	targets_remove: (frm) => sf_total(frm),
});

function sf_total(frm) {
	let total = 0;
	(frm.doc.targets || []).forEach((r) => (total += flt(r.target_amount)));
	frm.set_value("total_target", total);
}

function sf_distribute(frm) {
	const annual = flt(frm.doc.annual_target);
	if (!annual) {
		frappe.msgprint({
			title: __("Nothing to Distribute"),
			message: __("Enter an Annual Target first."),
			indicator: "orange",
		});
		return;
	}
	const months = ["January", "February", "March", "April", "May", "June",
		"July", "August", "September", "October", "November", "December"];
	const precision = frappe.defaults.get_default("currency_precision") || 2;
	const each = flt(annual / 12, precision);
	frm.clear_table("targets");
	months.forEach((month, i) => {
		const row = frm.add_child("targets", { month: month });
		// the last month absorbs the rounding, so twelve rows always sum to the annual figure
		row.target_amount = i === 11 ? flt(annual - each * 11, precision) : each;
	});
	frm.refresh_field("targets");
	sf_total(frm);
}
