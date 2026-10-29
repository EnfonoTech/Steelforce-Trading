// apps/sf_trading/sf_trading/sf_trading/doctype/sf_motivation/sf_motivation.js
//
// One line of daily motivation. "Preview" draws the card as people will see it.

frappe.ui.form.on("SF Motivation", {
	refresh(frm) {
		frm.add_custom_button(__("Preview"), () => {
			if (!window.sf_ann) {
				frappe.msgprint(__("Reload the page, then try again."));
				return;
			}
			window.sf_ann.show_motivation(
				{
					name: frm.doc.name,
					message: frm.doc.message || __("(write the message first)"),
					author: frm.doc.author,
					title: __("Thought for today"),
				},
				{ preview: true }
			);
		});
	},
});
