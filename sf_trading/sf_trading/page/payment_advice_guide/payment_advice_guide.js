// apps/sf_trading/sf_trading/sf_trading/page/payment_advice_guide/payment_advice_guide.js
// Supplier Payments Guide — an in-desk guide at /app/payment-advice-guide.
//
// Lives inside the desk (login required, opens from the Workspace) so staff read it without
// leaving ERPNext. The public copy at /user-guide carries the same content for people who
// have no desk login.
//
// The UI panels below are ILLUSTRATIONS drawn in HTML — labelled as such — showing the exact
// fields and dialogs of the real forms. They are not screenshots and never claim to be.
// Buttons in them are inert; the "Open …" links go to the real thing.

frappe.pages["payment-advice-guide"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Supplier Payments — Payment Advice Guide"),
		single_column: true,
	});

	page.set_primary_action(__("Open Builder"), () => frappe.set_route("payment-advice-builder"), "add");
	page.add_menu_item(__("Payment Advice list"), () => frappe.set_route("List", "Payment Advice"));
	page.add_menu_item(__("Automation Settings"), () =>
		frappe.set_route("List", "Payment Automation Settings")
	);
	page.add_menu_item(__("Public guide (/user-guide)"), () => window.open("/user-guide", "_blank"));

	$(guide_html()).appendTo(page.main);

	// in-page navigation
	page.main.on("click", ".sfg-nav a", function (e) {
		e.preventDefault();
		const target = page.main.find($(this).attr("href"));
		if (target.length) {
			target[0].scrollIntoView({ behavior: "smooth", block: "start" });
		}
	});
};

function guide_html() {
	return `
<style>
.sfg { max-width: 980px; margin: 0 auto; padding-bottom: 4rem; color: var(--text-color); }
.sfg h2 { font-size: 1.35rem; margin: 2.5rem 0 .35rem; }
.sfg h3 { font-size: 1.05rem; margin: 1.75rem 0 .35rem; }
.sfg p, .sfg li { line-height: 1.65; }
.sfg .lead { font-size: 1.05rem; color: var(--text-muted); }
.sfg-nav { display: flex; flex-wrap: wrap; gap: .4rem; margin: 1rem 0 2rem;
  padding: 1rem; background: var(--fg-color); border: 1px solid var(--border-color);
  border-radius: var(--border-radius-md); position: sticky; top: 0; z-index: 5; }
.sfg-nav a { font-size: .82rem; padding: .25rem .6rem; border-radius: var(--border-radius-sm);
  background: var(--control-bg); color: var(--text-color); text-decoration: none; }
.sfg-nav a:hover { background: var(--bg-blue); color: var(--text-on-blue, #fff); }
.sfg-card { border: 1px solid var(--border-color); border-radius: var(--border-radius-md);
  padding: 1.1rem 1.25rem; margin: 1rem 0; background: var(--fg-color); }
.sfg-note { border-left: 3px solid var(--blue-500); background: var(--bg-light-blue, rgba(0,120,255,.06));
  padding: .85rem 1rem; border-radius: var(--border-radius-sm); margin: 1rem 0; }
.sfg-warn { border-left: 3px solid var(--orange-500); background: rgba(255,150,0,.08);
  padding: .85rem 1rem; border-radius: var(--border-radius-sm); margin: 1rem 0; }
.sfg-steps { counter-reset: sfg; padding: 0; list-style: none; margin: 1rem 0; }
.sfg-steps > li { counter-increment: sfg; position: relative; padding: 0 0 1.1rem 2.4rem; }
.sfg-steps > li::before { content: counter(sfg); position: absolute; left: 0; top: 0;
  width: 1.6rem; height: 1.6rem; border-radius: 50%; background: var(--blue-500); color: #fff;
  font-size: .8rem; display: grid; place-items: center; font-weight: 600; }
.sfg-shot { border: 1px solid var(--border-color); border-radius: var(--border-radius-md);
  overflow: hidden; margin: 1rem 0 .4rem; background: var(--card-bg, var(--fg-color)); }
.sfg-shot-bar { display: flex; align-items: center; gap: .5rem; padding: .55rem .8rem;
  background: var(--control-bg); border-bottom: 1px solid var(--border-color); font-size: .82rem; }
.sfg-shot-bar .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--gray-400); }
.sfg-shot-body { padding: .9rem 1rem; }
.sfg-caption { font-size: .78rem; color: var(--text-muted); margin: 0 0 1.5rem; font-style: italic; }
.sfg-field { display: grid; grid-template-columns: 190px 1fr; gap: .5rem .75rem;
  font-size: .84rem; margin-bottom: .5rem; }
.sfg-field span:first-child { color: var(--text-muted); }
.sfg-input { border: 1px solid var(--border-color); border-radius: var(--border-radius-sm);
  padding: .2rem .45rem; background: var(--control-bg); display: inline-block; min-width: 160px; }
.sfg-btn { display: inline-block; padding: .25rem .7rem; border-radius: var(--border-radius-sm);
  font-size: .8rem; border: 1px solid var(--border-color); background: var(--control-bg); }
.sfg-btn.primary { background: var(--blue-500); color: #fff; border-color: var(--blue-500); }
.sfg table { width: 100%; border-collapse: collapse; font-size: .84rem; margin: .75rem 0 1.25rem; }
.sfg th, .sfg td { border: 1px solid var(--border-color); padding: .45rem .6rem; text-align: left;
  vertical-align: top; }
.sfg th { background: var(--control-bg); font-weight: 600; }
.sfg .pill { display: inline-block; padding: .1rem .5rem; border-radius: 10px; font-size: .74rem;
  border: 1px solid var(--border-color); }
.sfg .pill.grey { background: var(--gray-100); }
.sfg .pill.blue { background: rgba(0,120,255,.12); }
.sfg .pill.green { background: rgba(0,160,80,.14); }
.sfg .pill.amber { background: rgba(255,180,0,.16); }
.sfg .pill.orange { background: rgba(255,140,0,.16); }
.sfg .pill.red { background: rgba(230,60,60,.14); }
.sfg .flow { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; margin: 1rem 0; }
.sfg .flow b { border: 1px solid var(--border-color); border-radius: var(--border-radius-sm);
  padding: .3rem .65rem; background: var(--fg-color); font-weight: 500; font-size: .85rem; }
.sfg .flow i { color: var(--text-muted); font-style: normal; }
.sfg code { background: var(--control-bg); padding: .1rem .35rem; border-radius: 3px; font-size: .82rem; }
.sfg-illus-tag { display: inline-block; font-size: .68rem; text-transform: uppercase;
  letter-spacing: .05em; color: var(--text-muted); border: 1px dashed var(--border-color);
  padding: .05rem .4rem; border-radius: 3px; margin-left: .5rem; }
</style>

<div class="sfg">

  <p class="lead">Pay many invoices of one supplier with a single approved document — by hand,
  in batches, or on a schedule.</p>

  <div class="sfg-nav">
    <a href="#why">Why</a><a href="#one">Raise one advice</a><a href="#fetch">Get Outstanding</a>
    <a href="#batch">Batch by supplier</a><a href="#list">From invoice list</a>
    <a href="#approve">Approval</a><a href="#pe">Payment Entry</a><a href="#auto">Automation</a>
    <a href="#guards">Guards</a><a href="#status">Statuses</a><a href="#skips">Skip reasons</a>
    <a href="#fix">Troubleshooting</a>
  </div>

  <div class="sfg-warn">
    <b>Illustrations, not screenshots.</b> The panels below are drawn in HTML to show the exact
    fields and dialogs of each screen. They are accurate to the forms as built, but they are not
    captures of a running system — open the real screens from the buttons at the top right.
  </div>

  <!-- ── why ───────────────────────────────────────────────────────────────── -->
  <h2 id="why">Why this exists</h2>
  <p>ERPNext's <b>Payment Request</b> covers one document per request. It cannot say
  <i>"pay these 21 invoices for A.A. Kothambawala"</i>. A <b>Payment Advice</b> can: one party,
  many references, one authorised amount, one approval.</p>

  <div class="flow">
    <b>Outstanding invoices</b><i>→</i><b>Payment Advice</b><i>→</i><b>Approval</b>
    <i>→</i><b>Payment Entry</b><i>→</i><b>Invoice paid</b>
  </div>

  <div class="sfg-note">
    <b>Not Purchase-Invoice-only.</b> References may be Purchase Invoices, Sales Invoices,
    <b>Journal Entries</b>, Expense Claims, Purchase Orders or Sales Orders. Real supplier
    balances often include Journal Entries, and those are pulled in automatically.
  </div>

  <p>Outstanding figures are not recalculated by this app — they come from the same ERPNext engine
  the Payment Entry form uses, so part-payments, credit notes and return invoices are already
  netted before you see them.</p>

  <!-- ── one advice ────────────────────────────────────────────────────────── -->
  <h2 id="one">Raise one advice</h2>

  <div class="sfg-shot">
    <div class="sfg-shot-bar"><span class="dot"></span> Payment Advice — new
      <span style="margin-left:auto"><span class="sfg-btn">Get Outstanding Documents</span></span>
    </div>
    <div class="sfg-shot-body">
      <div class="sfg-field"><span>Company *</span><span><span class="sfg-input">Steel Force Trading WLL</span></span></div>
      <div class="sfg-field"><span>Party Type *</span><span><span class="sfg-input">Supplier</span></span></div>
      <div class="sfg-field"><span>Party *</span><span><span class="sfg-input">A.A. Kothambawala CO</span></span></div>
      <div class="sfg-field"><span>Status</span><span><span class="pill grey">Draft</span></span></div>
      <div class="sfg-field"><span>Payment Amount *</span><span><span class="sfg-input">2,464.713</span></span></div>
      <div class="sfg-field"><span>Total Payable</span><span>2,464.713 &nbsp;<span style="color:var(--text-muted)">(read only)</span></span></div>
      <hr>
      <table>
        <thead><tr><th>Type</th><th>Document</th><th>Party Doc No</th><th>Date</th><th>Ageing</th><th>Status</th><th>Outstanding</th><th>Allocated</th></tr></thead>
        <tbody>
          <tr><td>Purchase Invoice</td><td>ACC-PINV-2026-01469</td><td>SUP-77</td><td>31-01-2026</td><td>176</td><td>Overdue</td><td>110.000</td><td>110.000</td></tr>
          <tr><td>Journal Entry</td><td>ACC-JV-2026-03351</td><td></td><td>01-01-2026</td><td>206</td><td></td><td>2,266.282</td><td>2,266.282</td></tr>
          <tr><td>Purchase Invoice</td><td>ACC-PINV-2026-01470</td><td></td><td>01-02-2026</td><td>175</td><td>Overdue</td><td>114.400</td><td>88.431</td></tr>
        </tbody>
      </table>
      <div style="font-size:.8rem;color:var(--text-muted)">2,464.713 of 2,464.713 allocated across 3 of 3 references</div>
    </div>
  </div>
  <p class="sfg-caption">Illustration — the Payment Advice form with references fetched and the
  allocation headline. <span class="sfg-illus-tag">not a screenshot</span></p>

  <ol class="sfg-steps">
    <li><b>Set Company, Party Type and Party.</b> Every other picker — bank account, cost centre,
      project, and the reference documents themselves — is filtered to that company and party.</li>
    <li><b>Press Get Outstanding Documents</b> and apply whatever filters you need.</li>
    <li><b>Enter the Payment Amount.</b> It is allocated <b>oldest first</b>. Rows beyond the amount
      stay listed with zero allocated, so the full picture survives.</li>
    <li><b>Submit</b> — or use the workflow actions if approval is switched on.</li>
  </ol>

  <!-- ── fetch dialog ──────────────────────────────────────────────────────── -->
  <h2 id="fetch">Get Outstanding Documents</h2>
  <p>The same filter set the Payment Entry form offers, because it runs the same code underneath.</p>

  <div class="sfg-shot">
    <div class="sfg-shot-bar"><span class="dot"></span> Get Outstanding Documents</div>
    <div class="sfg-shot-body">
      <div class="sfg-field"><span>Posting Date — From / To</span><span><span class="sfg-input">01-01-2026</span> <span class="sfg-input">26-07-2026</span></span></div>
      <div class="sfg-field"><span>Due Date — From / To</span><span><span class="sfg-input"></span> <span class="sfg-input">26-07-2026</span></span></div>
      <div class="sfg-field"><span>Outstanding — Greater / Less</span><span><span class="sfg-input">1.000</span> <span class="sfg-input"></span></span></div>
      <div class="sfg-field"><span>Cost Center</span><span><span class="sfg-input">Main - SFB</span></span></div>
      <div class="sfg-field"><span>Outstanding Invoices</span><span>☑</span></div>
      <div class="sfg-field"><span>Orders To Be Billed</span><span>☐</span></div>
      <div style="text-align:right"><span class="sfg-btn primary">Get Documents</span></div>
    </div>
  </div>
  <p class="sfg-caption">Illustration — the fetch dialog. <span class="sfg-illus-tag">not a screenshot</span></p>

  <div class="sfg-note">Use <b>Outstanding — Greater</b> to keep rounding residue out. Live data
  contains invoices with 0.005 outstanding; a floor of 1.000 removes them.</div>

  <!-- ── builder ───────────────────────────────────────────────────────────── -->
  <h2 id="batch">Batch by supplier — the Builder</h2>
  <p><code>/app/payment-advice-builder</code> — sweep everything payable, tick, and create one
  advice per supplier in a single action.</p>

  <div class="sfg-shot">
    <div class="sfg-shot-bar"><span class="dot"></span> Payment Advice Builder
      <span style="margin-left:auto"><span class="sfg-btn primary">Fetch Outstanding</span></span>
    </div>
    <div class="sfg-shot-body">
      <div style="display:flex;gap:1.5rem;flex-wrap:wrap;font-size:.82rem;padding-bottom:.75rem;border-bottom:1px solid var(--border-color)">
        <div><div style="color:var(--text-muted)">Parties</div><b>5</b></div>
        <div><div style="color:var(--text-muted)">Vouchers</div><b>49</b></div>
        <div><div style="color:var(--text-muted)">Outstanding</div><b>32,589.377</b></div>
        <div><div style="color:var(--text-muted)">Skipped</div><b>3</b></div>
      </div>

      <div style="border:1px solid var(--border-color);border-radius:6px;margin-top:.9rem">
        <div style="display:flex;gap:.6rem;align-items:center;padding:.5rem .7rem;background:var(--control-bg);font-size:.85rem">
          ☑ <b>Alex Steel Industries LLC</b>
          <span style="margin-left:auto;color:var(--text-muted)">4 vouchers · oldest 46d</span>
          <b>28,677.344</b>
        </div>
        <table style="margin:0">
          <thead><tr><th></th><th>Document</th><th>Ageing</th><th>Status</th><th>Outstanding</th></tr></thead>
          <tbody>
            <tr><td>☑</td><td>ACC-PINV-2026-01812</td><td>46</td><td>Overdue</td><td>12,400.000</td></tr>
            <tr><td>☑</td><td>ACC-PINV-2026-01880</td><td>31</td><td>Overdue</td><td>16,277.344</td></tr>
          </tbody>
        </table>
      </div>

      <div style="border:1px dashed var(--border-color);border-radius:6px;margin-top:.9rem;padding:.7rem .9rem;font-size:.82rem">
        <div style="color:var(--text-muted);margin-bottom:.3rem">Skipped (3)</div>
        7Skies Metal Fabrication — Below the minimum advice total<br>
        Al Ala Trading Group WLL — Below the minimum advice total<br>
        AL Ayyam Trading CO WLL — Below the minimum advice total
      </div>

      <div style="display:flex;align-items:center;gap:1rem;margin-top:1rem;padding-top:.8rem;border-top:1px solid var(--border-color)">
        <span style="font-size:.85rem">1 party, 2 vouchers — <b>28,677.344</b></span>
        <span style="margin-left:auto"><span class="sfg-btn">Advice Options</span>
        <span class="sfg-btn primary">Create Advices</span></span>
      </div>
    </div>
  </div>
  <p class="sfg-caption">Illustration — grouped preview with a skipped list and the selection
  footer. Figures are real ones measured on prod data.
  <span class="sfg-illus-tag">not a screenshot</span></p>

  <ol class="sfg-steps">
    <li><b>Filters:</b> company, party type, party, due on or before, ageing over N days,
      minimum advice total, cost centre, branch, include on-hold parties.</li>
    <li><b>Fetch Outstanding</b> — suppliers come back biggest first; unpayable ones are listed
      separately <i>with the reason</i>.</li>
    <li><b>Tick</b> a whole supplier or individual invoices. The footer totals your selection live.</li>
    <li><b>Advice Options</b> (optional): mode of payment, company bank account, approver, cost
      centre, remarks, submit-or-not — applied to the whole batch.</li>
    <li><b>Create Advices</b> — confirm the count and total. Drafts by default. Over 15 suppliers
      the work moves to the background and chimes when done.</li>
  </ol>

  <!-- ── list action ───────────────────────────────────────────────────────── -->
  <h2 id="list">From the Purchase Invoice list</h2>
  <p>Tick invoices → <b>Actions → Create Payment Advice (by supplier)</b>. They are grouped by
  supplier, one draft advice each. Anything already on a live advice is skipped and named.
  All ticked invoices must be from one company.</p>

  <!-- ── approval ──────────────────────────────────────────────────────────── -->
  <h2 id="approve">Approval</h2>
  <h3>With the PM Workflow (recommended)</h3>
  <div class="flow">
    <b>Draft</b><i>— Send for Approval →</i><b>Pending Approval</b><i>— Approve →</i>
    <b>Approved</b><i>(submitted)</i>
  </div>
  <table>
    <thead><tr><th>Action</th><th>From → To</th><th>Who</th><th>Requires</th></tr></thead>
    <tbody>
      <tr><td>Send for Approval</td><td>Draft → Pending Approval</td><td>Accounts User</td><td>—</td></tr>
      <tr><td>Approve</td><td>Pending Approval → Approved</td><td>Finance Manager</td><td>An attachment</td></tr>
      <tr><td>Reject</td><td>Pending Approval → Rejected</td><td>Finance Manager</td><td>A comment</td></tr>
      <tr><td>Send for Approval</td><td>Rejected → Pending Approval</td><td>Accounts User</td><td>—</td></tr>
    </tbody>
  </table>
  <p>Reminder after 3 days, escalation after 7. Whoever prepares cannot approve. While the workflow
  is active, scheduled runs deliberately stop at drafts.</p>

  <h3>Without a workflow</h3>
  <p>Only the user linked to the <b>Approver</b> Employee may submit; a System Manager can release
  a stuck advice.</p>

  <!-- ── payment entry ─────────────────────────────────────────────────────── -->
  <h2 id="pe">Payment Entry</h2>
  <p>On a submitted advice press <b>Create Payment Entry</b>. Allocations become the Payment Entry's
  references and ERPNext computes the rest.</p>
  <table>
    <thead><tr><th>Event</th><th>Effect on the advice</th></tr></thead>
    <tbody>
      <tr><td>Payment Entry submitted</td><td>Stamped with the entry and date; status
        <span class="pill green">Paid</span> or <span class="pill amber">Partly Paid</span>;
        every row's status and outstanding refresh</td></tr>
      <tr><td>Payment Entry cancelled</td><td>Stamp cleared, advice back to
        <span class="pill blue">Approved</span>, rows refreshed — raise a corrected entry</td></tr>
      <tr><td>Advice cancelled</td><td>Blocked while its Payment Entry is still submitted</td></tr>
    </tbody>
  </table>
  <div class="sfg-note">One invoice can sit on only <b>one</b> live advice — drafts included — and
  two submitted Payment Entries can never claim the same advice. Each invoice shows its advice
  under <b>Connections</b>.</div>

  <!-- ── automation ────────────────────────────────────────────────────────── -->
  <h2 id="auto">Scheduled runs</h2>
  <p><b>Payment Automation Settings</b> — one configuration per company and party type. A run goes
  as far as you allow, and each step needs the one before it.</p>

  <div class="sfg-shot">
    <div class="sfg-shot-bar"><span class="dot"></span> Payment Automation Settings — PAS-SFB-Supplier
      <span style="margin-left:auto"><span class="sfg-btn">Dry Run</span>
      <span class="sfg-btn primary">Run Now</span></span></div>
    <div class="sfg-shot-body">
      <div style="font-size:.82rem;color:var(--text-muted);margin-bottom:.7rem">
        Will create advices → submit advices on Mon, Tue, Wed, Thu, Sun at 07:00:00</div>
      <div class="sfg-field"><span>Enabled</span><span>☑</span></div>
      <div class="sfg-field"><span>1 · Create Payment Advice</span><span>☑</span></div>
      <div class="sfg-field"><span>2 · Submit Payment Advice</span><span>☑</span></div>
      <div class="sfg-field"><span>3 · Create Payment Entry</span><span>☐</span></div>
      <div class="sfg-field"><span>4 · Submit Payment Entry</span><span>☐</span></div>
      <div class="sfg-field"><span>Dry Run (log only)</span><span>☑</span></div>
      <div class="sfg-field"><span>Processing Time</span><span><span class="sfg-input">07:00:00</span></span></div>
      <div class="sfg-field"><span>Due Date Offset</span><span><span class="sfg-input">0</span> days</span></div>
      <div class="sfg-field"><span>Minimum Amount</span><span><span class="sfg-input">1.000</span></span></div>
      <div class="sfg-field"><span>Max Parties Per Run</span><span><span class="sfg-input">25</span></span></div>
      <div class="sfg-field"><span>Last Execution</span><span>26-07-2026 07:00:11</span></div>
    </div>
  </div>
  <p class="sfg-caption">Illustration — the four steps, the plan sentence the form builds, and the
  schedule fields. <span class="sfg-illus-tag">not a screenshot</span></p>

  <div class="sfg-warn"><b>Start safe.</b> Enable <b>step 1 only</b> with <b>Dry Run</b> ticked.
  Read the summary it reports, then widen. Step 4 moves money.</div>

  <p>Every run reports through the chime, the desk bell, and email where an outgoing account
  exists. Quiet runs stay quiet unless you ask otherwise.</p>

  <!-- ── guards ────────────────────────────────────────────────────────────── -->
  <h2 id="guards">Guards &amp; thresholds</h2>
  <table>
    <thead><tr><th>Setting</th><th>What it does</th></tr></thead>
    <tbody>
      <tr><td>Due date offset</td><td>Include invoices due within N days. 0 = due today or earlier</td></tr>
      <tr><td>Minimum amount</td><td>Skip a supplier below this total — keeps rounding residue out</td></tr>
      <tr><td>Ageing over</td><td>Only invoices more than N days past due</td></tr>
      <tr><td>Max parties per run</td><td>Hard cap, so a wrong filter cannot raise hundreds of advices</td></tr>
      <tr><td>Advice threshold</td><td>Skip a supplier whose total would exceed this</td></tr>
      <tr><td>Submit threshold</td><td>Above this, advices stay drafts even with auto-submit on</td></tr>
      <tr><td>Payment Entry threshold</td><td>Above this, Payment Entries stay drafts</td></tr>
      <tr><td>Exclude foreign currency</td><td>Skip suppliers with non-company-currency invoices</td></tr>
      <tr><td>Ignore hold / blocked</td><td>Off (recommended) means on-hold suppliers are skipped</td></tr>
    </tbody>
  </table>
  <div class="sfg-note">One supplier can be excluded from all runs by ticking <b>Disable Automatic
  Payment</b> on the Supplier — no configuration change needed.</div>

  <!-- ── statuses ──────────────────────────────────────────────────────────── -->
  <h2 id="status">Statuses</h2>
  <p>
    <span class="pill grey">Draft</span>
    <span class="pill orange">Pending Approval</span>
    <span class="pill blue">Approved</span>
    <span class="pill amber">Partly Paid</span>
    <span class="pill green">Paid</span>
    <span class="pill red">Cancelled</span>
  </p>
  <p>The Payment Advice list is colour-coded by these, marks scheduler-raised advices with an
  <i>auto</i> pill, and has an <b>Awaiting Payment Entry</b> button for the approved-but-unpaid
  queue.</p>

  <!-- ── skips ─────────────────────────────────────────────────────────────── -->
  <h2 id="skips">Why a party was skipped</h2>
  <table>
    <thead><tr><th>Message</th><th>Meaning &amp; fix</th></tr></thead>
    <tbody>
      <tr><td>No default payable account</td><td>Set a payable account on the Supplier or the Company</td></tr>
      <tr><td>Already allocated on a live Payment Advice</td><td>Open that advice, or cancel it to free the invoices</td></tr>
      <tr><td>Below the minimum advice total</td><td>Rounding residue — lower the minimum to include it</td></tr>
      <tr><td>Party is on hold</td><td>Release the supplier, or tick "Include on-hold parties"</td></tr>
      <tr><td>Party is disabled</td><td>Re-enable the supplier</td></tr>
      <tr><td>Automation disabled on the party</td><td><b>Disable Automatic Payment</b> is ticked on the Supplier</td></tr>
      <tr><td>Over the per-run cap</td><td>Comes next run, or raise Max Parties Per Run</td></tr>
      <tr><td>Advices left as drafts — a PM Workflow governs approval</td><td>Intended: approvers submit, not the scheduler</td></tr>
    </tbody>
  </table>

  <!-- ── troubleshooting ───────────────────────────────────────────────────── -->
  <h2 id="fix">Troubleshooting</h2>
  <table>
    <thead><tr><th>Symptom</th><th>Cause &amp; fix</th></tr></thead>
    <tbody>
      <tr><td>Bank / IBAN / SWIFT blank on the advice</td><td>The supplier has no Bank Account record. Create one; the details then fetch automatically</td></tr>
      <tr><td>"Payment Amount exceeds the total payable"</td><td>You authorised more than the references total. Fetch more or lower the amount</td></tr>
      <tr><td>Reference picker empty</td><td>Set Company first — the picker is scoped to it, and to submitted documents</td></tr>
      <tr><td>"Cancel Payment Entry … before cancelling this advice"</td><td>Cancel the Payment Entry first</td></tr>
      <tr><td>No chime after a run</td><td>Click once anywhere per session — browsers block sound until you interact</td></tr>
      <tr><td>No email summary</td><td>No outgoing Email Account configured. Chime and bell still work</td></tr>
      <tr><td>Scheduled run never fires</td><td>Check Enabled, the weekday ticks, the time, and that step 1 is on</td></tr>
    </tbody>
  </table>

  <p style="color:var(--text-muted);font-size:.82rem;margin-top:2rem">
    Enfono Technologies · sf_trading · a public copy of this guide is served at
    <code>/user-guide</code>.
  </p>
</div>
`;
}
