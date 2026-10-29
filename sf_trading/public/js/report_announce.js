// apps/sf_trading/sf_trading/public/js/report_announce.js
//
// Report form -> Actions -> "Announce this report". Opens a new SF Announcement (kind Report, with
// the Open button already pointing at this report) whose audience is exactly the roles that may run
// the report, so what is announced reaches the people who can use it -- and nobody who cannot.
// A report limited to no role is open to everyone, so the audience starts as Everyone.
//
// Only an Announcement Manager or System Manager sees the button. The announcement is a draft: the
// short message is left empty for the author to write, and nothing goes out until they publish.

frappe.ui.form.on("Report", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.disabled) return;
		if (!frappe.user.has_role("Announcement Manager") && !frappe.user.has_role("System Manager")) return;
		frm.add_custom_button(__("Announce this report"), () => sf_announce_report(frm), __("Actions"));
	},
});

function sf_announce_report(frm) {
	const roles = (frm.doc.roles || []).map((row) => row.role).filter(Boolean);

	frappe.model.with_doctype("SF Announcement", () => {
		const doc = frappe.model.get_new_doc("SF Announcement");
		doc.title = __("New report: {0}", [frm.doc.report_name || frm.doc.name]);
		doc.kind = "Report";
		doc.link_type = "Report";
		doc.link_report = frm.doc.name;
		doc.link_label = __("Open report");

		(roles.length ? roles : [null]).forEach((role) => {
			const row = frappe.model.add_child(doc, "SF Announcement Audience", "audience");
			row.audience_type = role ? "Role" : "Everyone";
			if (role) row.role = role;
		});
		if (!roles.length) {
			frappe.show_alert({ message: __("This report is not limited to any role, so the audience starts as Everyone."), indicator: "blue" });
		}
		frappe.set_route("Form", "SF Announcement", doc.name);
	});
}
