# sf_trading/sales_performance_block.py
"""The Custom HTML Block that turns the Sales Performance workspace into a dashboard.

A workspace can hold number cards, charts and shortcuts, but nothing that shows a table of who
is where against their target. `Custom HTML Block` fills that gap: frappe renders its html,
style and script inside the workspace, handing the script a `root_element` to draw into.

It reads the same `performance_snapshot` the board page uses, so the workspace, the board, the
reports and the cards can never disagree.
"""

import frappe

BLOCK = "SF Sales Performance Overview"

HTML = """<div class="sfws">
  <div class="sfws-strip"></div>
  <div class="sfws-cols">
    <div class="sfws-panel sfws-wide">
      <div class="sfws-title">Target vs actual by month</div>
      <div class="sfws-chart"></div>
    </div>
    <div class="sfws-panel">
      <div class="sfws-title">Branches</div>
      <div class="sfws-branches"></div>
    </div>
    <div class="sfws-panel">
      <div class="sfws-title">Sales people</div>
      <div class="sfws-people"></div>
    </div>
  </div>
  <div class="sfws-foot"></div>
</div>"""

STYLE = """
.sfws { padding: 2px 0 6px; }
.sfws-strip { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px;
  margin-bottom:12px; }
.sfws-tile { border:1px solid var(--border-color); border-left:3px solid #8d99a6; border-radius:8px;
  padding:10px 12px; background:var(--card-bg, var(--fg-color)); }
.sfws-tile.good { border-left-color:#29cd42; } .sfws-tile.warn { border-left-color:#f5a524; }
.sfws-tile.bad { border-left-color:#ff5858; }
.sfws-tile-label { font-size:11px; text-transform:uppercase; letter-spacing:.04em;
  color:var(--text-muted); }
.sfws-tile-value { font-size:18px; font-weight:650; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }
.sfws-tile-extra { font-size:11px; color:var(--text-muted); }
.sfws-cols { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }
.sfws-panel { border:1px solid var(--border-color); border-radius:8px; padding:12px;
  background:var(--card-bg, var(--fg-color)); overflow:hidden; }
.sfws-wide { grid-column: span 2; }
.sfws-title { font-size:11px; text-transform:uppercase; letter-spacing:.04em;
  color:var(--text-muted); margin-bottom:6px; }
.sfws-row { padding:6px 0; border-bottom:1px solid var(--border-color); }
.sfws-row:last-child { border-bottom:0; }
.sfws-row-top { display:flex; justify-content:space-between; gap:8px; font-size:13px; }
.sfws-row-name { font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.sfws-pct { font-weight:600; white-space:nowrap; }
.sfws-pct.good { color:#1f9d3a; } .sfws-pct.warn { color:#c67c06; } .sfws-pct.bad { color:#e03636; }
.sfws-bar { height:7px; border-radius:4px; background:var(--gray-200); overflow:hidden; margin:4px 0 2px; }
.sfws-bar > span { display:block; height:100%; background:#2490ef; }
.sfws-bar > span.good { background:#29cd42; } .sfws-bar > span.warn { background:#f5a524; }
.sfws-bar > span.bad { background:#ff5858; }
.sfws-amt { font-size:11px; color:var(--text-muted); }
.sfws-foot { font-size:11px; color:var(--text-muted); margin-top:8px; }
@media (max-width: 1200px) { .sfws-cols { grid-template-columns:1fr; } .sfws-wide { grid-column: span 1; } }
"""

SCRIPT = """
// frappe hands this script `root_element`; everything is drawn inside it.
const money = (v, c) => `<bdi dir="ltr">${format_currency(flt(v), c)}</bdi>`;
const pctText = (v) => (v === null || v === undefined ? "\\u2014" : `${flt(v, 1)}%`);
const tone = (v) => (v === null || v === undefined ? "" : v >= 100 ? "good" : v >= 80 ? "warn" : "bad");

function rows(list, cur) {
    if (!list.length) return `<div class="sfws-amt">${__("Nothing to show yet.")}</div>`;
    return list.map((r) => {
        const w = Math.max(0, Math.min(100, flt(r.pct || 0)));
        return `<div class="sfws-row">
            <div class="sfws-row-top">
                <span class="sfws-row-name">${frappe.utils.escape_html(r.name)}</span>
                <span class="sfws-pct ${tone(r.pct)}">${pctText(r.pct)}</span>
            </div>
            <div class="sfws-bar"><span class="${tone(r.pct)}" style="width:${w}%"></span></div>
            <div class="sfws-amt" dir="ltr">${money(r.actual, cur)} ${__("of")} ${money(r.target, cur)}</div>
        </div>`;
    }).join("");
}

frappe.call({ method: "sf_trading.sales_target.performance_snapshot" }).then((r) => {
    const d = r && r.message;
    if (!d) return;
    const cur = d.meta.currency, s = d.summary;

    const tile = (label, value, cls, extra) => `<div class="sfws-tile ${cls || ""}">
        <div class="sfws-tile-label">${label}</div>
        <div class="sfws-tile-value">${value}</div>
        <div class="sfws-tile-extra">${extra || ""}</div></div>`;

    root_element.querySelector(".sfws-strip").innerHTML =
        tile(__("This month"), money(s.mtd_actual, cur), tone(s.mtd_pct),
             `${__("target")} ${money(s.mtd_target, cur)}`) +
        tile(__("This month %"), pctText(s.mtd_pct), tone(s.mtd_pct),
             `${__("to date")} ${money(s.mtd_target_to_date, cur)}`) +
        tile(__("Year to date"), money(s.ytd_actual, cur), tone(s.ytd_pct),
             `${__("target")} ${money(s.ytd_target, cur)}`) +
        tile(__("Year to date %"), pctText(s.ytd_pct), tone(s.ytd_pct), "") +
        tile(s.variance >= 0 ? __("Ahead by") : __("Behind by"), money(Math.abs(s.variance), cur),
             s.variance >= 0 ? "good" : "bad", "") +
        tile(__("Top branch"), frappe.utils.escape_html(s.best_branch || "\\u2014"), "",
             `${__("best seller")}: ${frappe.utils.escape_html(s.best_person || "\\u2014")}`);

    root_element.querySelector(".sfws-branches").innerHTML = rows(d.branches, cur);
    root_element.querySelector(".sfws-people").innerHTML =
        rows(d.people.filter((p) => p.name !== "Unassigned").slice(0, 6), cur);

    const el = root_element.querySelector(".sfws-chart");
    if (el && typeof frappe.Chart !== "undefined" && (d.months || []).length) {
        new frappe.Chart(el, {
            data: {
                labels: d.months.map((m) => m.short),
                datasets: [
                    { name: __("Actual"), chartType: "bar", values: d.months.map((m) => flt(m.actual)) },
                    { name: __("Target"), chartType: "line", values: d.months.map((m) => flt(m.target)) },
                ],
            },
            type: "axis-mixed", height: 220, colors: ["#2490ef", "#ff5858"],
            tooltipOptions: { formatTooltipY: (v) => format_currency(v, cur) },
        });
    }

    const w = d.meta.window || [];
    root_element.querySelector(".sfws-foot").textContent = __(
        "{0} \\u00b7 {1} \\u00b7 {2} \\u00b7 year to date covers {3} \\u00b7 as on {4}",
        [d.meta.company, d.meta.fiscal_year, d.meta.basis,
         w.length ? `${w[0]}\\u2013${w[w.length - 1]}` : __("no target set"), d.meta.as_on]);
});
"""


def ensure_block():
	"""Create or refresh the block. The body is rewritten every migrate so fixes ship."""
	payload = {"html": HTML, "script": SCRIPT, "style": STYLE, "private": 0}
	if frappe.db.exists("Custom HTML Block", BLOCK):
		doc = frappe.get_doc("Custom HTML Block", BLOCK)
		doc.update(payload)
		doc.save(ignore_permissions=True)
		return doc

	doc = frappe.get_doc({"doctype": "Custom HTML Block", "name": BLOCK, **payload})
	for role in ("Accounts Manager", "Accounts User", "Sales Manager", "Sales User",
	             "Branch Head", "System Manager"):
		if frappe.db.exists("Role", role):
			doc.append("roles", {"role": role})
	doc.insert(ignore_permissions=True)
	return doc
