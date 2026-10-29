// apps/sf_trading/sf_trading/public/js/announcements.js
//
// Updates and announcements, on every desk page:
//   * the megaphone in the navbar, with its badge and a drop-down of the latest updates,
//   * the must-read pop-up (an Urgent update, or one that needs the person's confirmation),
//   * a toast the moment something new is published,
//   * and `window.sf_ann`, the card renderer the Updates Hub page (page/sf_updates) draws with.
//
// Who may see what is decided on the server (sf_trading/announcements.py); this file only shows
// what that returns. A report the person may not open arrives without a button, so there is no
// permission logic here to get wrong.

(function () {
	"use strict";

	const API = "sf_trading.announcements.";
	const HUB = "sf-updates";
	const LATER_KEY = "sf_ann_later";

	const esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));

	// ── Icons (24px grid, drawn with the current text colour) ──────────────────────────
	const ICONS = {
		megaphone: '<path d="M3 11v2a1 1 0 0 0 1 1h2l5 4V6L6 10H4a1 1 0 0 0-1 1z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>',
		sparkles: '<path d="M11 3l1.8 4.7L17.5 9.5l-4.7 1.8L11 16l-1.8-4.7L4.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
		shield: '<path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
		chart: '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M22 20H2"/>',
		wrench: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/>',
		pin: '<path d="M12 17v5"/><path d="M9 3h6l-1 6 3 3H7l3-3z"/>',
		check: '<path d="M5 12l5 5L20 7"/>',
		bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10 21a2 2 0 0 0 4 0"/>',
		arrow: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>',
		lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
		users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.5-3.5 3-5.5 6.5-5.5s6 2 6.5 5.5"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14.5c2.5 0 4.2 1.5 4.5 4"/>',
		inbox: '<path d="M3 13l3-8h12l3 8"/><path d="M3 13v6a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-6h-5l-1 2h-6l-1-2z"/>',
		clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
		alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/>',
		sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"/>',
	};
	const svg = (name) => `<svg class="sf-ico" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

	// ── Kinds ──────────────────────────────────────────────────────────────────────────
	const KIND_LIST = ["Announcement", "New Feature", "Rule or Policy", "Report", "Maintenance"];
	function kind_meta(kind) {
		const metas = {
			"Announcement": { cls: "announcement", icon: "megaphone", label: __("Announcement"), plural: __("Announcements") },
			"New Feature": { cls: "feature", icon: "sparkles", label: __("New Feature"), plural: __("New features") },
			"Rule or Policy": { cls: "rule", icon: "shield", label: __("Rule"), plural: __("Rules") },
			"Report": { cls: "report", icon: "chart", label: __("Report"), plural: __("Reports") },
			"Maintenance": { cls: "maintenance", icon: "wrench", label: __("Maintenance"), plural: __("Maintenance") },
		};
		return metas[kind] || metas["Announcement"];
	}

	// ── Small helpers ──────────────────────────────────────────────────────────────────
	function when(timestamp) {
		if (!timestamp) return "";
		// the form's preview has no publish time yet
		if (timestamp === "now") return __("just now");
		try {
			const m = moment.tz ? moment.tz(timestamp, frappe.boot.time_zone.system) : moment(timestamp);
			return m.fromNow();
		} catch (e) {
			return String(timestamp).slice(0, 10);
		}
	}

	function on_date(timestamp) {
		try {
			return frappe.datetime.str_to_user(String(timestamp).slice(0, 10), false, true);
		} catch (e) {
			return String(timestamp).slice(0, 10);
		}
	}

	const call = (method, args) => frappe.xcall(API + method, args || {});

	// what the server last said about this person's counters
	const state = { counts: { unread: 0, need_ack: 0, urgent: 0, total: 0, can_post: false } };

	function set_counts(counts) {
		if (!counts) return;
		Object.assign(state.counts, counts);
		paint_badge();
		$(document).trigger("sf_ann:counts", [state.counts]);
	}

	// open what an update points to: a route in the desk, or an address
	function go(link) {
		if (!link || !link.can_open) return;
		if (link.route) {
			frappe.set_route(...link.route);
		} else if (link.href && link.href.startsWith("/app/") && !link.href.includes("?")) {
			frappe.set_route(...link.href.slice(5).split("/").filter(Boolean).map(decodeURIComponent));
		} else if (link.href && link.href.startsWith("/")) {
			window.location.assign(link.href);
		} else if (link.href) {
			window.open(link.href, "_blank", "noopener");
		}
	}

	function open_hub(name) {
		frappe.route_options = name ? { open: name } : null;
		frappe.set_route(HUB);
	}

	// ── The card ───────────────────────────────────────────────────────────────────────
	// opts: { posts: manager's "posted by us" view, expanded: details open }
	function card(item, opts = {}) {
		const k = kind_meta(item.kind);
		const live = item.status === "Published";
		// "posted by us" is the author's view of the post, not their own reading list: no unread dot, no confirm button
		const unread = live && !item.is_read && !opts.posts;
		const needs_ack = live && item.require_ack && !item.acknowledged && !opts.posts;

		const badges = [];
		if (item.priority === "Urgent") badges.push(`<span class="sf-badge is-urgent">${esc(__("Urgent"))}</span>`);
		else if (item.priority === "Important") badges.push(`<span class="sf-badge is-important">${esc(__("Important"))}</span>`);
		if (item.pinned) badges.push(`<span class="sf-badge is-ack">${svg("pin")} ${esc(__("Pinned"))}</span>`);
		if (needs_ack) badges.push(`<span class="sf-badge is-ack">${svg("check")} ${esc(__("Please confirm"))}</span>`);
		if (opts.posts) {
			badges.push(`<span class="sf-badge is-status is-${esc(String(item.status).toLowerCase())}">${esc(__(item.status))}</span>`);
		}

		const actions = [];
		if (item.details) {
			actions.push(`<button class="sf-btn" data-act="toggle">${esc(opts.expanded ? __("Show less") : __("Read more"))}</button>`);
		}
		const link = item.link;
		if (link && live) {
			if (link.can_open) {
				const primary = needs_ack ? "" : " is-primary";
				actions.push(`<button class="sf-btn${primary}" data-act="open">${esc(link.label)} ${svg("arrow")}</button>`);
			} else {
				const what = link.type === "Report" ? __("this report") : __("this page");
				actions.push(`<span class="sf-locked">${svg("lock")} ${esc(__("You do not have access to {0}. Ask your manager if you need it.", [what]))}</span>`);
			}
		}
		if (live && item.require_ack && !opts.posts) {
			actions.push(
				item.acknowledged
					? `<span class="sf-done">${svg("check")} ${esc(__("You confirmed this on {0}", [on_date(item.acknowledged_on)]))}</span>`
					: `<button class="sf-btn is-primary" data-act="ack">${svg("check")} ${esc(__("I have read and understood"))}</button>`
			);
		} else if (unread && !item.details && !opts.posts) {
			actions.push(`<button class="sf-btn" data-act="read">${esc(__("Mark as read"))}</button>`);
		}
		if (opts.posts) {
			actions.push(`<button class="sf-btn" data-act="edit">${esc(__("Edit"))}</button>`);
			if (live) actions.push(`<button class="sf-btn" data-act="reach">${svg("users")} ${esc(__("Who has read it"))}</button>`);
		}

		return `
			<article class="sf-card sf-k-${k.cls}${live && item.priority === "Urgent" ? " sf-p-urgent" : ""}${unread ? " is-unread" : ""}${opts.expanded ? " is-open" : ""}${!live && !opts.posts ? " is-off" : ""}"
				data-name="${esc(item.name)}" data-link="${esc(link ? JSON.stringify(link) : "")}">
				<div class="sf-card-rail"><span>${esc(live && item.priority === "Urgent" ? __("Urgent") : k.label)}</span></div>
				<div class="sf-card-main">
					<div class="sf-card-top">
						${badges.join("")}
						${unread ? '<span class="sf-dot" title="' + esc(__("New")) + '"></span>' : ""}
						<span class="sf-card-time" title="${esc(item.published_on || "")}">${esc(when(item.published_on))}</span>
					</div>
					<h3 class="sf-card-title">${esc(item.title)}</h3>
					<p class="sf-card-summary">${esc(item.summary)}</p>
					${item.details ? `<div class="sf-card-details"${opts.expanded ? "" : " hidden"}>${item.details}</div>` : ""}
					<div class="sf-card-meta">
						<span>${esc(__("Posted by {0}", [item.posted_by]))}</span>
						${item.audience ? `<span>${svg("users")} ${esc(item.audience)}</span>` : ""}
					</div>
					${opts.posts && item.reach ? reach_bars(item.reach, item.require_ack) : ""}
					${actions.length ? `<div class="sf-card-actions">${actions.join("")}</div>` : ""}
				</div>
			</article>`;
	}

	function reach_bars(reach, needs_ack) {
		const pct = (n) => (reach.total ? Math.round((n * 100) / reach.total) : 0);
		const row = (label, n) => `
			<div class="sf-reach-row">
				<span class="sf-reach-label">${esc(label)}</span>
				<span class="sf-bar"><i style="width:${pct(n)}%"></i></span>
				<span class="sf-reach-num">${n} / ${reach.total}</span>
			</div>`;
		return `<div class="sf-reach">${row(__("Have read it"), reach.seen)}${needs_ack ? row(__("Confirmed"), reach.acknowledged) : ""}</div>`;
	}

	// ── Card actions: one delegated handler per container ──────────────────────────────
	// ctx: { opts: card options for the container, on_change(counts) }
	function wire($root, ctx = {}) {
		const opts = ctx.opts || {};

		function refresh($card, extra) {
			const name = $card.data("name");
			return call("get_one", { announcement: name }).then((item) => {
				if (!item) return;
				const expanded = $card.hasClass("is-open");
				const $new = $(card(item, Object.assign({}, opts, { expanded }, extra || {})));
				$card.replaceWith($new);
				if (ctx.on_change) ctx.on_change(state.counts);
			});
		}

		function mark_read($card) {
			return call("mark_read", { names: JSON.stringify([$card.data("name")]) }).then((counts) => {
				set_counts(counts);
				return refresh($card);
			});
		}

		$root.off("click.sfann").on("click.sfann", "[data-act]", function (event) {
			event.preventDefault();
			const $btn = $(this);
			const $card = $btn.closest(".sf-card");
			const name = $card.data("name");
			const act = $btn.data("act");

			if (act === "toggle") {
				const $details = $card.find(".sf-card-details");
				const opening = $details.prop("hidden");
				$details.prop("hidden", !opening);
				$card.toggleClass("is-open", opening);
				$btn.text(opening ? __("Show less") : __("Read more"));
				if (opening && $card.hasClass("is-unread")) mark_read($card);
			} else if (act === "open") {
				go($card.data("link"));
				if ($card.hasClass("is-unread")) call("mark_read", { names: JSON.stringify([name]) }).then(set_counts);
			} else if (act === "ack") {
				$btn.prop("disabled", true);
				call("acknowledge", { announcement: name })
					.then((counts) => {
						set_counts(counts);
						return refresh($card);
					})
					.catch(() => $btn.prop("disabled", false));
			} else if (act === "read") {
				mark_read($card);
			} else if (act === "edit") {
				frappe.set_route("Form", "SF Announcement", name);
			} else if (act === "reach") {
				reach_dialog(name, $card.find(".sf-card-title").text(), () => refresh($card));
			}
		});
	}

	// ── Who has read it (a manager) ────────────────────────────────────────────────────
	function reach_dialog(name, title, on_done) {
		call("get_reach", { announcement: name }).then((r) => {
			const pending = r.pending
				.map(
					(p) => `<div class="sf-dlg-person"><span>${esc(p.full_name)}</span>
						<span class="text-muted">${esc(p.seen ? __("Read, not confirmed") : __("Not read yet"))}</span></div>`
				)
				.join("");
			const dialog = new frappe.ui.Dialog({
				title: title || name,
				fields: [{ fieldtype: "HTML", fieldname: "body" }],
				primary_action_label: r.can_remind ? __("Remind them") : null,
				primary_action() {
					call("remind_pending", { announcement: name }).then((res) => {
						frappe.show_alert({ message: __("Reminder sent to {0} people", [res.reminded]), indicator: "green" });
						dialog.hide();
						if (on_done) on_done();
					});
				},
			});
			dialog.fields_dict.body.$wrapper.html(`
				<div class="sf-dlg">
					${reach_bars({ total: r.total, seen: r.seen, acknowledged: r.acknowledged }, r.needs_ack)}
					<p class="text-muted" style="margin:12px 0 0">${
						r.pending_total
							? esc(__("{0} still to {1}:", [r.pending_total, r.needs_ack ? __("read and confirm") : __("read")]))
							: esc(__("Everyone has done it. Nothing is waiting."))
					}</p>
					${pending ? `<div class="sf-dlg-pending">${pending}</div>` : ""}
					${r.last_reminded_on ? `<p class="text-muted small" style="margin-top:8px">${esc(__("Last reminder: {0}", [when(r.last_reminded_on)]))}</p>` : ""}
				</div>`);
			dialog.show();
		});
	}

	// ── The megaphone ──────────────────────────────────────────────────────────────────
	function paint_badge(ring) {
		const $nav = $(".sf-ann-nav");
		if (!$nav.length) return;
		const n = state.counts.unread || 0;
		const $badge = $nav.find(".sf-ann-badge");
		$badge.text(n > 99 ? "99+" : n).prop("hidden", !n).toggleClass("is-urgent", !!state.counts.urgent);
		const $btn = $nav.find(".sf-ann-btn");
		const label = n ? __("{0} new updates", [n]) : __("Updates");
		$btn.attr({ title: label, "aria-label": label });
		if (ring) {
			$btn.removeClass("is-ring");
			void $btn[0].offsetWidth;
			$btn.addClass("is-ring");
		}
	}

	function mount_bell() {
		if ($(".sf-ann-nav").length) return true;
		const $anchor = $(".navbar .navbar-nav .dropdown-notifications").first();
		if (!$anchor.length) return false;

		const $li = $(`
			<li class="nav-item dropdown sf-ann-nav dropdown-mobile">
				<button class="btn-reset nav-link sf-ann-btn text-muted" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false"
					aria-label="${esc(__("Updates"))}" title="${esc(__("Updates"))}">
					${svg("megaphone")}<span class="sf-ann-badge" hidden></span>
				</button>
				<div class="dropdown-menu dropdown-menu-right sf-ann-panel" role="menu">
					<div class="sf-ann-head"><b>${esc(__("Updates"))}</b><button type="button" data-bell="read-all">${esc(__("Mark all as read"))}</button></div>
					<div class="sf-ann-list"></div>
					<a class="sf-ann-foot" href="/app/${HUB}">${esc(__("See all updates"))}</a>
				</div>
			</li>`);
		$li.insertBefore($anchor);
		paint_badge();

		const $list = $li.find(".sf-ann-list");

		function load_list() {
			$list.html('<div class="sf-ann-none">' + esc(__("Loading...")) + "</div>");
			call("get_feed", { view: "all", page_length: 6 }).then((feed) => {
				if (!feed.items.length) {
					$list.html(`<div class="sf-ann-none">${svg("inbox")}<br>${esc(__("Nothing new. You are all caught up."))}</div>`);
					return;
				}
				$list.html(
					feed.items
						.map((item) => {
							const k = kind_meta(item.kind);
							const unread = !item.is_read;
							return `
								<button type="button" class="sf-ann-item sf-k-${k.cls}${unread ? " is-unread" : ""}" data-name="${esc(item.name)}">
									<div class="sf-card-ico">${svg(k.icon)}</div>
									<div>
										<div class="sf-ann-item-title">${esc(item.title)}
											${item.priority === "Urgent" ? `<span class="sf-badge is-urgent">${esc(__("Urgent"))}</span>` : ""}
										</div>
										<div class="sf-ann-item-sum">${esc(item.summary)}</div>
										<div class="sf-ann-item-time">${esc(when(item.published_on))}</div>
									</div>
								</button>`;
						})
						.join("")
				);
			});
		}

		$li.on("show.bs.dropdown", load_list);
		$li.on("click", ".sf-ann-item", function () {
			open_hub($(this).data("name"));
		});
		$li.on("click", "[data-bell=read-all]", function (event) {
			event.stopPropagation();
			call("mark_all_read").then((counts) => {
				set_counts(counts);
				load_list();
			});
		});
		$li.find(".sf-ann-foot").on("click", function (event) {
			event.preventDefault();
			open_hub(null);
		});
		return true;
	}

	// ── The must-read pop-up ───────────────────────────────────────────────────────────
	function later_list() {
		try {
			return JSON.parse(sessionStorage.getItem(LATER_KEY) || "[]");
		} catch (e) {
			return [];
		}
	}

	function remember_later(name) {
		try {
			sessionStorage.setItem(LATER_KEY, JSON.stringify([...new Set([...later_list(), name])]));
		} catch (e) {
			/* private window: the pop-up simply comes back on the next load */
		}
	}

	let popup_open = false;

	function show_popup(items) {
		if (popup_open || !items.length) return;
		popup_open = true;
		let index = 0;

		const $back = $('<div class="sf-pop-backdrop" role="dialog" aria-modal="true"></div>').appendTo("body");

		function close() {
			$(document).off("keydown.sfpop");
			$back.remove();
			popup_open = false;
		}

		function next() {
			index += 1;
			if (index >= items.length) close();
			else draw();
		}

		function later() {
			remember_later(items[index].name);
			next();
		}

		function draw() {
			const item = items[index];
			const k = kind_meta(item.kind);
			const link = item.link;
			const ack = item.require_ack && !item.acknowledged;
			$back.html(`
				<div class="sf-pop sf-k-${k.cls}${item.priority === "Urgent" ? " sf-p-urgent" : ""}">
					<div class="sf-pop-head">
						<div class="sf-card-ico">${svg(k.icon)}</div>
						<div>
							<div class="sf-pop-kind">${esc(item.priority === "Urgent" ? __("Urgent") + " - " + k.label : k.label)}</div>
							<div style="font-size:13px;opacity:.85">${esc(__("From {0}", [item.posted_by]))}</div>
						</div>
						<div class="sf-pop-count">${items.length > 1 ? esc(__("{0} of {1}", [index + 1, items.length])) : ""}</div>
					</div>
					<div class="sf-pop-body">
						<h2 class="sf-pop-title">${esc(item.title)}</h2>
						<p class="sf-card-summary">${esc(item.summary)}</p>
						${item.details ? `<div class="sf-card-details">${item.details}</div>` : ""}
						${link && !link.can_open ? `<p class="sf-locked" style="margin-top:12px">${svg("lock")} ${esc(__("You do not have access to this. Ask your manager if you need it."))}</p>` : ""}
					</div>
					<div class="sf-pop-foot">
						<button class="sf-btn" data-pop="later">${esc(ack ? __("Remind me later") : __("Close"))}</button>
						${link && link.can_open ? `<button class="sf-btn" data-pop="open">${esc(link.label)} ${svg("arrow")}</button>` : ""}
						<button class="sf-btn is-primary" data-pop="done">${svg("check")} ${esc(ack ? __("I have read and understood") : __("Got it"))}</button>
					</div>
				</div>`);

			$back.find("[data-pop=later]").on("click", later);
			$back.find("[data-pop=open]").on("click", () => {
				call("mark_read", { names: JSON.stringify([item.name]) }).then(set_counts);
				go(link);
				close();
			});
			$back.find("[data-pop=done]").on("click", function () {
				$(this).prop("disabled", true);
				call(ack ? "acknowledge" : "mark_read", ack ? { announcement: item.name } : { names: JSON.stringify([item.name]) })
					.then((counts) => {
						set_counts(counts);
						$(document).trigger("sf_ann:changed");
						next();
					})
					.catch(() => $(this).prop("disabled", false));
			});
		}

		$(document).on("keydown.sfpop", (event) => {
			if (event.key === "Escape") later();
		});
		draw();
	}

	function check_popup() {
		const c = state.counts;
		if (!(c.need_ack || c.urgent) || popup_open) return;
		call("get_popup_queue").then((items) => {
			const skipped = later_list();
			show_popup((items || []).filter((item) => !skipped.includes(item.name)));
		});
	}

	// ── The daily motivation card ──────────────────────────────────────────────────────
	// A soft card at the top of the screen, once a day, on the first desk load. It never blocks the
	// work: a must-read pop-up can open over it, and the cross or "Let's go" closes it. The server
	// is told it was shown the moment it is handed out, so a reload before closing it does not bring it back.
	function show_motivation(m) {
		if (!m || !m.message) return;
		$(".sf-mot").remove();
		const hour = new Date().getHours();
		const greeting = hour < 12 ? __("Good morning") : hour < 17 ? __("Good afternoon") : __("Good evening");
		const name = (frappe.boot.user && frappe.boot.user.first_name) || frappe.session.user_fullname || "";
		// the way into My Updates, with what is waiting there, so the day starts with one click to what matters
		const waiting = state.counts.unread || 0;
		const hub_label = waiting ? __("See what's new ({0})", [waiting]) : __("Open My Updates");
		const $card = $(`
			<aside class="sf-mot" role="status" aria-live="polite">
				<div class="sf-mot-sun">${svg("sun")}</div>
				<div class="sf-mot-body">
					<button type="button" class="sf-mot-x" aria-label="${esc(__("Close"))}">&times;</button>
					<div class="sf-mot-kicker"><span>${esc(greeting + (name ? ", " + name : ""))}</span><span>${esc(m.title || __("Thought for today"))}</span></div>
					<blockquote class="sf-mot-text">${esc(m.message)}</blockquote>
					${m.author ? `<div class="sf-mot-author">${esc(m.author)}</div>` : ""}
					<div class="sf-mot-actions">
						<button type="button" class="sf-btn is-primary sf-mot-go">${esc(__("Let's go"))}</button>
						<button type="button" class="sf-btn sf-mot-hub">${svg("megaphone")} ${esc(hub_label)}</button>
					</div>
				</div>
				<div class="sf-mot-timer"><i></i></div>
			</aside>`).appendTo("body");

		let timer = null;
		const close = () => {
			clearTimeout(timer);
			$card.addClass("is-leaving");
			setTimeout(() => $card.remove(), 340);
		};
		// it leaves by itself after the bar has drained; the pointer on the card holds it, and a short grace follows
		timer = setTimeout(close, 22500);
		$card.on("mouseenter", () => clearTimeout(timer)).on("mouseleave", () => {
			clearTimeout(timer);
			timer = setTimeout(close, 4000);
		});
		$card.find(".sf-mot-x, .sf-mot-go").on("click", close);
		$card.find(".sf-mot-hub").on("click", () => {
			close();
			open_hub(null);
		});

	}

	// ── A new update arrives while the desk is open ────────────────────────────────────
	function on_live(message) {
		call("get_summary").then((counts) => {
			set_counts(counts);
			paint_badge(true);
		});
		const html = `<b>${esc(message.title)}</b><br><span>${esc(message.summary)}</span>
			<br><a data-action="open">${esc(__("Open"))}</a>`;
		frappe.show_alert({ message: html, indicator: message.priority === "Urgent" ? "red" : "blue" }, 10, {
			open: () => open_hub(message.name),
		});
		$(document).trigger("sf_ann:new", [message]);
		if (message.priority === "Urgent" || message.require_ack) setTimeout(check_popup, 600);
	}

	// ── A fallback for when the socket cannot be reached (a proxy, a firewall, socketio down) ──
	// Every two minutes, and whenever the tab comes back to the front, ask the server for the
	// counters. Quiet: a failed call (an expired session, a blip) never shows an error.
	function poll() {
		if (document.hidden || popup_open) return;
		frappe.call({
			method: API + "get_summary",
			silent: true,
			callback(r) {
				const counts = r && r.message;
				if (!counts) return;
				const before = Object.assign({}, state.counts);
				set_counts(counts);
				const grew =
					counts.unread > before.unread || counts.need_ack > before.need_ack || counts.urgent > before.urgent;
				if (!grew) return;
				paint_badge(true);
				$(document).trigger("sf_ann:new", [{}]);
				if (counts.urgent || counts.need_ack) check_popup();
			},
			error() {},
		});
	}

	// ── Start ──────────────────────────────────────────────────────────────────────────
	function wait_for_navbar(tries) {
		if (mount_bell() || tries <= 0) return;
		setTimeout(() => wait_for_navbar(tries - 1), 250);
	}

	$(document).on("startup", function () {
		const boot = frappe.boot && frappe.boot.sf_announcements;
		// no payload: a site that has not migrated this feature, or a user who is not staff
		if (!boot) return;
		Object.assign(state.counts, boot);
		wait_for_navbar(40);
		frappe.realtime.on("sf_announcement", on_live);
		// asked of the server, never read from the boot payload (Frappe caches that per person, so the card
		// came back on every refresh); the server hands it out once a day and uses the day up in the same call
		setTimeout(() => {
			frappe
				.xcall("sf_trading.motivation.claim_motivation")
				.then((m) => show_motivation(m))
				.catch(() => {});
		}, 900);
		setTimeout(check_popup, 1800);
		setInterval(poll, 120000);
		document.addEventListener("visibilitychange", () => {
			if (!document.hidden) poll();
		});
	});

	window.sf_ann = {
		API,
		HUB,
		KIND_LIST,
		call,
		card,
		wire,
		svg,
		esc,
		when,
		go,
		kind_meta,
		set_counts,
		open_hub,
		counts: () => state.counts,
		reach_dialog,
		reach_bars,
		show_motivation,
	};
})();
