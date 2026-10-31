# Technical Guide — for your system administrator

This guide is for the person inside Steel Force who looks after the system day to day.

It covers what you can safely change yourself in the live system, the exact steps to do it, how to check you got it right, and where the line is — the short list of jobs that must come to Enfono. Follow it and you should not need us for routine work.

**How to use it.** Use the search box in the sidebar. Type what you are looking at — a screen name, a role, or the error message on your screen.

A companion document, the **SF Trading Admin Handbook** at `/user-guide`, explains what the custom features do. This guide is about running the system. Where the two overlap, this one points you there.

---

## 1. Before you change anything

Four rules. They exist because every one of them has cost somebody a day.

**1. Take a backup first, for anything that touches more than one record.**
Go to **Backups** (search `Download Backup`), press **Download Backup**, wait for it to build, and keep the file. For a single record you do not need this.

**2. Change one thing at a time, then check it.**
Every section below ends with a "check it worked" step. Do that step before moving on. Two changes at once means you cannot tell which one broke something.

**3. Do the risky things outside business hours.**
Anything that affects everybody — roles, workflows, naming series, company accounts — is best done early morning or after close, with one person to test it before staff arrive.

**4. If a change would affect posted accounts or stock, stop and ask us.**
Section 11 lists these. Adding a user is safe. Repointing an account that thousands of entries already use is not.

### What you cannot break by looking

Reading is always safe. Open any report, any list, any record. Use filters freely. Nothing in this system changes because you looked at it.

---

## 2. Your routine

A short list so nothing is discovered a month late.

| How often | Check | Where | What good looks like |
|---|---|---|---|
| Daily | Backups ran | `Download Backup` | Yesterday's file is there |
| Daily | Automatic jobs ran | `Scheduled Job Log` | No repeated failures |
| Daily | Errors | `Error Log` | Nothing new that mentions a user's action |
| Weekly | Approvals not stuck | `Work Flow Approval` report | Nothing sitting for days |
| Weekly | Overdue money | `Invoice Due and Overdue Report` | Matches what accounts expect |
| Weekly | Users who left | `User` list | Leavers are disabled |
| Monthly | Stock oddities | `Reorder Recommendation`, `Stock Balance` | No item with value but no quantity |
| Monthly | Disk and size | `System Health` or ask us | Nothing near full |

---

## 3. Users and access

### 3.1 Add a person

1. Search `User` → **Add User**.
2. Enter email, first name, last name. Save. The system emails them to set a password.
3. Under **Roles**, tick the roles they need — or set a **Role Profile**, which is a saved bundle of roles.
4. Save.

**Then give them their branch.** Roles say *what* a person may do; the branch says *which data* they see. Open the **Branch Configuration** for their branch, add them to the Users table, tick `is_default_branch` if this is their main branch, and save. Permissions are created for them automatically.

**Check it worked** — ask them to log in and open a new Sales Invoice. The warehouse and cost centre should already be filled in with their branch's values.

### 3.2 Someone has left

**Disable, never delete.** A deleted user breaks the history on every document they touched.

1. Open the `User`.
2. Untick **Enabled**. Save.
3. Remove them from the Users table of any Branch Configuration.
4. If they were an approver, put somebody else in their place — see 4.3 — otherwise documents will queue behind a person who cannot log in.

### 3.3 Someone cannot see something

This is the most common call, and you can nearly always solve it.

Work down this list in order:

1. **Do they have the role?** Open the User, look at Roles.
2. **Is it their branch?** Open the Branch Configuration for their branch. Is the warehouse, cost centre or payment mode they need listed?
3. **Check their permissions.** Search `User Permission`, filter by their user. This is the list of records they are limited to. If the warehouse they need is missing here, the Branch Configuration did not grant it — add it there and save again, which re-applies permissions.
4. **Are they on two branches?** Only one Branch Configuration may have `is_default_branch` ticked for a person. Two defaults produce confusing results.

**Do not fix this by adding User Permission rows by hand.** They will be removed the next time that Branch Configuration saves. Fix it at the Branch Configuration.

### 3.4 Roles worth knowing

| Role | Gives |
|---|---|
| System Manager | Everything. Give it to as few people as possible |
| Accounts Manager | Accounts setup and reports |
| Accountant | Approves payments |
| Finance Manager | Approves payments, configures payment automation |
| Purchase User / Purchase Assistant | Raises purchase documents |
| Stock Manager | The only role that can accept stock transfers waiting in *Pending Acceptance* |
| Sales User | Raises sales documents |
| B2B Creator | May create B2B company customers from the quick-create dialog |
| Driver | Delivery staff |

### 3.5 Who did what

- **Every document** has a **Version** history. Open it and scroll down to see who changed which field and when.
- **Logging in as somebody else** (impersonation) is recorded in the `Activity Log` **with the reason typed by the administrator**. If you ever need to prove who acted, that is where to look.
- **Deleted documents** are kept in `Deleted Document` — nothing is truly gone.

---

## 4. Approvals

Approvals on this system are run by **Permission Manager**. A workflow decides which states a document passes through, who may move it, and when it is finally submitted.

Open `PM Workflow` to see them. Each one has **States** (where a document can sit) and **Transitions** (who may move it from one state to the next).

### 4.1 What is switched on today

| Document | Approval |
|---|---|
| Journal Entry | Yes |
| Payment Advice | Yes |
| Payment Request | Yes, hidden from the approvals page |
| Purchase Invoice | Yes |
| Purchase Order | Yes |
| Stock Entry (Material Transfer) | Yes, and editing is restricted |
| Payment Entry | Configured but switched off |
| Purchase Receipt | Configured but switched off |

### 4.2 Reading a workflow

Open the PM Workflow. Two tables matter.

- **States** — the list of places a document can sit. Each one has a document status: 0 means draft, 1 means submitted, 2 means cancelled. A state with status 1 is the finish line.
- **Transitions** — one row per allowed move: from which state, by which role, to which state. **A role that is not in this table cannot act, whatever else it can do.**

So if somebody says "I have the role but no button appears", look for their role in Transitions for the state that document is in.

### 4.3 Change who approves

1. Open the PM Workflow.
2. Find the Transition row for the step you want to change.
3. Change the role on that row, or add a row for another role.
4. Save.

**Careful:** these transitions can be regenerated from the Approval Group configuration. If somebody later rebuilds the workflow, rows added by hand can be lost. If a change matters, tell us so we make it part of the generated setup rather than a manual row.

### 4.4 A document will not submit

**"… cannot be submitted directly — it still needs approval."** The document is in a state that is not the finish line. Use the workflow buttons, not Submit. This is the system working as designed.

**Nothing happens when the approver clicks.** Check the Transitions table for their role and that state (4.2).

**Stock Entries pile up in Pending Acceptance.** Only Stock Manager can accept them. Either give the role to the right person or have a Stock Manager clear the queue. This restriction is deliberate.

**Reminders never arrive.** The workflows have an escalation period set but no escalation email address. Set one on the workflow, or ask us to.

### 4.5 Approvals you cannot see

The approvals page hides workflows ticked **Hide from Approvals Page** — Payment Request is one today. That is a display choice, not a permission. The documents still exist in their lists.

---

## 5. Masters you own

### 5.1 Items

| Field | Why it matters |
|---|---|
| Item Group | Carries the margin percentage used by the selling price floor |
| Stock UOM | Cannot be changed once the item has moved. Get it right at creation |
| Lead Time (days) | Used by the Reorder Recommendation report |
| Safety Stock | The report never suggests less than this |
| Minimum Order Qty | Purchase suggestions are rounded up to it |
| `custom_enforce_min_price` | Forces a fixed price-list floor instead of a calculated one |

**A new item** needs at minimum: item code, item name, item group, stock UOM, and whether it is a stock item.

**Do not change the UOM or the "is stock item" tick after the item has transacted.** Create a new item instead.

### 5.2 Customers

**Customer groups follow two rules on this site:**

- A customer with a **credit-limit company set** belongs in **Credit Customer**.
- A customer with a **VAT registration number but no credit-limit company** belongs in **Company**.
- Customers with neither are left as they are.

**Giving a customer credit**

1. Open the Customer.
2. Add a row to **Credit Limits**: the company and the amount.
3. Save. The system adds your own branch to the **Branch Access** table automatically.
4. Add rows to Branch Access for any other branch allowed to sell to them on credit.
5. Move the customer into the **Credit Customer** group.

**Check it worked** — raise a Sales Invoice at that branch, set payment mode to Credit, and the customer should appear in the list.

**VAT numbers** — 15 digits, starting and ending with `3`. A customer with a VAT number cannot be saved without an attachment, which is the VAT certificate. Duplicates are blocked unless somebody with the override role ticks **Allow Duplicate VAT** and gives a reason.

### 5.3 Suppliers

| Setting | Effect |
|---|---|
| Default Payable Account | Without it, and without a company default, automatic payment skips this supplier |
| Bank Account record | Where the IBAN and SWIFT on a Payment Advice come from. No record means blank fields |
| On Hold | Payment runs skip them |
| Disable Automatic Payment | Keeps one supplier out of every scheduled run, without changing any configuration |

### 5.4 Prices

- **Price List** — one per currency and purpose. Selling prices live in `Item Price` rows against a price list.
- To change a price, edit or add the `Item Price` row, do not edit history.
- The **selling price floor** stops staff selling below cost. It is `max(last purchase rate, warehouse valuation) × (1 + the Item Group margin)`. Raise or lower the margin on the **Item Group** to move the floor for a whole category.

---

## 6. Money settings

Take extra care here. These feed posted accounts.

### 6.1 Safe to change yourself

| Setting | Where | Notes |
|---|---|---|
| Mode of Payment | `Mode of Payment` | Add a new card machine or wallet. Then add it to the branches that use it, in Branch Configuration |
| Branch payment modes | Branch Configuration | Which modes each branch may take. Flags for returns and cheques live here |
| Bank Account | `Bank Account` | For suppliers and for your own accounts |
| Customer credit limits | Customer | See 5.2 |
| Payment terms | `Payment Terms` | Due date rules |

### 6.2 Ask us first

| Setting | Why |
|---|---|
| Company → Default Payable / Receivable Account | Thousands of posted entries point at these |
| Company → **Default Expense Account** | On this site it is the Cost of Goods Sold account. Purchase Receipts post their small valuation rounding differences there. Changing it changes where future rounding lands — it is a reasonable thing to want, but we should check what else uses it first |
| Stock Adjustment Account | Used by stock write-offs other than a Stock Reconciliation. It must not be a COGS account |
| Stock Reconciliation Difference Account | The one Difference Account a Stock Reconciliation (Purpose: Stock Reconciliation) may use. Accounts tab, under Default Cost of Goods Sold Account. **Without it no Stock Reconciliation can be saved**, and the document may use no other account. Opening Stock is unaffected |
| Chart of Accounts structure | Renaming or moving accounts affects every report |
| Tax templates and rates | VAT is filed from these |
| Naming series, mid-year | Changing a series after documents exist creates gaps and confuses auditors |

### 6.3 VAT and tax templates

Bahrain VAT is applied through **Purchase/Sales Taxes and Charges Templates**. Two custom flags exist on the purchase side:

- **For Foreign Currency** — the template to use when the supplier invoices in another currency.
- **For No Tax ID Supplier** — the template for a supplier with no VAT number.

Pick the right template on the document. If you think a rate or a template is wrong, that is a change to bring to us — VAT returns are filed from this.

---

## 7. Printing and documents

### 7.1 Letter Head

The artwork at the top and bottom of printed documents.

1. Search `Letter Head`.
2. Open the default one, or create one and tick **Is Default**.
3. Put your header image in the content and your footer image in the footer.
4. Save.

Keep the images the same width, around 1000 pixels wide, or the header will look different from the footer.

**Check it worked** — print any invoice.

### 7.2 A company printing its own layout

Each company can print a document type with its own format without affecting the others.

1. Open the **Company**.
2. In the **Print Formats** grid, add a row: document type, and the print format for it.
3. For delivery notes printed from an invoice, also set **custom_delivery_note_print_format**. It is a separate field, and the **Print DN** button needs it.
4. Save.

### 7.3 Customer statements

1. Open the **Accounts Receivable** report.
2. Put **one** customer in the Party filter.
3. Print, or export as PDF, and pick **Statement of Account**.

The statement carries the letterhead artwork whether or not you tick "With Letter head", so it looks the same either way.

**If you want to change a statement layout** — tell us. The statement is a Print Format record you *can* edit in the desk, but the same layout also exists as a file in the app. If you edit only the record, the next update from us can overwrite your change. Ask, and we will change both.

### 7.4 Email

- Outgoing email needs an **Email Account** with **Enable Outgoing** ticked. Without one, the system still notifies people in the desk and by the bell icon, but sends no email.
- To test: open the Email Account and use the built-in test, or trigger a notification and check `Email Queue`.
- Automatic messages come from **Notification** records. You can edit the wording and the recipients there.

---

## 8. Reports and dashboards

You do not need us to build a view. Most questions are already answered.

| Question | Report |
|---|---|
| How much came in and went out? | Cash Flow Money In vs Money Out |
| Which vouchers make up that figure? | Cash Flow Detail |
| What does this customer owe, on paper? | Customer Statement of Account |
| What is overdue, sales and purchases? | Invoice Due and Overdue Report |
| What do we owe suppliers, by date? | Supplier Due Payment Report |
| How was this invoice paid? | Mode of Payment Invoice Wise |
| What should we reorder? | Reorder Recommendation |
| What happened in the cash drawer today? | DCR Report, DCR Detailed |
| What is waiting for approval? | Work Flow Approval |
| Which cheques are coming due? | PDC Report |
| How was this cost reached? | COGS Cost Breakdown, Transfer Cost Breakdown |
| Which supplier should we buy from? | Supplier Quotation Comparison Extended |

**Business Dashboard** (`/app/business-dashboard`) shows sales, expenses, profit, cash and bank balances and trends on one screen.

**Things you can do yourself in any report:** change filters, sort, add or remove columns from the menu, save a filter set for reuse, export to Excel or CSV, and share a link.

**If a report is empty,** the Company filter is almost always the reason. Set it, then widen the dates.

---

## 9. Housekeeping in production

### 9.1 Backups

- Search `Download Backup`. The list shows what exists; **Download Backup** builds a fresh one.
- Take one before any bulk change, and keep a copy off the server for anything important.
- Automatic backups run on the server. If the list has nothing recent, tell us — that is a server matter.

### 9.2 When something goes wrong, look here first

| Log | What it tells you |
|---|---|
| `Error Log` | Anything that failed with a traceback. The newest entries matter most |
| `Scheduled Job Log` | Whether the automatic jobs ran, and which failed |
| `Email Queue` | Whether a message was sent, and why not |
| `Version` (on a document) | Who changed which field, and when |
| `Activity Log` | Logins, and impersonation with its reason |

You are not expected to fix a traceback. You *are* the person best placed to tell us: which document, which user, what time, and what they were doing.

### 9.3 Automatic jobs

Two run on their own:

| Job | When | Does |
|---|---|---|
| Overdue invoice digest | Daily | Notifies the people who should chase money |
| Payment automation | Every few minutes | Runs the Payment Automation Settings whose day and time have arrived |

To check either: search `Scheduled Job Type`, find it, and look at **Last Execution**. If every job is stale, the scheduler itself is stopped — that is one for us.

### 9.4 Stock housekeeping

**An item shows value but no quantity.** Left over from an earlier migration. Fix it with a **Stock Reconciliation**: set quantity and rate to zero for those item and warehouse rows, The Difference Account is filled from the Company's **Stock Reconciliation Difference Account** (Accounts tab) and cannot be changed on the document, so check that account is not a COGS account first. Take a backup first.

**Reorder levels.** Nothing on this site has one configured yet. The **Reorder Recommendation** report proposes a level and a quantity per item and warehouse from real sales history. Read it before setting anything, and remember: transfers between your own warehouses are deliberately excluded, and most items fall back to the default lead time in the filter.

**Negative stock.** If a warehouse goes negative, stop and tell us. It usually means documents were posted out of order and the fix has accounting consequences.

**Cost breakdowns.** *COGS Cost Breakdown* (View > COGS Calculation on a Delivery Note or Sales Invoice) and *Transfer Cost Breakdown* (View > Transfer Cost Breakdown on a Material Transfer) replay the Stock Ledger under Moving Average and show which documents a cost was made of (`sf_trading/cogs_breakdown.py`). They are read only and recompute on every open, so a backdated entry shows its new cost the next time the report opens. Read the **Check** column first: *Replay differs* means the replay and the ledger disagree by more than rounding (a thousandth of the line, never under a fil and a half), and the gap is its own row. On the live ledger 99.98% of 18,883 lines agreed. The ones that do not are stock that left an empty bin (the entries written by the migration, before Allow Negative Stock was switched off) and a handful of lines with an unexplained gap; send us the voucher number of any other. The reports apply the same User Permissions the desk lists do, so a user restricted to a warehouse or branch sees only the vouchers inside it.

*Where to start when the books look wrong:* COGS Cost Breakdown with **Only Differences** lists the vouchers whose ledger cost and GL disagree (on the live data from June to October every one is a credit note with Update Stock; in the largest, the stock entry was reposted after its GL was written). **Returns: Only** with **Only Lines To Review** lists returns restocked at a different cost than the sale. Transfer Cost Breakdown with **Only Gaps** lists transfers whose receiving entry is worth something other than what left, which is how stock that arrived at zero value shows up (nine such lines on prod, all last modified on 7 August). The cause is in the data, not the report; fix it with a Stock Reconciliation or a repost, not here.

**Supplier Quotation Comparison Extended** (`sf_trading/sf_trading/report/supplier_quotation_comparison_extended/`) ranks the quotations of each Request for Quotation. Each quotation line is reduced to one comparable figure: the net rate per stock unit in company currency, plus the landed charges. They are the rows of the quotation's own `taxes` (Purchase Taxes and Charges) with `category` Valuation or Valuation and Total, in `base_tax_amount_after_discount_amount` (Deduct negative), shared over the quotation's lines by `base_net_amount / base_net_total`; the standard Purchase Taxes and Charges Template plus a Tax Rule put them on a quotation, so no sf_trading setting is involved. The `Landed Charges` filter can leave them out and `Extra % on Imports` adds a typed percent of the price on imports. A quotation is an import when it is in a currency other than the company's, or from a supplier whose country is set and is not the company's (the currency test is the one `purchase_order_procurement.is_import` uses). `View > Supplier Quotation Comparison Extended` on a submitted Request for Quotation (`public/js/rfq_quotation_comparison.js`, `doctype_js`) opens the report on that request; the same button is on a Supplier Quotation (its request, else the quotation alone) and on a submitted purchase Material Request (`material_request` filter), from `public/js/quotation_comparison_buttons.js`. `Recommend By` = Weighted Score ranks by a 0 to 100 score (`score_group`: price = lowest landed / this landed, lead = shortest stated / this stated, validity = days left to Valid Till capped at 30; a part nobody states is dropped from every score; weights from the `Weight` filters, 70/20/10 by default); `allocate_award` then walks the ranking giving each quotation what it offered up to the requested quantity (`award_qty`, `award_total`, `shortfall_qty`), and `recommended_orders` / `make_purchase_orders` order exactly those quantities (`limit_to_awards` cuts a part-awarded line and recalculates). `award_override.py` (Purchase Order `validate`, after the procurement rules) runs the report's own comparison for each Request for Quotation behind the order's lines, always at Lowest Cost with the quotation charges, and writes the lines whose quotation is awarded nothing into `custom_not_recommended_items`; without `custom_award_override_reason` the save is refused (a verbal order, an internal supplier or `doc.flags.ignore_award_override` is not checked; an empty comparison, as for a user who cannot read the quotations, is not a departure). The `RFQ Quotation Comparison Statement` print format (a standard Jinja format in `sf_trading/print_format`) reads `comparison_statement.py`, a Jinja method in `hooks.py`, so every figure on the page is the report's own. Per item the lowest figure is recommended; ties go to the stated shorter lead time, then the earlier quotation. Expired, stopped and unpriced quotations are never recommended, and one supplier competes once per item. The Procurement Rule column reads `purchase_order_procurement.rules` and counts different suppliers with a submitted quotation, expired included, as the Purchase Order rule does; for a local order the amount tested is the recommended quotation's own grand total. Last purchase rate is `Item.last_purchase_rate`, one read. `get_recommended_orders` / `make_purchase_orders` recompute the recommendation server side from the filters and raise drafts with core's `make_purchase_order(args={"filtered_children": ...})`, so the Purchase Order rules run on them; a line already on a live Purchase Order is skipped. Read only otherwise; every figure is recomputed on each open. Tests: `tests/test_supplier_quotation_comparison_extended.py`.

### 9.5 Closing a period

Month-end and year-end close, opening entries and fiscal years affect every report. Do not do these alone the first time. Ask us to walk through the first one with you, then it is yours.

---

## 10. Troubleshooting

Search the exact words on your screen. If they are not here, the message is probably in the **SF Trading Admin Handbook** at `/user-guide`.

### 10.1 People and access

**"Not permitted" or a blank list.** Role, then branch, then User Permission — in that order. See 3.3.

**A new user sees nothing.** They were not added to a Branch Configuration. Add them and save.

**Somebody sees another branch's data.** They are listed on two Branch Configurations, or `is_default_branch` is ticked twice.

**A user cannot log in.** Check **Enabled** on the User, then have them use "Forgot Password". If no email arrives, check the Email Account and `Email Queue`.

### 10.2 Documents

**"Document has been modified after you have opened it."** Somebody else saved it while it was open, or the same person has it open twice. Refresh and redo the change.

**"Cannot save: insufficient warehouse stock for …"** The quantity is more than that warehouse holds. Reduce it, change warehouse, or remove the row.

**"No Credit Limit" when choosing a customer.** No credit limit exists for that company on the customer. Add one (5.2).

**"At least one Branch must be added in Branch Access when a Credit Limit is set."** Add a branch row on the customer.

**A submitted document is wrong.** Cancel and amend it — never ask for it to be deleted. Cancelling keeps the audit trail; deleting destroys it.

### 10.3 Printing

**Header or footer missing on a PDF.** The statement layouts draw their own artwork, so first check there is a default Letter Head with images in both the header and the footer. If you were printing something else, tick **With Letter head** in the print dialog.

**Wrong layout for one company.** Fix that company's Print Formats row (7.2).

**"No Delivery Note print format set on company …"** Set `custom_delivery_note_print_format` on the Company.

**Arabic text looks wrong in a PDF.** Tell us — that is a font matter on the server.

### 10.4 Numbers that look wrong

**A report does not match another report.** Check whether one excludes internal transfers and the other does not. On this site, transfers between warehouses and between your own bank accounts are large enough to change a total on their own.

**Cost of Goods Sold has small unexplained entries from Purchase Receipts.** Expected. When quantity times rate does not divide exactly, the difference — usually a few fils — posts to the company's Default Expense Account, which here is COGS. It is not a mistake in the data. See 6.2 if you want it moved.

**A supplier balance includes journal entries.** Correct. Payment Advice pulls journals in as well as invoices.

### 10.5 Nothing is happening automatically

Check `Scheduled Job Type` → **Last Execution**, then `Scheduled Job Log` for failures, then tell us if every job is stale.

---

## 11. What to leave to us

Not because of secrecy — because these need server access or can damage posted data.

| Job | Why |
|---|---|
| Updating the apps, or any code change | Needs server access and a maintenance window |
| `bench migrate`, `bench build`, restarting services | Server level |
| Site settings, domains, SSL, email server setup | Server level |
| Creating or deleting a site | Server level |
| Editing data directly in the database | Skips every validation the system has |
| Bulk changes to posted documents | Backups and checks first; usually scripted |
| Repointing company default accounts | Everything already posted points at them (6.2) |
| Changing naming series once documents exist | Creates gaps and audit questions |
| Deleting submitted documents | Cancel and amend instead |
| Repairing negative stock or valuation | Accounting consequences |
| First month-end and year-end close | Once, together, then it is yours |
| Changing a report or print format layout | So the file and the record stay in step (7.3) |

### How to raise something with us

Send these five things. With them we usually fix it without a call:

1. **Which site** — for example `sft.enfonoerp.com`.
2. **What you did** — the steps, in order.
3. **The exact message** — a screenshot of the whole screen, not just the red text.
4. **Which document** — the name, for example `ACC-SINV-2026-01490`.
5. **When, and which user** — time and the login it happened on.

If it is stopping work, say so plainly in the first line.

---

## 12. Quick reference

### Where things live

| What | Search for, or go to |
|---|---|
| Users | `User` |
| Bundles of roles | `Role Profile` |
| Who is limited to what | `User Permission` |
| Branch setup | `Branch Configuration` |
| Approvals | `PM Workflow` |
| Approvals waiting | `Work Flow Approval` report |
| Letterhead | `Letter Head` |
| Print layouts | `Print Format` |
| Company settings | `Company` |
| Payment modes | `Mode of Payment` |
| Tax templates | `Sales Taxes and Charges Template`, `Purchase Taxes and Charges Template` |
| Prices | `Item Price` |
| Backups | `Download Backup` |
| Errors | `Error Log` |
| Automatic jobs | `Scheduled Job Type`, `Scheduled Job Log` |
| Email sending | `Email Account`, `Email Queue` |
| Business overview | `/app/business-dashboard` |
| Feature handbook | `/user-guide` |

### Safe / careful / ask us

| Safe on your own | Careful, outside busy hours | Ask us |
|---|---|---|
| Add or disable users | Change workflow approvers | Any code or app update |
| Assign roles and branches | Add a payment mode | Company default accounts |
| Customer credit limits and branch access | Change an Item Group margin | Tax rates and templates |
| Supplier bank details and payable account | Letterhead artwork | Naming series changes |
| Item and price maintenance | Print format per company | Database edits, bulk changes |
| Run and export any report | Notification wording | Negative stock, valuation repairs |
| Take a backup | Stock Reconciliation for stranded value | Period close, the first time |
