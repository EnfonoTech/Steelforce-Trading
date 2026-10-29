// apps/sf_trading/sf_trading/sf_trading/doctype/sf_motivation/sf_motivation_list.js
//
// The message bank. Two shortcuts to fill it fast: paste many lines at once, or start from twelve
// plain starter messages. Lines already in the bank are skipped, so pasting twice does no harm.

const SF_MOTIVATION_STARTERS = [
	"Small steps every day add up to big results.",
	"Do it right the first time. It saves everyone's time.",
	"A kind word to a customer can win the whole year.",
	"Check your work once more. Good work shows.",
	"Be on time. It tells people they matter.",
	"Ask for help early. Teams win together.",
	"Today's work is tomorrow's trust.",
	"Listen first. Then answer.",
	"Clean data today means fewer problems tomorrow.",
	"Make one thing better today.",
	"Thank someone today.",
	"Hard work beats waiting for luck.",
];

function sf_motivation_add(listview, lines) {
	return frappe.xcall("sf_trading.motivation.add_motivations", { lines }).then((r) => {
		const parts = [__("{0} added", [r.added])];
		if (r.duplicates) parts.push(__("{0} already there", [r.duplicates]));
		if (r.too_long) parts.push(__("{0} too long (280 characters at most)", [r.too_long]));
		frappe.show_alert({ message: parts.join(", "), indicator: r.added ? "green" : "orange" });
		listview.refresh();
	});
}

frappe.listview_settings["SF Motivation"] = {
	add_fields: ["enabled", "show_on"],

	get_indicator(doc) {
		return doc.enabled ? [__("On"), "green", "enabled,=,1"] : [__("Off"), "gray", "enabled,=,0"];
	},

	onload(listview) {
		listview.page.add_inner_button(__("Add many messages"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Add many messages"),
				fields: [
					{
						fieldtype: "HTML",
						options: `<p class="text-muted">${__("Write one message on each line. To say who said it, add a dash and the name after the message, like: Small steps add up. — Name")}</p>`,
					},
					{ fieldtype: "Small Text", fieldname: "lines", label: __("Messages"), reqd: 1 },
				],
				primary_action_label: __("Add"),
				primary_action(values) {
					sf_motivation_add(listview, values.lines).then(() => dialog.hide());
				},
			});
			dialog.show();
		});

		listview.page.add_inner_button(__("Add 12 starter messages"), () => {
			frappe.confirm(__("Add 12 simple starter messages? Any you already have are skipped."), () =>
				sf_motivation_add(listview, SF_MOTIVATION_STARTERS.join("\n"))
			);
		});
	},
};
