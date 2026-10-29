// apps/sf_trading/sf_trading/sf_trading/doctype/sf_announcement/sf_announcement.js
//
// The form a manager writes an update in. The rules (who may see it, what a link may point to,
// how it is published) live on the server -- sf_trading/announcements.py. The buttons here only
// call it, and the two panels show what it answers: who the audience reaches before publishing,
// and who has read it afterwards.

const SF_ANN = "sf_trading.announcements.";

frappe.ui.form.on("SF Announcement", {
	setup(frm) {
		frm.set_query("link_report", () => ({ filters: { disabled: 0 } }));
	},

	refresh(frm) {
		const colour = { Draft: "orange", Scheduled: "blue", Published: "green", Expired: "gray", Archived: "gray" };
		if (!frm.is_new()) frm.page.set_indicator(__(frm.doc.status), colour[frm.doc.status] || "gray");

		frm.trigger("set_intro");
		frm.trigger("set_buttons");
		frm.trigger("preview_audience");
		frm.trigger("show_reach");
		frm.trigger("watch_summary");
	},

	// ── Simple English: a live hint under the short message as it is typed ─────────────
	watch_summary(frm) {
		const field = frm.fields_dict.summary;
		if (!field || !field.$input) return;
		if (!field._sf_hint_bound) {
			field._sf_hint_bound = true;
			field.$input.on("input", () => frm.events.readability(frm, field.$input.val()));
		}
		frm.events.readability(frm, frm.doc.summary);
	},

	readability(frm, text) {
		const field = frm.fields_dict.summary;
		// the description line only: set_df_property would redraw the field under the cursor
		if (field && field.set_description) field.set_description(sf_ann_hint(text));
	},

	summary(frm) {
		frm.events.readability(frm, frm.doc.summary);
	},

	// ── How it will look to a reader, before it is published ─────────────────────────
	preview(frm) {
		const A = window.sf_ann;
		if (!A) {
			frappe.msgprint(__("Reload the page, then try again."));
			return;
		}
		const d = frm.doc;
		const labels = (d.audience || []).map(sf_ann_row_label).filter(Boolean);
		let link = null;
		if (d.link_type) {
			const target = d.link_type === "Report" ? d.link_report : d.link_type === "DocType" ? d.link_doctype : d.link_url;
			link = { type: d.link_type, label: d.link_label || __("Open"), target, can_open: true, route: null, href: d.link_url };
		}
		// the message is cleaned by the server the same way it is for readers
		frappe.xcall(SF_ANN + "sanitize_preview", { html: d.details || "" }).then((details) => {
			const item = {
				name: d.name || "preview",
				title: d.title || __("(no title yet)"),
				summary: d.summary || "",
				details,
				kind: d.kind || "Announcement",
				priority: d.priority || "Normal",
				pinned: d.pinned ? 1 : 0,
				require_ack: d.require_ack ? 1 : 0,
				status: "Published",
				published_on: "now",
				posted_by: frappe.session.user_fullname,
				audience: labels.length ? labels.slice(0, 3).join(", ") : __("(nobody yet)"),
				is_read: false,
				acknowledged: false,
				link,
			};
			const dialog = new frappe.ui.Dialog({
				title: __("How people will see it"),
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "body" }],
			});
			dialog.fields_dict.body.$wrapper.html(`
				<div class="sf-hub">
					<div class="sf-feed">${A.card(item, { expanded: !!details })}</div>
					<p class="text-muted small" style="margin-top:12px">${__("This is a preview. The buttons do nothing here.")}${
						d.priority === "Urgent" || d.require_ack
							? " " + __("People will also get a pop-up until they close or confirm it.")
							: ""
					}</p>
				</div>`);
			dialog.show();
		});
	},

	set_intro(frm) {
		frm.set_intro("");
		if (frm.is_new() || frm.doc.status === "Draft") {
			frm.set_intro(__("Write the message, add who should see it, then press Publish. Use simple words."), "blue");
		} else if (frm.doc.status === "Scheduled") {
			frm.set_intro(__("Scheduled. It goes out by itself at {0}.", [frappe.datetime.str_to_user(frm.doc.publish_on)]), "blue");
		} else if (frm.doc.status === "Published") {
			frm.set_intro(__("Published. People see it in My Updates and in the megaphone at the top."), "green");
		} else {
			frm.set_intro(__("This update is {0}. People no longer see it.", [__(frm.doc.status)]), "grey");
		}
	},

	set_buttons(frm) {
		frm.add_custom_button(__("Preview"), () => frm.events.preview(frm));
		if (frm.is_new()) return;
		const status = frm.doc.status;
		const set_state = (state, message) => () =>
			frappe.confirm(message, () =>
				frappe.xcall(SF_ANN + "set_state", { announcement: frm.doc.name, state }).then(() => frm.reload_doc())
			);

		if (["Draft", "Scheduled"].includes(status)) {
			frm.add_custom_button(__("Publish"), () => frm.events.publish(frm)).addClass("btn-primary");
		}
		if (status === "Published") {
			frm.add_custom_button(__("Remind people who have not read it"), () => frm.events.remind(frm), __("Actions"));
			frm.add_custom_button(__("Open in My Updates"), () => window.sf_ann && window.sf_ann.open_hub(frm.doc.name), __("Actions"));
		}
		if (["Scheduled", "Published", "Expired"].includes(status)) {
			frm.add_custom_button(
				__("Take down"),
				set_state("Archived", __("Take this update down? People will no longer see it.")),
				__("Actions")
			);
		}
		if (["Scheduled", "Expired", "Archived"].includes(status)) {
			frm.add_custom_button(
				__("Back to draft"),
				set_state("Draft", __("Turn this back into a draft? You can edit it and send it again.")),
				__("Actions")
			);
		}
	},

	// ── Starting points: a rule must be confirmed, a report update points at a report ────
	kind(frm) {
		if (!frm.is_new()) return;
		const d = frm.doc;
		if (d.kind === "Rule or Policy") frm.set_value("require_ack", 1);
		if (d.kind === "Report" && !d.link_type) frm.set_value("link_type", "Report");
		if (d.kind === "Maintenance" && d.priority === "Normal") frm.set_value("priority", "Important");
	},

	link_type(frm) {
		frm.trigger("preview_audience");
	},
	link_report(frm) {
		frm.trigger("preview_audience");
	},
	audience_add(frm) {
		frm.trigger("preview_audience");
	},
	audience_remove(frm) {
		frm.trigger("preview_audience");
	},

	// ── Publishing ───────────────────────────────────────────────────────────────────
	publish(frm) {
		const run = () =>
			frappe.xcall(SF_ANN + "preview_audience", { rows: JSON.stringify(sf_ann_rows(frm)) }).then((preview) => {
				const when = frm.doc.publish_on && moment(frm.doc.publish_on).isAfter(moment());
				const message = when
					? __("Schedule this for {0} people at {1}?", [preview.count, frappe.datetime.str_to_user(frm.doc.publish_on)])
					: __("Send this to {0} people now?", [preview.count]);
				frappe.confirm(message, () =>
					frappe.xcall(SF_ANN + "publish", { announcement: frm.doc.name }).then((result) => {
						frappe.show_alert({
							message:
								result.status === "Scheduled"
									? __("Scheduled for {0} people", [result.audience])
									: __("Published to {0} people", [result.audience]),
							indicator: "green",
						});
						frm.reload_doc();
					})
				);
			});
		if (frm.is_dirty()) frm.save().then(run);
		else run();
	},

	remind(frm) {
		frappe.confirm(__("Ring the bell again for everyone who has not read (or confirmed) this?"), () =>
			frappe.xcall(SF_ANN + "remind_pending", { announcement: frm.doc.name }).then((r) => {
				frappe.show_alert({ message: __("Reminder sent to {0} people", [r.reminded]), indicator: "green" });
				frm.reload_doc();
			})
		);
	},

	// ── Who it reaches, before it goes out ───────────────────────────────────────────
	preview_audience(frm) {
		clearTimeout(frm._sf_preview);
		frm._sf_preview = setTimeout(() => {
			const $box = frm.fields_dict.audience_preview.$wrapper;
			const rows = sf_ann_rows(frm);
			if (!rows.length) {
				$box.html(`<p class="text-muted">${__("Nobody yet. Add a line above.")}</p>`);
				return;
			}
			frappe
				.xcall(SF_ANN + "preview_audience", {
					rows: JSON.stringify(rows),
					link_type: frm.doc.link_type,
					link_report: frm.doc.link_report,
				})
				.then((r) => {
					const esc = frappe.utils.escape_html;
					const names = r.sample.map((p) => esc(p.full_name)).join(", ");
					const more = r.count > r.sample.length ? __(" and {0} more", [r.count - r.sample.length]) : "";
					let html = r.count
						? `<div class="alert alert-info" style="margin:0 0 8px"><b>${__("{0} people", [r.count])}</b> ${__("will see this.")}
							<div class="small text-muted" style="margin-top:4px">${names}${esc(more)}</div></div>`
						: `<div class="alert alert-warning" style="margin:0 0 8px">${__("Nobody is in this audience yet.")}</div>`;
					const gap = r.no_report_access;
					if (gap && gap.count) {
						html += `<div class="alert alert-warning" style="margin:0">
							<b>${__("{0} of them cannot open the report.", [gap.count])}</b>
							${__("They will see the update without the Open button.")}
							<div class="small" style="margin-top:4px">${esc(gap.sample.join(", "))}${gap.count > gap.sample.length ? "..." : ""}</div>
							<div class="small text-muted">${__("The report is limited to: {0}", [esc((gap.roles || []).join(", "))])}</div></div>`;
					}
					$box.html(html);
				});
		}, 350);
	},

	// ── Who has read it, once it is out ──────────────────────────────────────────────
	show_reach(frm) {
		const live = !frm.is_new() && frm.doc.status === "Published";
		frm.toggle_display("section_reach", live);
		if (!live || !window.sf_ann) return;
		frappe.xcall(SF_ANN + "get_reach", { announcement: frm.doc.name }).then((r) => {
			const esc = frappe.utils.escape_html;
			const pending = r.pending
				.map(
					(p) => `<div class="sf-dlg-person"><span>${esc(p.full_name)}</span>
						<span class="text-muted">${esc(p.seen ? __("Read, not confirmed") : __("Not read yet"))}</span></div>`
				)
				.join("");
			frm.fields_dict.reach_html.$wrapper.html(`
				<div class="sf-dlg sf-k-announcement">
					${window.sf_ann.reach_bars({ total: r.total, seen: r.seen, acknowledged: r.acknowledged }, r.needs_ack)}
					<p class="text-muted" style="margin:12px 0 0">${
						r.pending_total
							? esc(__("{0} still to {1}:", [r.pending_total, r.needs_ack ? __("read and confirm") : __("read")]))
							: esc(__("Everyone has done it. Nothing is waiting."))
					}</p>
					${pending ? `<div class="sf-dlg-pending">${pending}</div>` : ""}
				</div>`);
		});
	},
});

frappe.ui.form.on("SF Announcement Audience", {
	audience_type: (frm) => frm.trigger("preview_audience"),
	role: (frm) => frm.trigger("preview_audience"),
	department: (frm) => frm.trigger("preview_audience"),
	include_sub_departments: (frm) => frm.trigger("preview_audience"),
	branch: (frm) => frm.trigger("preview_audience"),
	user: (frm) => frm.trigger("preview_audience"),
});

// words a manager is likely to write that a plainer one replaces
const SF_ANN_PLAIN = {
	"utilize": "use", "commence": "start", "terminate": "end", "facilitate": "help", "prior to": "before",
	"in order to": "to", "with regard to": "about", "ascertain": "find out", "endeavour": "try", "assist": "help",
	"approximately": "about", "additional": "more", "purchase": "buy", "subsequently": "later",
	"regarding": "about", "inform": "tell", "require": "need", "obtain": "get",
};

function sf_ann_hint(text) {
	const base = __("One or two short lines. This is what people see on the card.");
	text = (text || "").trim();
	if (!text) return base;

	const count = (s) => (s.match(/\S+/g) || []).length;
	const longest = Math.max(0, ...text.split(/[.!?\n]+/).map(count));
	const tips = [];
	if (text.length > 400) tips.push(__("It is {0} characters. The most allowed is 400: put the rest in the full message.", [text.length]));
	if (longest > 20) tips.push(__("One sentence has {0} words. Try to keep each sentence under 20.", [longest]));
	const lower = text.toLowerCase();
	Object.keys(SF_ANN_PLAIN).forEach((word) => {
		if (new RegExp("\\b" + word + "\\b").test(lower)) tips.push(__("Say \"{0}\" instead of \"{1}\".", [SF_ANN_PLAIN[word], word]));
	});
	return __("{0} words.", [count(text)]) + " " + (tips.length ? tips.slice(0, 3).join(" ") : __("Reads simply. Good."));
}

function sf_ann_row_label(row) {
	return (
		{
			Everyone: __("Everyone"),
			Role: row.role,
			Department: row.department ? __("{0} department", [row.department]) : "",
			Branch: row.branch ? __("{0} branch", [row.branch]) : "",
			User: row.user,
		}[row.audience_type] || ""
	);
}

function sf_ann_rows(frm) {
	return (frm.doc.audience || []).map((row) => ({
		audience_type: row.audience_type,
		role: row.role,
		department: row.department,
		include_sub_departments: row.include_sub_departments,
		branch: row.branch,
		user: row.user,
	}));
}
