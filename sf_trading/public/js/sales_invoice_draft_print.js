// Sales Invoice: no Print on a draft sale; a draft Sales Return still prints.
// The server refuses the print itself (sf_trading/draft_print.py); this only stops the form from
// offering it -- the Print icon, the menu entry and Ctrl+P.
frappe.ui.form.on("Sales Invoice", {
	setup(frm) {
		const core_print_doc = frm.print_doc.bind(frm);
		frm.print_doc = function () {
			if (sf_trading_draft_sale(frm)) {
				frappe.show_alert({
					message: __("A draft Sales Invoice cannot be printed. Submit it first."),
					indicator: "orange",
				});
				return;
			}
			return core_print_doc();
		};
	},

	refresh(frm) {
		sf_trading_toggle_draft_print(frm);
	},

	is_return(frm) {
		sf_trading_toggle_draft_print(frm);
	},
});

function sf_trading_draft_sale(frm) {
	return frm.doc.docstatus === 0 && !frm.doc.is_return;
}

function sf_trading_toggle_draft_print(frm) {
	const hide = sf_trading_draft_sale(frm);
	frm.toolbar && frm.toolbar.print_icon && frm.toolbar.print_icon.toggleClass("hide", hide);
	// the form's own menu entries carry no data-label; they are told apart by their label text
	frm.page.menu
		.find(".menu-item-label")
		.filter((_i, el) => $(el).text().trim() === __("Print"))
		.closest("li")
		.toggle(!hide);
}
