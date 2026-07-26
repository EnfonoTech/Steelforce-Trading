# SF Trading — User Guide

Trading feature pack for Steel Force, built on top of ERPNext. This guide documents every configuration screen and behavior this app adds, starting with the foundational setup (Branch Configuration) and working up through everything that depends on it.

This same content is available inside the Frappe desk as a searchable page — open the Awesomebar (`Ctrl+G` / `Cmd+G`) and type **SF Trading User Guide**.

## Table of Contents

- [1. Overview](#1-overview)
- [2. Core Configuration](#2-core-configuration)
  - [2.1 Branch Configuration](#21-branch-configuration)
  - [2.2 Customer Branch Access](#22-customer-branch-access)
  - [2.3 Inter Company Branch](#23-inter-company-branch)
  - [2.4 Company Print Format](#24-company-print-format)
- [3. Sales Invoice Features](#3-sales-invoice-features)
- [4. Stock & Material Request Features](#4-stock--material-request-features)
- [5. Quotation Features](#5-quotation-features)
- [6. Customer, Supplier & Pricing Features](#6-customer-supplier--pricing-features)
- [7. Global Features](#7-global-features)
- [8. Reports](#8-reports)
- [9. Supplier Payments — Payment Advice](#9-supplier-payments--payment-advice)
  - [9.1 Raise one advice](#91-raise-one-advice)
  - [9.2 Batch by supplier — the Builder](#92-batch-by-supplier--the-builder)
  - [9.3 Approval](#93-approval)
  - [9.4 Creating the Payment Entry](#94-creating-the-payment-entry)
  - [9.5 Scheduled runs](#95-scheduled-runs)
  - [9.6 Why a party was skipped](#96-why-a-party-was-skipped)
- [10. Troubleshooting / FAQ](#10-troubleshooting--faq)

---

## 1. Overview

SF Trading layers branch-based multi-company trading controls on top of standard ERPNext: every user works out of a **Branch**, and a **Branch Configuration** record decides which company, warehouses, cost centers, payment modes, and roles that branch grants its users. Almost every other feature in this app — credit control, payment-mode restrictions, inter-company invoicing, stock validation, print formats — reads from that one setup screen. Configure it first; everything else follows from it.

---

## 2. Core Configuration

Set these up in this order — each one either depends on the one before it, or is read by the day-to-day features documented in later sections.

### 2.1 Branch Configuration

**Doctype:** `Branch Configuration` · **Where:** one record per Branch, named after the branch itself.

This is the master setup record for a branch. It links the branch to a company and lists the warehouses, cost centers, and payment modes its users are allowed to use, plus which users belong to the branch and what role profile they get.

| Field | Type | Purpose |
|---|---|---|
| `branch` | Link → Branch (required, unique) | The branch this record configures. The document name is auto-set to the branch name. |
| `company` | Link → Company | The company this branch trades under. |
| `warehouse` (table) | Branch Configuration Warehouse | Warehouses this branch can use. |
| `cost_center` (table) | Branch Configuration Cost Center | Cost centers this branch can use. |
| `mode_of_payment` (table) | Branch Configuration Mode of Payment | Payment modes allowed at this branch (each row also flags `for_return` / `for_pdc`). |
| `user` (table) | Branch Configuration User | Users assigned to the branch, with an optional Role Profile and an `is_default_branch` flag. |

Warehouse and Cost Center pickers are automatically filtered to the selected `company`; changing `company` clears both tables (values may belong to the old company).

**What happens on save:** for every user listed in the `user` table, SF Trading automatically creates User Permission records for that Branch, Company, every listed Warehouse/Cost Center/Mode of Payment, and the branch's Letter Head (if set on the Branch). If a Role Profile is set on the user row, that profile's roles are granted directly. Only one Branch Configuration per user should have `is_default_branch` ticked — that one supplies the user's default Branch/Company/Warehouse/Cost Center/Mode of Payment values everywhere in the desk.

Removing a user from the table (or removing a warehouse/cost center/payment mode) automatically revokes the User Permissions that record had granted — but only if no *other* Branch Configuration still grants the same thing to that user.

**Validation:** saving fails if a listed Warehouse or Cost Center belongs to a different company than the one set on the record ("Warehouse … belongs to company …, not …").

**Example**

> Create a Branch Configuration for branch **Riyadh**:
> - `company` = SF Trading LLC
> - Warehouse: Riyadh Stores
> - Cost Center: Riyadh - SFT
> - Mode of Payment: Cash
> - User: `riyadh.manager@sf.com`, Role Profile = *Branch Manager*, `is_default_branch` = ✓
>
> On save, `riyadh.manager@sf.com` gets User Permissions restricting them to Branch = Riyadh, Company = SF Trading LLC, Warehouse = Riyadh Stores, Cost Center = Riyadh - SFT, Mode of Payment = Cash — and these become their defaults. When they open a new Sales Invoice, it defaults to warehouse "Riyadh Stores" and cost center "Riyadh - SFT", the invoice's payment table only offers Cash-type modes from this list, and the branch's letter head is applied automatically.

### 2.2 Customer Branch Access

**Doctype:** `Customer Branch Access` (child table) · **Where:** the "Branch Access" grid on the **Customer** form.

Restricts which branches are allowed to transact with a **credit customer** (a customer with a credit limit). This exists so a customer given credit terms at one branch can't automatically be invoiced from every other branch too.

| Field | Type | Purpose |
|---|---|---|
| `branch` | Link → Branch (required) | A branch permitted to deal with this customer. |

**Automatic behavior:** the moment a Customer's credit limit goes from zero/blank to a positive amount, SF Trading auto-adds the *saving user's own branches* to this table. You only need to add rows manually for extra branches beyond the one that created the credit limit.

**Validation:** a Customer cannot be saved with a positive credit limit and an empty Branch Access table — "At least one Branch must be added in Branch Access when a Credit Limit is set."

**Example**

> On customer **ACME Contracting LLC**, add a Customer Credit Limit row of 50,000. Saving (as the Riyadh branch user) auto-adds **Riyadh** to Branch Access. To let the Jeddah branch also invoice ACME on credit, add a second row: `branch = Jeddah`. ACME will now appear in the customer picker for Credit-mode invoices raised from Riyadh or Jeddah, but not from any other branch.

### 2.3 Inter Company Branch

**Doctype:** `Inter Company Branch` · **Where:** its own list, plus the `inter_company_branch` field that appears on a Sales Invoice when it's billed to an internal/represented company.

Maps each company in the group to the cost center (and, for stock-updating invoices, warehouse) that should be used on the Purchase Invoice that SF Trading auto-creates on the *buying* company's side of an inter-company sale.

| Field | Type | Purpose |
|---|---|---|
| `branch_name` | Data (required, unique) | Name of this inter-company routing setup. Also the document name. |
| `company_cost_centers` (table) | Inter Company Branch Cost Center | One row per company: `company`, `cost_center`, `warehouse`. |

Cost Center and Warehouse pickers on each row are filtered to that row's `company`; changing a row's company clears its cost center/warehouse.

**Validation:** the same company cannot appear twice in `company_cost_centers` ("Duplicate company in Company Cost Centers").

**Example**

> Create an Inter Company Branch **"Central Warehouse Transfer"** with two rows: `SF Trading LLC (Riyadh)` → cost center *Riyadh - SFT*, warehouse *Riyadh Stores*; and `SF Trading LLC (Jeddah)` → cost center *Jeddah - SFT*, warehouse *Jeddah Stores*.
>
> When Riyadh raises a Sales Invoice against Jeddah as an internal customer, set `inter_company_branch = Central Warehouse Transfer` on the invoice (the field only lists setups that have a row for Jeddah). On submit, the auto-created Purchase Invoice for Jeddah uses cost center *Jeddah - SFT* and, if the invoice updates stock, warehouse *Jeddah Stores* — regardless of the logged-in user's own session defaults. If the invoice updates stock and the chosen setup has no warehouse for the buying company, submission is blocked: "Configure Warehouse in Inter Company Branch … for company … to create Purchase Invoice with stock update."

### 2.4 Company Print Format

**Doctype:** `Company Print Format` (child table) · **Where:** the "Print Formats" grid on the **Company** form, plus the single `custom_delivery_note_print_format` field on Company.

Lets each company in the group print a document type with its own layout, without changing the doctype-wide default print format used by every other company.

| Field | Type | Purpose |
|---|---|---|
| `document_type` | Link → DocType (required) | Which transaction doctype this row applies to (e.g. Sales Invoice). |
| `print_format` | Link → Print Format (required) | The print format to use for that doctype, for this company. The picker only offers formats built for the chosen doctype. |

The separate `custom_delivery_note_print_format` field on Company (a single Link, not part of the table) feeds only the invoice's **Print DN** quick button — set it directly on the Company form.

**Example**

> On Company **SF Trading LLC — Jeddah Branch**, add: `Sales Invoice → SFT Jeddah Tax Invoice`, `Delivery Note → SFT Standard Delivery Note`, and set `custom_delivery_note_print_format = SFT Standard Delivery Note`.
>
> Printing any Sales Invoice for that company now defaults to "SFT Jeddah Tax Invoice" instead of the doctype's global default. The **Print DN** button on a submitted invoice opens "SFT Standard Delivery Note" directly — if that field were left blank, the button shows "No Delivery Note print format set on company …" instead. A company with no matching row simply falls back to the normal doctype default.

---

## 3. Sales Invoice Features

Everything below builds on the Branch/Customer configuration in Section 2.

**Warehouse stock check.** Changing an item's `qty`, `warehouse`, or `item_code` checks the row's quantity against actual warehouse stock. If it's not enough, an **Insufficient Stock** message appears, the row's qty is reset to 0, and the row is flagged so saving is blocked ("Cannot save: insufficient warehouse stock for …") until it's fixed or removed. This is a client-side check on normal UI saves.

**Credit control.** Setting `custom_payment_mode = Credit` filters the Customer field to only customers with Branch Access for the current branch (Section 2.2). Selecting a customer with no credit limit for the invoice's company shows **No Credit Limit** and reverts to Cash. If the customer has a prior overdue, unsettled credit invoice, an **Overdue Credit Invoice** warning appears and the save is blocked server-side until it's settled.

**Cheque (PDC).** Selecting `custom_payment_mode = Cheque` checks that the branch has at least one Mode of Payment flagged `for_pdc`; if not, shows **Cheque Not Available** and reverts to Cash.

**Barcode scanning.** Scanning into the header `scan_barcode` field, or a row's `barcode` column, adds a new item row (qty 1) or increments qty on a matching existing row. Unresolved codes show "Cannot find Item with this Barcode." Empty scan rows are cleaned up automatically before save.

**Submit & payment popups.** What happens on submit depends on `custom_payment_mode`:
- **Cheque** — opens a Cheque Payment dialog (cheque date/number + amounts).
- **Cash/Bank, no driver** — opens "Enter Payment Amounts", pre-filled with the branch's allowed payment modes; the entered total must match the outstanding amount.
- **Cash/Bank with a `custom_driver` set** — skips payment collection and submits after a confirmation, deferring payment to the delivery person.
- **Credit** — plain submit confirmation, no payment dialog.

  A driver's unsettled invoice older than their configured `custom_payment_days` (default 1 day) blocks a new Cash-mode invoice for that same driver.

  After submit, a print preview opens automatically with **Print Invoice** (company print format, Section 2.4) and **Print DN** buttons.

**Sales Person auto-fill.** Selecting a customer copies their default Sales Team onto the invoice; editing `custom_sales_person` directly replaces the sales team with a single 100%-allocated row.

**Inline warehouse stock panel.** Focusing an item row shows per-warehouse stock for that item with a **Request Items** button per warehouse, which creates and auto-submits a Material Transfer Request between warehouses.

**Minimum selling price.** Typing a rate below cost triggers a **Selling Price Warning** immediately; saving with a rate below the computed floor (`max(last purchase rate, warehouse valuation) × (1 + Item Group margin %)`, or a fixed price-list floor if `custom_enforce_min_price` is set) is blocked with **Invalid Selling Price**.

---

## 4. Stock & Material Request Features

**Stock Entry.** Opening a brand-new Stock Entry clears the header `from_warehouse`/`to_warehouse` once, so a stale default warehouse doesn't leak into a different transfer.

**Material Request.**
- A colored priority pill (red/yellow/green) reflects `custom_priority`.
- A submitted Material Transfer MR shows a **Transfer Status** table (Requested / Transferred / Pending qty per item, from linked Stock Entries).
- If any item on a Transfer MR still needs purchasing, a **Purchase Request** button creates a linked Purchase-type MR, pre-filling the purchase warehouse from the transfer's source and tagging items with `custom_source_mr`; the resulting Purchase MR shows a banner linking back to the originating Transfer MR.

**Stock Availability.** A **Stock Availability** button on Sales Order, Quotation, Delivery Note, Purchase Invoice/Order/Receipt, Supplier Quotation, and Stock Entry item grids opens a dialog of stock-per-warehouse for the selected item (Sales Invoice instead uses the inline panel from Section 3).

**Returns.** On any return document (Sales Invoice/Purchase Invoice/Delivery Note/Purchase Receipt with `is_return = 1`), typing a positive qty is silently converted to negative — you never need to type the qty as negative yourself. Submitting a Purchase Invoice return (Debit Note) automatically creates and submits a matching Purchase Receipt Return for the same items, capped at what's still returnable.

**Purchase Invoice guard.** A Purchase Invoice cannot be saved if its billed qty against a Purchase Order line would exceed that PO line's ordered qty across all non-cancelled PIs.

---

## 5. Quotation Features

- A **Sales Invoice** button (Create menu) on a submitted, not-yet-Ordered Quotation maps it to a new unsaved invoice; a Quotation that already has a non-cancelled linked invoice shows **Duplicate Invoice** instead.
- A Quotation's `status` (Open / Partially Ordered / Ordered) recalculates automatically whenever a linked Sales Invoice is submitted or cancelled, by comparing quoted vs. invoiced quantities.
- Setting the header `set_warehouse` pushes that warehouse onto every existing item row.

---

## 6. Customer, Supplier & Pricing Features

**Create New Customer / Create New Supplier.** Quick-create dialogs available from draft Sales/Purchase documents. Choosing **B2C Individual** only needs Name + Mobile. Choosing **B2B Company** (only offered to B2B Creator/Manager/System Manager roles) requires a VAT number and, for Saudi Arabia companies, a full address block with format rules:
- VAT: exactly 15 digits, starting and ending with `3` (`3XXXXXXXXXXXX3`)
- Mobile: at least 10 digits
- Postal Code: exactly 5 digits

Duplicate VAT numbers are blocked unless the user has an override role and ticks **Allow Duplicate VAT** with a reason. A Customer with a VAT number cannot be saved without at least one attached file (the VAT document).

**Last Purchase/Selling Rate.** Buttons on purchase and sales document item grids show the last 20 transactions for the focused item, scoped to the document's Cost Center.

**Quick Entry.** A bulk-add dialog (requires `set_warehouse` set first) listing all sales items with live stock and rate for that warehouse — check items, adjust qty/rate, and add them all to the document in one go; entering more than available stock is flagged before you can add it.

---

## 7. Global Features

- **Accounting dimension sync** — changing `branch`/`cost_center`/`project` on a transaction pulls that branch's defaults (letter head, cost center, warehouse) and pushes the current values down onto every item row.
- **Item search** — every item-code field app-wide shows live **Stock: qty (warehouse)** and **Rate: currency amount** next to each search result, and sorts results numerically (so "2MM" sorts before "2.6MM").
- **Workflow Approval badge** — the "Work Flow Approval" workspace shortcut shows a live pending-count badge, refreshing every 60 seconds.

---

## 8. Reports

| Report | Purpose |
|---|---|
| **DCR Report** | Daily cash report summary (Opening Balance, Cash/Credit Sales, VAT, Petty Cash, closing Balance) for a date range, company, and optional cost center. Rows link into DCR Detail. Gross Margin column is System Manager only. |
| **DCR Detail** | Transaction-level drill-down for a specific DCR Report line (Cash Sales, Credit Purchase, VAT variants, Internal Transfers, etc.). |
| **DCR Detailed** | A single day's full cash-flow ledger — every Sales Invoice, Purchase Invoice, and Payment Entry posted that day, split into Cash vs. Bank/Card columns. |
| **Work Flow Approval** | Every document sitting in a "Pending" workflow state, with a bulk **Apply Workflow Action** to approve/reject checked rows at once. |
| **Supplier Due Payment Report** | Supplier payables by due date with overdue days and ageing buckets, plus the **Payment Advice** each invoice already sits on and that advice's status. |
| **Invoice Due and Overdue Report** | Sales and Purchase outstanding together, overdue-only by default, with ageing buckets, reward-free totals and the same Payment Advice columns. |
| **PDC Report** | Cheque Payment Entries (ZATCA payment means 20) with cheque date, posting date and a T-3 reminder date. |
| **Loyalty Rewards Report** | Loyalty reward journals with their linked Sales Invoice, customer and reward % of invoice, plus a customer-wise summary. |

---

## 9. Supplier Payments — Payment Advice


ERPNext's **Payment Request** covers one document per request. It cannot express
*"pay these 21 invoices for A.A. Kothambawala"*. A **Payment Advice** can: one party, many
references, one authorised amount, one approval.

```
Outstanding invoices → Payment Advice → Approval → Payment Entry → Invoice paid
```

**It is not limited to Purchase Invoices.** References may be Purchase Invoices, Sales
Invoices, **Journal Entries**, Expense Claims, Purchase Orders or Sales Orders. Real supplier
balances often contain Journal Entries, and those are pulled in automatically.

Outstanding figures are not recalculated by this app. They come from the same ERPNext engine
the Payment Entry form uses, so part-payments, credit notes and return invoices are already
netted before you see them.

#### Who can do what

| Role | Prepare | Approve | Configure automation |
|---|---|---|---|
| Accounts User | Yes | No | No |
| Accountant | Yes | No | No |
| Finance Manager | Yes | Yes | Yes |
| Accounts Manager | Yes | No | Yes |

### 9.1 Raise one advice

1. **New Payment Advice.** Set Company, Party Type and Party. Every other picker — bank
   account, cost centre, project, and the reference documents themselves — is filtered to that
   company and party.
2. **Get Outstanding Documents** and apply whatever filters you need.
3. **Enter the Payment Amount.** It is allocated **oldest first**. Rows beyond the authorised
   amount stay listed with zero allocated, so the full picture survives. The headline reads,
   for example, *"2,464.713 of 2,464.713 allocated across 3 of 3 references"*.
4. **Submit** — or use the workflow actions where approval is switched on.

An invoice can sit on only **one** live advice, drafts included. If it is already on another
advice you are told which one, so nothing is paid twice.

### 9.1.1 Get Outstanding Documents

The same filter set the Payment Entry form offers, because it runs the same code underneath:

| Filter | Use |
|---|---|
| Posting Date — From / To | Restrict by document date |
| Due Date — From / To | "Pay everything due by month end" |
| Outstanding — Greater / Less | An amount window |
| Cost Center | One cost centre only |
| Outstanding Invoices | On by default |
| Orders To Be Billed | Include unbilled orders |

Use **Outstanding — Greater** to keep rounding residue out. Live data contains invoices with
0.005 outstanding; a floor of 1.000 removes them.

### 9.2 Batch by supplier — the Builder

`/app/payment-advice-builder` — sweep everything payable, tick, and create one advice per
supplier in a single action.

1. **Filters:** company, party type, party, due on or before, ageing over N days, minimum
   advice total, cost centre, branch, include on-hold parties.
2. **Fetch Outstanding.** Suppliers come back grouped, biggest first, each showing invoice
   count, oldest ageing, currency and total. Unpayable parties are listed separately **with the
   reason**.
3. **Tick** a whole supplier or individual invoices inside it. The footer totals your selection
   live.
4. **Advice Options** (optional): mode of payment, company bank account, approver, cost centre,
   remarks, submit-or-not — applied to the whole batch.
5. **Create Advices.** You confirm a count and total first. Drafts by default. Over 15
   suppliers the work moves to a background job and chimes when it finishes. The result panel
   links every advice and lists anything skipped.

A real sweep of prod data returned 54 candidate suppliers, of which 5 were payable — 49
vouchers totalling 32,589.377 — with three suppliers skipped because their totals were 0.01,
0.002 and 0.005.

### 9.2.1 From the Purchase Invoice list

Tick invoices → **Actions → Create Payment Advice (by supplier)**. They are grouped by supplier
and one draft advice is raised each. Anything already on a live advice is skipped and named.
All ticked invoices must belong to one company.

### 9.3 Approval

#### With the PM Workflow (recommended)

```
Draft --Send for Approval--> Pending Approval --Approve--> Approved (submitted)
                                    \--Reject--> Rejected --> Pending Approval
```

| Action | From → To | Who | Requires |
|---|---|---|---|
| Send for Approval | Draft → Pending Approval | Accounts User | — |
| Approve | Pending Approval → Approved | Finance Manager | An attachment |
| Reject | Pending Approval → Rejected | Finance Manager | A comment |
| Send for Approval | Rejected → Pending Approval | Accounts User | — |

Reminder after 3 days, escalation after 7. Whoever prepares an advice cannot approve it. While
the workflow is active the single-approver rule steps aside and scheduled runs stop at drafts,
so approvers stay in control.

#### Without a workflow

Only the user linked to the **Approver** Employee may submit. A System Manager can release a
stuck advice.

### 9.4 Creating the Payment Entry

On a submitted advice press **Create Payment Entry**. The advice's allocations become the
Payment Entry's references and ERPNext computes base amounts, party balance and unallocated
amount.

| Event | Effect on the advice |
|---|---|
| Payment Entry submitted | Stamped with the entry and date; status becomes **Paid** or **Partly Paid**; every reference row's status and outstanding refresh |
| Payment Entry cancelled | Stamp cleared, advice back to **Approved**, rows refreshed — a corrected entry can be raised |
| Advice cancelled | Blocked while its Payment Entry is still submitted. Cancel the entry first |

Two submitted Payment Entries can never claim the same advice, and each invoice lists its
advice under **Connections**.

### 9.5 Scheduled runs

**Payment Automation Settings** — one configuration per company and party type. A run goes as
far as you allow, and each step requires the one before it:

1. Create Payment Advice
2. Submit Payment Advice
3. Create Payment Entry
4. Submit Payment Entry

The form states the plan in words, for example *"Will create advices → submit advices on Mon,
Tue at 07:00"*. Set the weekdays and the time; the run fires on the first scheduler tick at or
after that time, once a day.

- **Dry Run** works out exactly what would happen and reports it without creating anything.
- **Run Now** ignores the schedule and runs immediately.

**Start with step 1 only and Dry Run ticked.** Read the summary, then widen. Step 4 moves money.

Every run reports through the chime, the desk bell, and email where an outgoing Email Account
exists. Quiet runs stay quiet unless you ask to hear about them.

### 9.5.1 Guards and thresholds

| Setting | What it does |
|---|---|
| Due date offset | Include invoices due within N days. 0 = due today or earlier |
| Minimum amount | Skip a supplier below this total — keeps rounding residue out |
| Ageing over | Only invoices more than N days past due |
| Max parties per run | Hard cap, so a wrong filter cannot raise hundreds of advices |
| Advice threshold | Skip a supplier whose total would exceed this |
| Submit threshold | Above this, advices stay drafts even with auto-submit on |
| Payment Entry threshold | Above this, Payment Entries stay drafts |
| Exclude foreign currency | Skip suppliers with invoices outside the company currency |
| Ignore hold / blocked | Off (recommended) means on-hold suppliers are skipped |

A single supplier can be excluded from every run by ticking **Disable Automatic Payment** on
the Supplier — no configuration change needed.

### 9.5.2 Statuses

| Status | Meaning |
|---|---|
| Draft | Being prepared |
| Pending Approval | Approver selected, not yet submitted |
| Approved | Submitted, no Payment Entry yet |
| Partly Paid | A Payment Entry covers part of it |
| Paid | Fully covered |
| Cancelled | Withdrawn |

The Payment Advice list is colour-coded by status, marks scheduler-raised advices with an
*auto* pill, and has an **Awaiting Payment Entry** button for the approved-but-unpaid queue.

### 9.6 Why a party was skipped

| Message | Meaning and fix |
|---|---|
| No default payable account | Set a payable account on the Supplier or the Company |
| Already allocated on a live Payment Advice | Open that advice, or cancel it to free the invoices |
| Below the minimum advice total | Rounding residue — lower the minimum to include it |
| Party is on hold | Release the supplier, or tick "Include on-hold parties" |
| Party is disabled | Re-enable the supplier |
| Automation disabled on the party | **Disable Automatic Payment** is ticked on the Supplier |
| Over the per-run cap | It comes next run, or raise Max Parties Per Run |
| Advices left as drafts — a PM Workflow governs approval | Intended: approvers submit, not the scheduler |

### 9.7 Payment troubleshooting

| Symptom | Cause and fix |
|---|---|
| Bank / IBAN / SWIFT blank on the advice | The supplier has no Bank Account record. Create one and the details fetch automatically |
| "Payment Amount exceeds the total payable" | You authorised more than the references total. Fetch more invoices or lower the amount |
| Reference picker empty | Set Company first — the picker is scoped to it, and to submitted documents |
| "Cancel Payment Entry … before cancelling this advice" | Cancel the Payment Entry first |
| No chime after a run | Click once anywhere per session; browsers block sound until you interact |
| No email summary | No outgoing Email Account is configured. Chime and bell still work |
| Scheduled run never fires | Check Enabled, the weekday ticks, the time, and that step 1 is on |

---

## 10. Troubleshooting / FAQ

**"Cannot save: insufficient warehouse stock for …"** — the item's quantity exceeds what the selected warehouse actually has in stock. Reduce the quantity, pick a different warehouse, or remove the item.

**"No Credit Limit" when selecting a customer for a Credit invoice** — the customer has no `Customer Credit Limit` row for the invoice's company. Add one on the Customer form.

**"At least one Branch must be added in Branch Access when a Credit Limit is set"** — add a row to the customer's Branch Access grid (Section 2.2), or let it auto-populate by saving the credit limit as a user who belongs to the intended branch.

**A branch's users can't see the warehouses/cost centers/payment modes they should** — check that branch's Branch Configuration record (Section 2.1) lists them, and that the user row's `is_default_branch` is ticked if this should be their default branch.

**Inter-company Purchase Invoice creation fails with "Configure Warehouse in Inter Company Branch …"** — the selected Inter Company Branch setup (Section 2.3) has no warehouse row for the buying company, but the Sales Invoice updates stock. Add the missing warehouse to that row.

**A company's invoices print with the wrong layout** — add or fix the row for that document type on the Company's Print Formats grid (Section 2.4).
