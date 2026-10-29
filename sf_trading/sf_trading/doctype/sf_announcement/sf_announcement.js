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
