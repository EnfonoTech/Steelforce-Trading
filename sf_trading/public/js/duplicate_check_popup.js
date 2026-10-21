// sf_trading/public/js/duplicate_check_popup.js
// GS Issue 15: warn while typing, on every master -- Item, Customer, Supplier, ledger (Account), the
// groups and trees (Item / Customer / Supplier Group, Warehouse, Cost Center, Territory, Sales Person),
// Brand, UOM and Employee; on the full form, the Quick Entry popup and the tree view's "Add Child" dialog.
//
// Non-blocking: shows what already exists, does not stop the save. The real uniqueness rule is
// server-side (party_completeness.py); this is UX so a duplicate is spotted before it is typed in
// full, not just rejected after.
//
// Shown only while SF Trading Settings > "Show Similar Names While Creating Masters" is ticked: the
// server answers an empty list otherwise (api/duplicate_check.py). The matches are listed under the
// name field as it is typed -- like the customer search on a Sales Invoice -- each a link to open it.
(function () {
	// Keep in step with _MASTERS in api/duplicate_check.py.
	const FIELD_BY_DOCTYPE = {
		Item: "item_name",
		Customer: "customer_name",
		Supplier: "supplier_name",
		Account: "account_name",
		"Item Group": "item_group_name",
		"Customer Group": "customer_group_name",
		"Supplier Group": "supplier_group_name",
		Warehouse: "warehouse_name",
		"Cost Center": "cost_center_name",
		Territory: "territory_name",
		"Sales Person": "sales_person_name",
		Brand: "brand",
		UOM: "uom_name",
	};
	// An Employee's name is typed in parts and joined into employee_name on save: those parts are read
	// together, and the list hangs under the first of them.
	const NAME_PARTS_BY_DOCTYPE = {
		Employee: ["first_name", "middle_name", "last_name"],
	};
	const DEBOUNCE_MS = 350;
	const timers = {};

	// A "field" here is any control with a $wrapper and an $input -- the one on the full form, or
	// the one in the Quick Entry dialog (where most masters are actually created from).
	function box(field) {
		let $box = field.$wrapper.find(".sf-similar-masters");
		if (!$box.length) $box = $(`<div class="sf-similar-masters" style="margin-top: 6px"></div>`).appendTo(field.$wrapper);
		return $box;
	}

	function show_matches(field, doctype, matches) {
		const $box = box(field);
		if (!matches || !matches.length) {
			$box.empty();
			return;
		}
		const esc = frappe.utils.escape_html;
		const route = frappe.router.slug(doctype);
		const items = matches
			.map(
				(m) => `<li style="padding: 2px 0">
					<a href="/app/${route}/${encodeURIComponent(m.name)}" target="_blank">${esc(m.label || m.name)}</a>
					${m.label && m.label !== m.name ? `<span class="text-muted small"> · ${esc(m.name)}</span>` : ""}
					${m.group ? `<span class="text-muted small"> · ${esc(m.group)}</span>` : ""}
					${m.disabled ? `<span class="indicator-pill red small" style="margin-left: 4px">${__("Disabled")}</span>` : ""}
				</li>`
			)
			.join("");
		$box.html(`
			<div style="border: 1px solid var(--yellow-300, #f5d68a); background: var(--yellow-50, #fffcef); border-radius: 6px; padding: 8px 10px">
				<div class="small" style="font-weight: 600; margin-bottom: 4px">
					${__("Existing {0} records with a similar name. Check before creating a new one:", [__(doctype)])}
				</div>
				<ul style="margin: 0; padding-left: 18px">${items}</ul>
			</div>`);
	}

	// `field` hosts the list; `read` gives what has been typed (its own input unless the name is in parts)
	function lookup(field, doctype, read) {
		clearTimeout(timers[doctype]);
		const value = read();
		if (value.length < 3) {
			box(field).empty();
			return;
		}
		timers[doctype] = setTimeout(() => {
			frappe.call({
				method: "sf_trading.api.duplicate_check.similar_records",
				args: { doctype: doctype, value: value },
				callback: (r) => {
					// typed on since: an older answer must not overwrite a newer one
					if (read() === value) show_matches(field, doctype, r.message);
				},
			});
		}, DEBOUNCE_MS);
	}

	// as the name is typed, not only when the field is left: the point is to see the match first
	function bind(field, doctype, host = field, read = () => (field.$input.val() || "").trim()) {
		if (!field || !field.$input || field.$input.data("sf_similar_bound")) return;
		field.$input.data("sf_similar_bound", true);
		field.$input.on("input", () => lookup(host, doctype, read));
	}

	Object.keys(FIELD_BY_DOCTYPE).forEach((doctype) => {
		const fieldname = FIELD_BY_DOCTYPE[doctype];
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				const field = frm.fields_dict[fieldname];
				if (!field || !field.$input) return;
				// Editing an existing record isn't where a duplicate gets created -- only warn
				// on a new one being typed in.
				if (!frm.doc.__islocal) {
					box(field).empty();
					return;
				}
				bind(field, doctype);
			},
		});
	});

	Object.keys(NAME_PARTS_BY_DOCTYPE).forEach((doctype) => {
		const parts = NAME_PARTS_BY_DOCTYPE[doctype];
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				const fields = parts.map((f) => frm.fields_dict[f]).filter((f) => f && f.$input);
				if (!fields.length) return;
				const host = fields[0];
				if (!frm.doc.__islocal) {
					box(host).empty();
					return;
				}
				const read = () =>
					fields
						.map((f) => (f.$input.val() || "").trim())
						.filter(Boolean)
						.join(" ");
				fields.forEach((f) => bind(f, doctype, host, read));
			},
		});
	});

	// Quick Entry ("New Customer" popup from a list or a link field). The Customer / Supplier popups
	// are subclasses that call super.render_dialog(), so wrapping the base one covers them all.
	const QuickEntryForm = frappe.ui.form.QuickEntryForm;
	if (QuickEntryForm && !QuickEntryForm.prototype.__sf_similar_wrapped) {
		const render_dialog = QuickEntryForm.prototype.render_dialog;
		QuickEntryForm.prototype.render_dialog = function () {
			const out = render_dialog.apply(this, arguments);
			const fieldname = FIELD_BY_DOCTYPE[this.doctype];
			if (fieldname && this.dialog) bind(this.dialog.fields_dict[fieldname], this.doctype);
			return out;
		};
		QuickEntryForm.prototype.__sf_similar_wrapped = true;
	}

	// Tree view "Add Child" dialog -- how Item Group, Cost Center, Warehouse, Territory, Sales Person and
	// the Chart of Accounts get their new nodes. new_node() keeps the dialog in a local and cur_dialog
	// is only set once its open animation ends, so the dialog is caught as new_node() shows it.
	const TreeView = frappe.views && frappe.views.TreeView;
	if (TreeView && !TreeView.prototype.__sf_similar_wrapped) {
		const new_node = TreeView.prototype.new_node;
		TreeView.prototype.new_node = function () {
			const doctype = this.doctype;
			const fieldname = FIELD_BY_DOCTYPE[doctype];
			if (!fieldname) return new_node.apply(this, arguments);
			const show = frappe.ui.Dialog.prototype.show;
			frappe.ui.Dialog.prototype.show = function () {
				const out = show.apply(this, arguments);
				bind(this.fields_dict[fieldname], doctype);
				return out;
			};
			try {
				return new_node.apply(this, arguments);
			} finally {
				frappe.ui.Dialog.prototype.show = show;
			}
		};
		TreeView.prototype.__sf_similar_wrapped = true;
	}
})();
