// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// Accounts that were made before new accounts were numbered automatically (sf_trading/account_numbering.py):
// see what each would get, and number them.
frappe.ui.form.on("SF Trading Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Preview Unnumbered Accounts"), () => sf_ask_company(false), __("Account Numbers"));
		frm.add_custom_button(__("Number Unnumbered Accounts"), () => sf_ask_company(true), __("Account Numbers"));
	},
});

function sf_ask_company(apply) {
	frappe.prompt(
		[
			{
				fieldname: "company",
				label: __("Company"),
				fieldtype: "Link",
				options: "Company",
				default: frappe.defaults.get_user_default("Company"),
				reqd: 1,
			},
		],
		(values) => (apply ? sf_confirm_numbering(values.company) : sf_preview_numbering(values.company)),
		apply ? __("Number Unnumbered Accounts") : __("Preview Unnumbered Accounts"),
		apply ? __("Number Them") : __("Preview")
	);
}

function sf_preview_numbering(company) {
	frappe.call({
		method: "sf_trading.account_numbering.number_existing_accounts",
		args: { company: company },
		freeze: true,
		callback(r) {
			const out = r.message || {};
			const rows = (out.plan || [])
				.map(
					(p) =>
						`<tr><td>${frappe.utils.escape_html(p.number)}</td><td>${frappe.utils.escape_html(
							p.account_name
						)}</td><td>${frappe.utils.escape_html(p.parent_account)}</td></tr>`
				)
				.join("");
			frappe.msgprint({
				title: __("Accounts Without A Number"),
				wide: true,
				message: rows
					? `<p>${__("{0} account(s) would be numbered:", [out.left])}</p>
						<table class="table table-bordered table-sm"><thead><tr><th>${__("Number")}</th><th>${__("Account")}</th><th>${__(
							"Under"
					  )}</th></tr></thead><tbody>${rows}</tbody></table>`
					: __("Every account that can be numbered already has a number."),
			});
		},
	});
}

function sf_confirm_numbering(company) {
	frappe.confirm(
		__(
			"This renames every unnumbered account of {0} so that its name starts with its number; entries and documents follow the new name. ERPNext only renames while no entries are being posted, so run it when the system is quiet. Continue?",
			[company]
		),
		() => sf_number_batch(company, 0)
	);
}

// 25 accounts at a time, one commit each, until none is left or ERPNext refuses (entries are being posted)
function sf_number_batch(company, done) {
	frappe.call({
		method: "sf_trading.account_numbering.number_existing_accounts",
		args: { company: company, apply: 1, limit: 25 },
		freeze: true,
		freeze_message: __("Numbering accounts... {0} done", [done]),
		callback(r) {
			const out = r.message || {};
			const applied = (out.applied || []).length;
			done += applied;
			if (!out.stopped && out.left && applied) {
				sf_number_batch(company, done);
				return;
			}
			let message = __("{0} account(s) numbered.", [done]);
			if (out.stopped) {
				message += `<br>${__("Stopped at {0}:", [frappe.utils.escape_html(out.stopped.account)])} ${frappe.utils.escape_html(
					out.stopped.reason
				)}`;
			}
			if (out.left) {
				message += `<br>${__("{0} left: run it again when the system is quiet.", [out.left])}`;
			}
			frappe.msgprint({
				title: __("Account Numbers"),
				indicator: out.stopped || out.left ? "orange" : "green",
				message: message,
			});
		},
	});
}
