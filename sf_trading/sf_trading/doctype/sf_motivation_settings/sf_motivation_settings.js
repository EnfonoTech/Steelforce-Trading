// apps/sf_trading/sf_trading/sf_trading/doctype/sf_motivation_settings/sf_motivation_settings.js
//
// The daily motivation switches. "Show today's card now" draws the card the way the person will see
// it on their first desk load of the day -- from what is saved, so save first after a change.

frappe.ui.form.on("SF Motivation Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Show today's card now"), () => {
			frappe.xcall("sf_trading.motivation.get_motivation").then((m) => {
				if (!m) {
					frappe.msgprint(
						__("There is nothing to show yet. Add a message in Daily Motivation, and make sure the switch above is on and saved.")
					);
					return;
				}
				if (window.sf_ann) window.sf_ann.show_motivation(m, { preview: true });
			});
		});
		frm.add_custom_button(__("Open the message list"), () => frappe.set_route("List", "SF Motivation"));
	},
});
