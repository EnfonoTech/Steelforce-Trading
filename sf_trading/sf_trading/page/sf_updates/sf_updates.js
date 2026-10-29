// apps/sf_trading/sf_trading/page/sf_updates/sf_updates.js
//
// My Updates: everything meant for the signed-in person -- announcements, new features, rules,
// reports and notices -- with what is new, what waits for their confirmation, and the reports
// they may open. A manager also sees what the team has posted and who has read it.
//
// The cards, their buttons and the counters come from public/js/announcements.js (window.sf_ann);
// who may see what comes from sf_trading/announcements.py. Nothing here decides access.

frappe.pages["sf-updates"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("My Updates"),
		single_column: true,
	});
	wrapper.sf_hub = new SfUpdatesHub(wrapper, page);
};

frappe.pages["sf-updates"].on_page_show = function (wrapper) {
	if (wrapper.sf_hub) wrapper.sf_hub.show();
};

class SfUpdatesHub {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.can_post = !!((frappe.boot.sf_announcements || {}).can_post);
		this.state = { view: "all", kind: null, search: "", total: 0, shown: 0 };
		this.reports = [];
		this.report_total = 0;
		this.reports_all = false;
		this.opened = null;
		this.request = 0;

		this.build();
		this.bind();
	}

	// ── Layout ─────────────────────────────────────────────────────────────────────────
	build() {
		const A = window.sf_ann;
		const tiles = [
			{ key: "unread", icon: "bell", label: __("New for you"), cls: "announcement", view: "unread" },
			{ key: "need_ack", icon: "check", label: __("Waiting for your OK"), cls: "rule", view: "need_ack" },
			{ key: "urgent", icon: "alert", label: __("Urgent"), cls: "urgent", view: "urgent" },
			{ key: "reports", icon: "chart", label: __("Reports you can open"), cls: "report", view: null },
		];
		const view_chips = [
			{ view: "all", label: __("All") },
			{ view: "unread", label: __("Unread") },
			{ view: "need_ack", label: __("Need my OK") },
			{ view: "urgent", label: __("Urgent") },
		];
		const kind_chips = A.KIND_LIST.map((kind) => ({ kind, label: A.kind_meta(kind).plural }));

		const chip = (c) =>
			`<button class="sf-fchip" data-view="${c.view || ""}" data-kind="${c.kind || ""}">${A.esc(c.label)}</button>`;

		this.$root = $(`
			<div class="sf-hub">
				<section class="sf-hero">
					<div>
						<h2 class="sf-hero-title"></h2>
						<p class="sf-hero-sub"></p>
						<div class="sf-hero-quote" hidden></div>
					</div>
					<div class="sf-hero-art">${A.svg("megaphone")}</div>
				</section>

				<section class="sf-tiles">
					${tiles
						.map(
							(t) => `
						<button class="sf-tile sf-k-${t.cls}" data-tile="${t.key}" data-view="${t.view || ""}">
							<span class="sf-tile-ico">${A.svg(t.icon)}</span>
							<span><div class="sf-tile-num">0</div><div class="sf-tile-label">${A.esc(t.label)}</div></span>
						</button>`
						)
						.join("")}
				</section>

				<div data-region="focus"></div>

				<section class="sf-toolbar">
					<div class="sf-chips">
						${view_chips.map(chip).join("")}
						<span class="sf-fchip-sep"></span>
						${kind_chips.map(chip).join("")}
						${this.can_post ? `<span class="sf-fchip-sep"></span><button class="sf-fchip" data-view="posts" data-kind="">${A.esc(__("Posted by us"))}</button>` : ""}
					</div>
					<div class="sf-search"><input type="search" class="form-control input-sm" placeholder="${A.esc(__("Search updates"))}"></div>
				</section>

				<div class="sf-hub-grid">
					<div class="sf-feed" data-region="feed"></div>
					<aside class="sf-side">
						<div class="sf-panel sf-k-report" data-region="reports">
							<h4 class="sf-panel-title">${A.svg("chart")} ${A.esc(__("Reports for you"))}</h4>
							<input type="search" class="form-control input-sm" placeholder="${A.esc(__("Find a report"))}">
							<div data-region="report-list"></div>
						</div>
					</aside>
				</div>
			</div>`).appendTo(this.page.main);

		this.$feed = this.$root.find("[data-region=feed]");
		this.$focus = this.$root.find("[data-region=focus]");
		this.$reports = this.$root.find("[data-region=report-list]");

		if (this.can_post) {
			this.page.set_primary_action(__("New Announcement"), () => frappe.new_doc("SF Announcement"), "add");
		}
		this.page.set_secondary_action(__("Mark all as read"), () => this.mark_all_read());
		this.paint_hero();
		this.paint_chips();
		this.paint_tiles(A.counts());
	}

	bind() {
		const A = window.sf_ann;

		// the feed is wired again on every load, because the "posted by us" view draws its cards differently
		A.wire(this.$focus, { opts: {} });

		this.$root.on("click", ".sf-fchip", (event) => {
			const $c = $(event.currentTarget);
			const kind = $c.data("kind") || null;
			this.state.kind = kind;
			this.state.view = kind ? "all" : $c.data("view") || "all";
			this.paint_chips();
			this.load(true);
		});

		this.$root.on("click", ".sf-tile", (event) => {
			const $t = $(event.currentTarget);
			if ($t.data("tile") === "reports") {
				this.$root.find("[data-region=reports]")[0].scrollIntoView({ behavior: "smooth", block: "center" });
				return;
			}
			this.state.view = $t.data("view");
			this.state.kind = null;
			this.paint_chips();
			this.load(true);
		});

		let timer = null;
		this.$root.find(".sf-search input").on("input", (event) => {
			clearTimeout(timer);
			const value = event.target.value;
			timer = setTimeout(() => {
				this.state.search = value;
				this.load(true);
			}, 300);
		});

		let report_timer = null;
		this.$root.find("[data-region=reports] input").on("input", (event) => {
			clearTimeout(report_timer);
			const value = event.target.value;
			report_timer = setTimeout(() => this.load_reports(value), 300);
		});

		this.$root.on("click", "[data-more=feed]", () => this.load(false));
		this.$root.on("click", "[data-more=reports]", () => {
			this.reports_all = !this.reports_all;
			this.paint_reports();
		});
		this.$root.on("click", ".sf-rep", (event) => {
			const report = this.reports[$(event.currentTarget).data("i")];
			if (report) frappe.set_route(...report.route);
		});
		this.$root.on("click", "[data-focus-close]", () => this.$focus.empty());

		// counters move when something is read, confirmed or arrives -- here or in the pop-up
		$(document).on("sf_ann:counts.sfhub", (event, counts) => this.paint_tiles(counts));
		$(document).on("sf_ann:new.sfhub sf_ann:changed.sfhub", () => {
			if ($(this.wrapper).is(":visible")) this.load(true);
		});
	}

	// ── Showing the page ───────────────────────────────────────────────────────────────
	show() {
		const A = window.sf_ann;
		A.call("get_summary").then((counts) => A.set_counts(counts));
		A.call("get_motivation").then((m) => this.paint_quote(m));
		this.load(true);
		this.load_reports(this.$root.find("[data-region=reports] input").val());

		const open = (frappe.route_options && frappe.route_options.open) || new URLSearchParams(window.location.search).get("open");
		frappe.route_options = null;
		if (open && open !== this.opened) {
			this.opened = open;
			this.focus(open);
		}
	}

	// the update a notification pointed at, opened and marked read
	focus(name) {
		const A = window.sf_ann;
		A.call("get_one", { announcement: name })
			.then((item) => {
				if (!item) return null;
				const unread = item.status === "Published" && !item.is_read && !item.require_ack;
				return unread
					? A.call("mark_read", { names: JSON.stringify([name]) })
							.then(A.set_counts)
							.then(() => A.call("get_one", { announcement: name }))
					: item;
			})
			.then((item) => {
				if (!item) return;
				this.$focus.html(`
					<div class="sf-feed" style="margin-bottom:16px">
						${A.card(item, { expanded: true }).replace("sf-card ", "sf-card is-focus ")}
						<button class="sf-more" data-focus-close>${A.esc(__("Close this"))}</button>
					</div>`);
				this.$focus[0].scrollIntoView({ behavior: "smooth", block: "start" });
			})
			.catch(() => {
				this.opened = null;
			});
	}

	// ── Painting ───────────────────────────────────────────────────────────────────────
	paint_hero() {
		const A = window.sf_ann;
		const hour = new Date().getHours();
		const greeting = hour < 12 ? __("Good morning") : hour < 17 ? __("Good afternoon") : __("Good evening");
		const name = (frappe.boot.user && frappe.boot.user.first_name) || frappe.session.user_fullname || "";
		const c = A.counts();
		const sub = c.urgent
			? __("{0} urgent: please read it first.", [c.urgent])
			: c.unread
			? __("{0} new for you. Everything that concerns you is here.", [c.unread])
			: __("You are all caught up. New rules, reports and notices will show up here.");
		this.$root.find(".sf-hero-title").text(`${greeting}${name ? ", " + name : ""}`);
		this.$root.find(".sf-hero-sub").text(sub);
	}

	// today's motivation, kept at the top of the page all day (a manager can switch this off in the settings)
	paint_quote(m) {
		const A = window.sf_ann;
		const $quote = this.$root.find(".sf-hero-quote");
		if (!m || !m.message || !m.show_in_hub) {
			$quote.prop("hidden", true).empty();
			return;
		}
		$quote
			.html(`${A.svg("sun")}<span>&ldquo;${A.esc(m.message)}&rdquo;${m.author ? ` <em>&mdash; ${A.esc(m.author)}</em>` : ""}</span>`)
			.prop("hidden", false);
	}

	paint_tiles(counts) {
		const c = counts || {};
		this.$root.find("[data-tile=unread] .sf-tile-num").text(c.unread || 0);
		this.$root.find("[data-tile=need_ack] .sf-tile-num").text(c.need_ack || 0);
		this.$root.find("[data-tile=urgent] .sf-tile-num").text(c.urgent || 0);
		this.$root.find("[data-tile=urgent]").toggleClass("is-hot", !!c.urgent);
		this.$root.find("[data-tile=need_ack]").toggleClass("is-hot", !!c.need_ack);
		this.$root.find("[data-tile=reports] .sf-tile-num").text(this.report_total || 0);
		this.paint_hero();
	}

	paint_chips() {
		const { view, kind } = this.state;
		this.$root.find(".sf-fchip").each(function () {
			const $c = $(this);
			const is_kind = !!$c.data("kind");
			const active = is_kind ? $c.data("kind") === kind : !kind && $c.data("view") === view;
			$c.toggleClass("is-active", active);
			if (is_kind) $c.addClass("sf-k-" + window.sf_ann.kind_meta($c.data("kind")).cls);
		});
		this.$root.find(".sf-tile").each(function () {
			$(this).toggleClass("is-active", !kind && !!$(this).data("view") && $(this).data("view") === view);
		});
	}

	skeleton() {
		this.$feed.html('<div class="sf-skel"></div><div class="sf-skel"></div><div class="sf-skel"></div>');
	}

	empty_state() {
		const A = window.sf_ann;
		const { view, kind, search } = this.state;
		let title = __("Nothing here yet");
		let text = __("When a manager posts something for you, it will show up here.");
		if (search) {
			title = __("No match");
			text = __("Nothing matches \"{0}\". Try a shorter word.", [search]);
		} else if (view === "unread") {
			title = __("You have read everything");
			text = __("New updates will appear here as soon as they are published.");
		} else if (view === "need_ack") {
			title = __("Nothing waits for your OK");
			text = __("Rules that need your confirmation will show up here.");
		} else if (view === "urgent") {
			title = __("Nothing urgent");
			text = __("Urgent notices show up here and as a pop-up.");
		} else if (view === "posts") {
			title = __("You have not posted anything yet");
			text = __("Use the New Announcement button to tell people something.");
		} else if (kind) {
			text = __("There is nothing of this type for you right now.");
		}
		return `<div class="sf-empty">${A.svg("inbox")}<h4>${A.esc(title)}</h4><div>${A.esc(text)}</div></div>`;
	}

	// ── The feed ───────────────────────────────────────────────────────────────────────
	load(reset) {
		const A = window.sf_ann;
		const posts = this.state.view === "posts";
		const ticket = ++this.request;
		if (reset) {
			this.state.shown = 0;
			this.skeleton();
		}
		A.call("get_feed", {
			view: this.state.view,
			kind: this.state.kind,
			search: this.state.search,
			start: this.state.shown,
			page_length: 15,
		}).then((feed) => {
			if (ticket !== this.request) return; // a newer request has already replaced this one
			this.state.total = feed.total;
			if (reset) this.$feed.empty();
			this.$feed.find("[data-more=feed]").remove();

			if (reset && !feed.items.length) {
				this.$feed.html(this.empty_state());
				return;
			}
			const opts = { posts };
			A.wire(this.$feed, { opts });
			this.$feed.append(feed.items.map((item) => A.card(item, opts)).join(""));
			this.state.shown += feed.items.length;

			if (this.state.shown < this.state.total) {
				this.$feed.append(
					`<button class="sf-btn sf-loadmore" data-more="feed">${A.esc(__("Show more ({0} left)", [this.state.total - this.state.shown]))}</button>`
				);
			}
		});
	}

	mark_all_read() {
		const A = window.sf_ann;
		A.call("mark_all_read").then((counts) => {
			A.set_counts(counts);
			frappe.show_alert({ message: __("All updates marked as read"), indicator: "green" });
			this.load(true);
		});
	}

	// ── Reports for you ────────────────────────────────────────────────────────────────
	load_reports(search) {
		const A = window.sf_ann;
		A.call("get_my_reports", { search: search || "" }).then((reports) => {
			this.reports = reports || [];
			// the tile counts every report the person can open, not just those the search box leaves
			if (!search) this.report_total = this.reports.length;
			this.paint_reports();
			this.paint_tiles(A.counts());
		});
	}

	paint_reports() {
		const A = window.sf_ann;
		if (!this.reports.length) {
			this.$reports.html(`<div class="text-muted small" style="padding:6px 2px">${A.esc(__("No reports to show."))}</div>`);
			return;
		}
		const visible = this.reports_all ? this.reports : this.reports.slice(0, 8);
		const rows = visible
			.map((report, i) => {
				const sub = report.announced_title
					? __("Announced: {0}", [report.announced_title])
					: report.is_new
					? __("Updated recently")
					: report.module || "";
				return `
					<button class="sf-rep" data-i="${i}">
						<span class="sf-rep-ico">${A.svg("chart")}</span>
						<span class="sf-rep-main">
							<div class="sf-rep-name">${A.esc(report.name)}</div>
							<div class="sf-rep-sub">${A.esc(sub)}</div>
						</span>
						${report.is_new ? `<span class="sf-badge is-new">${A.esc(__("New"))}</span>` : ""}
					</button>`;
			})
			.join("");
		const more =
			this.reports.length > 8
				? `<button class="sf-more" data-more="reports">${A.esc(this.reports_all ? __("Show fewer") : __("Show all {0}", [this.reports.length]))}</button>`
				: "";
		this.$reports.html(rows + more);
	}
}
