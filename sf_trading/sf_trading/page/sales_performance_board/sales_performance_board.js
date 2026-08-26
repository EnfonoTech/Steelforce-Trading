// apps/sf_trading/sf_trading/page/sales_performance_board/sales_performance_board.js
//
// Targets against actuals, drawn rather than tabulated. One call fills the page
// (sf_trading.sales_target.performance_snapshot) and every panel is read from that one payload,
// so no two things on screen can disagree — the same rule the Business Dashboard follows.
//
// The reports remain the place to audit a figure; this is the place to see where you stand.

frappe.pages["sales-performance-board"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Sales Performance"),
		single_column: true,
	});
	new SalesPerformance(page);
};

// BHD prints its symbol right-to-left, and two figures next to each other then swap places on
// screen. Each one is isolated so what is read is what is meant.
const SP_MONEY = (v, currency) => format_currency(flt(v), currency || frappe.boot.sysdefaults.currency);
const SP_FMT = (v, currency) => `<bdi dir="ltr">${SP_MONEY(v, currency)}</bdi>`;
const SP_PCT = (v) => (v === null || v === undefined ? "—" : `${flt(v, 1)}%`);
// green once the target is met, amber within reach, red when it is not
const SP_TONE = (pct) => (pct === null || pct === undefined ? "flat" : pct >= 100 ? "good" : pct >= 80 ? "warn" : "bad");

class SalesPerformance {
	constructor(page) {
		this.page = page;
		this.charts = {};
		this.filters = {};
		this._build_shell();
		this._build_filters();
		this.refresh();
	}

	_build_filters() {
		this.filters.company = this.page.add_field({
			fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			change: () => this.refresh(),
		});
		this.filters.fiscal_year = this.page.add_field({
			fieldname: "fiscal_year", label: __("Fiscal Year"), fieldtype: "Link",
			options: "Fiscal Year", default: frappe.defaults.get_user_default("fiscal_year"),
			change: () => this.refresh(),
		});
		this.filters.branch = this.page.add_field({
			fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch",
			change: () => this.refresh(),
		});
		this.filters.basis = this.page.add_field({
			fieldname: "basis", label: __("Measured On"), fieldtype: "Select",
			options: ["Net of VAT", "Gross"].join("\n"), default: "Net of VAT",
			change: () => this.refresh(),
		});
		this.page.set_primary_action(__("Set a Target"), () =>
			frappe.new_doc("Sales Target", { company: this._val("company") }));
		this.page.add_menu_item(__("Branch report"), () =>
			frappe.set_route("query-report", "Branch Sales Target vs Actual", this._report_filters()));
		this.page.add_menu_item(__("Sales person report"), () =>
			frappe.set_route("query-report", "Sales Person Target vs Actual", this._report_filters()));
		this.page.add_menu_item(__("Scorecard"), () =>
			frappe.set_route("query-report", "Sales Target Scorecard", this._report_filters()));
	}

	_report_filters() {
		return {
			company: this._val("company"), fiscal_year: this._val("fiscal_year"),
			basis: this._val("basis") || "Net of VAT", branch: this._val("branch"),
		};
	}

	_val(f) {
		const field = this.filters[f];
		return field && field.get_value ? field.get_value() : null;
	}

	_build_shell() {
		this.$body = $(`
			<div class="sfsp">
				<div class="sfsp-loading text-muted">${__("Loading…")}</div>
				<div class="sfsp-content" style="display:none">
					<div class="sfsp-hero"></div>
					<div class="sfsp-kpi"></div>
					<div class="sfsp-grid">
						<div class="sfsp-card sfsp-span2">
							<h6>${__("Target vs Actual by month")}</h6>
							<div class="sfsp-chart" id="sfsp-months"></div>
						</div>
						<div class="sfsp-card">
							<h6>${__("Achievement by branch")}</h6>
							<div class="sfsp-chart" id="sfsp-branch-pct"></div>
						</div>
					</div>
					<div class="sfsp-grid">
						<div class="sfsp-card"><h6>${__("Branches")}</h6><div class="sfsp-branches"></div></div>
						<div class="sfsp-card sfsp-span2"><h6>${__("Sales people")}</h6><div class="sfsp-people"></div></div>
					</div>
					<div class="sfsp-foot text-muted"></div>
				</div>
			</div>
		`).appendTo(this.page.main);
		this._inject_css();
	}

	refresh() {
		if (this._skip_next_refresh) {
			this._skip_next_refresh = false;
			return;
		}
		const $c = this.$body.find(".sfsp-content");
		const $l = this.$body.find(".sfsp-loading");
		$l.show().text(__("Loading…"));

		frappe.call({
			method: "sf_trading.sales_target.performance_snapshot",
			args: {
				company: this._val("company"), fiscal_year: this._val("fiscal_year"),
				basis: this._val("basis") || "Net of VAT", branch: this._val("branch"),
			},
			callback: (r) => {
				if (!r || !r.message) {
					$l.text(__("No data returned."));
					return;
				}
				this.data = r.message;
				try {
					this._render();
					$l.hide();
					$c.show();
				} catch (e) {
					// a visible failure beats a page that says Loading for ever
					console.error("Sales Performance render failed", e);
					$c.hide();
					$l.show().html(
						`<div class="text-danger">${__("The page loaded its data but could not draw it.")}</div>
						 <div class="text-muted small">${frappe.utils.escape_html(String((e && e.message) || e))}</div>`
					);
				}
			},
			error: () => $l.text(__("Could not load sales performance.")),
		});
	}

	_render() {
		const d = this.data;
		const cur = d.meta.currency;
		this._hero(d.summary, d.meta, cur);
		this._kpis(d.summary, cur);
		this._months(d.months, cur);
		this._branch_pct(d.branches);
		this._table(".sfsp-branches", d.branches, cur, __("No branch has sold anything yet."));
		this._table(".sfsp-people", d.people, cur, __("No sales person has sold anything yet."));
		// The server picks the year when the filter is blank; show which one it chose. Writing
		// the field fires its change handler, so the refetch it would cause is skipped -- the
		// data on screen already came from that very year.
		if (!this._val("fiscal_year") && d.meta.fiscal_year) {
			this._skip_next_refresh = true;
			this.filters.fiscal_year.set_value(d.meta.fiscal_year);
		}
		this.$body.find(".sfsp-foot").text(
			__("{0} · {1} · {2} · as on {3} · generated {4}", [
				d.meta.company, d.meta.fiscal_year, d.meta.basis, d.meta.as_on, d.meta.generated_on])
		);
	}

	_hero(s, meta, cur) {
		const pct = s.ytd_pct;
		const bar = Math.max(0, Math.min(100, flt(pct || 0)));
		const note = meta.has_targets
			? __("Year to date against target")
			: __("No target has been set yet — set one and this fills in.");
		const delta = s.ytd_target
			? `<span class="sfsp-delta ${s.variance >= 0 ? "up" : "down"}">${
				s.variance >= 0 ? "▲" : "▼"} ${SP_FMT(Math.abs(s.variance), cur)} ${
				s.variance >= 0 ? __("ahead") : __("behind")}</span>`
			: "";

		this.$body.find(".sfsp-hero").html(`
			<div class="sfsp-hero-inner ${SP_TONE(pct)}">
				<div class="sfsp-hero-main">
					<div class="sfsp-hero-label">${note}</div>
					<div class="sfsp-hero-value">${SP_FMT(s.ytd_actual, cur)}</div>
					<div class="sfsp-hero-sub" dir="ltr">${__("of")} <b>${SP_FMT(s.ytd_target, cur)}</b> ${delta}</div>
				</div>
				<div class="sfsp-hero-gauge">
					<div class="sfsp-gauge-pct">${SP_PCT(pct)}</div>
					<div class="sfsp-bar"><span style="width:${bar}%"></span></div>
					<div class="sfsp-gauge-note">${__("This month")}: <b>${SP_FMT(s.mtd_actual, cur)}</b>
						${__("of")} ${SP_FMT(s.mtd_target, cur)} (${SP_PCT(s.mtd_pct)})</div>
				</div>
			</div>`);
	}

	_kpis(s, cur) {
		const card = (label, value, tone, extra) => `
			<div class="sfsp-kpi-card ${tone || ""}">
				<div class="sfsp-kpi-label">${label}</div>
				<div class="sfsp-kpi-value">${value}</div>
				<div class="sfsp-kpi-extra">${extra || ""}</div>
			</div>`;
		this.$body.find(".sfsp-kpi").html(
			card(__("This month"), SP_FMT(s.mtd_actual, cur), SP_TONE(s.mtd_pct),
				`${__("target")} ${SP_FMT(s.mtd_target, cur)}`) +
			card(__("Month to date target"), SP_FMT(s.mtd_target_to_date, cur), "flat",
				__("prorated by days elapsed")) +
			card(__("Year to date"), SP_PCT(s.ytd_pct), SP_TONE(s.ytd_pct),
				`${SP_FMT(s.ytd_actual, cur)} ${__("of")} ${SP_FMT(s.ytd_target, cur)}`) +
			card(s.variance >= 0 ? __("Ahead by") : __("Behind by"),
				SP_FMT(Math.abs(s.variance), cur), s.variance >= 0 ? "good" : "bad", "") +
			card(__("Top branch"), frappe.utils.escape_html(s.best_branch || "—"), "flat", "") +
			card(__("Top sales person"), frappe.utils.escape_html(s.best_person || "—"), "flat", "")
		);
	}

	_chart(id, opts) {
		const el = this.$body.find("#" + id).get(0);
		if (!el) return;
		if (typeof frappe.Chart === "undefined") {
			$(el).html(`<p class="text-muted">${__("Charts are unavailable on this page.")}</p>`);
			return;
		}
		if (this.charts[id]) {
			this.charts[id].destroy && this.charts[id].destroy();
			$(el).empty();
		}
		this.charts[id] = new frappe.Chart(el, opts);
	}

	_months(months, cur) {
		if (!months || !months.length) return;
		// a bar per month for what was sold, a line over it for what was asked for
		this._chart("sfsp-months", {
			data: {
				labels: months.map((m) => m.short),
				datasets: [
					{ name: __("Actual"), chartType: "bar", values: months.map((m) => flt(m.actual)) },
					{ name: __("Target"), chartType: "line", values: months.map((m) => flt(m.target)) },
				],
			},
			type: "axis-mixed",
			height: 260,
			colors: ["#2490ef", "#ff5858"],
			axisOptions: { xIsSeries: false },
			tooltipOptions: { formatTooltipY: (v) => SP_FMT(v, cur) },
		});
	}

	_branch_pct(branches) {
		const rows = (branches || []).filter((b) => b.pct !== null && b.pct !== undefined);
		if (!rows.length) {
			this.$body.find("#sfsp-branch-pct").html(
				`<p class="text-muted">${__("Set branch targets to see achievement here.")}</p>`);
			return;
		}
		this._chart("sfsp-branch-pct", {
			data: {
				labels: rows.map((b) => b.name),
				datasets: [{ name: __("Achieved %"), values: rows.map((b) => flt(b.pct, 1)) }],
				yMarkers: [{ label: __("Target"), value: 100, options: { labelPos: "left" } }],
			},
			type: "bar",
			height: 260,
			colors: ["#29cd42"],
			tooltipOptions: { formatTooltipY: (v) => `${v}%` },
		});
	}

	_table(selector, rows, cur, empty) {
		if (!rows || !rows.length) {
			this.$body.find(selector).html(`<p class="text-muted">${empty}</p>`);
			return;
		}
		const body = rows.map((r) => {
			const pct = r.pct;
			const bar = Math.max(0, Math.min(100, flt(pct || 0)));
			// Stacked rather than four columns: a currency column of "550,341.398 د.ب" squeezed
			// the bar out of existence in the narrow card, and the bar is the point of the row.
			return `
				<div class="sfsp-row">
					<div class="sfsp-row-top">
						<span class="sfsp-row-name">${frappe.utils.escape_html(r.name)}</span>
						<span class="sfsp-row-pct ${SP_TONE(pct)}">${SP_PCT(pct)}</span>
					</div>
					<div class="sfsp-row-bar"><span class="${SP_TONE(pct)}" style="width:${bar}%"></span></div>
					<div class="sfsp-row-amt" dir="ltr">${SP_FMT(r.actual, cur)}
						<span class="text-muted">${__("of")} ${SP_FMT(r.target, cur)}</span></div>
				</div>`;
		}).join("");
		this.$body.find(selector).html(body);
	}

	_inject_css() {
		if (document.getElementById("sfsp-style")) return;
		$(`<style id="sfsp-style">
			.sfsp { padding: 4px 0 24px; }
			.sfsp-hero-inner { display:flex; gap:24px; flex-wrap:wrap; justify-content:space-between;
				border:1px solid var(--border-color); border-radius:10px; padding:20px 24px;
				background:var(--fg-color); margin-bottom:14px; }
			.sfsp-hero-label { color:var(--text-muted); font-size:12px; text-transform:uppercase;
				letter-spacing:.04em; }
			.sfsp-hero-value { font-size:34px; font-weight:700; line-height:1.2; }
			.sfsp-hero-sub { color:var(--text-muted); }
			.sfsp-delta { margin-left:10px; font-weight:600; }
			.sfsp-delta.up { color:#1f9d3a; } .sfsp-delta.down { color:#e03636; }
			.sfsp-hero-gauge { min-width:280px; flex:1; max-width:420px; }
			.sfsp-gauge-pct { font-size:26px; font-weight:700; text-align:right; }
			.sfsp-bar { height:10px; border-radius:6px; background:var(--gray-200); overflow:hidden; margin:6px 0; }
			.sfsp-bar > span { display:block; height:100%; background:#2490ef; }
			.sfsp-hero-inner.good .sfsp-bar > span { background:#29cd42; }
			.sfsp-hero-inner.warn .sfsp-bar > span { background:#f5a524; }
			.sfsp-hero-inner.bad  .sfsp-bar > span { background:#ff5858; }
			.sfsp-gauge-note { color:var(--text-muted); font-size:12px; text-align:right; }
			.sfsp-kpi { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
				gap:10px; margin-bottom:14px; }
			.sfsp-kpi-card { border:1px solid var(--border-color); border-left:3px solid var(--gray-400);
				border-radius:8px; padding:12px 14px; background:var(--fg-color); }
			.sfsp-kpi-card.good { border-left-color:#29cd42; }
			.sfsp-kpi-card.warn { border-left-color:#f5a524; }
			.sfsp-kpi-card.bad  { border-left-color:#ff5858; }
			.sfsp-kpi-label { color:var(--text-muted); font-size:11px; text-transform:uppercase;
				letter-spacing:.04em; }
			.sfsp-kpi-value { font-size:20px; font-weight:650; }
			.sfsp-kpi-extra { color:var(--text-muted); font-size:12px; }
			.sfsp-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:14px; }
			.sfsp-card { border:1px solid var(--border-color); border-radius:8px; padding:14px;
				background:var(--fg-color); overflow:hidden; }
			.sfsp-card h6 { color:var(--text-muted); text-transform:uppercase; font-size:11px;
				letter-spacing:.04em; margin-bottom:8px; }
			.sfsp-span2 { grid-column: span 2; }
			.sfsp-row { padding:8px 0; border-bottom:1px solid var(--border-color); }
			.sfsp-row:last-child { border-bottom:0; }
			.sfsp-row-top { display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
			.sfsp-row-name { font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
			.sfsp-row-bar { height:8px; border-radius:5px; background:var(--gray-200);
				overflow:hidden; margin:5px 0 3px; }
			.sfsp-row-bar > span { display:block; height:100%; background:#2490ef; }
			.sfsp-row-bar > span.good { background:#29cd42; }
			.sfsp-row-bar > span.warn { background:#f5a524; }
			.sfsp-row-bar > span.bad  { background:#ff5858; }
			.sfsp-row-pct { font-weight:600; white-space:nowrap; }
			.sfsp-row-pct.good { color:#1f9d3a; }
			.sfsp-row-pct.warn { color:#c67c06; }
			.sfsp-row-pct.bad  { color:#e03636; }
			.sfsp-row-amt { font-size:12px; color:var(--text-muted); }
			.sfsp-foot { font-size:11px; margin-top:6px; }
			@media (max-width: 992px) {
				.sfsp-grid { grid-template-columns:1fr; }
				.sfsp-span2 { grid-column: span 1; }
			}
		</style>`).appendTo(document.head);
	}
}
