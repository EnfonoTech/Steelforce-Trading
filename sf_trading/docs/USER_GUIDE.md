# SF Trading — User Guide

Steel Force Trading WLL · ERPNext customisations shipped in the `sf_trading` app.

This file is the single source of the guide. It renders in the desk at
**SF Trading User Guide** (`/app/sf-trading-user-guide`) and publicly at **`/user-guide`**.

## Table of Contents

- [Supplier Payments — Payment Advice](#supplier-payments--payment-advice)
- [Raise one advice](#raise-one-advice)
- [Get Outstanding Documents](#get-outstanding-documents)
- [Batch by supplier — the Builder](#batch-by-supplier--the-builder)
- [From the Purchase Invoice list](#from-the-purchase-invoice-list)
- [Approval](#approval)
- [Creating the Payment Entry](#creating-the-payment-entry)
- [Scheduled runs](#scheduled-runs)
- [Guards and thresholds](#guards-and-thresholds)
- [Statuses](#statuses)
- [Why a party was skipped](#why-a-party-was-skipped)
- [Troubleshooting](#troubleshooting)
- [Other reports in this app](#other-reports-in-this-app)

## Supplier Payments — Payment Advice

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

### Who can do what

| Role | Prepare | Approve | Configure automation |
|---|---|---|---|
| Accounts User | Yes | No | No |
| Accountant | Yes | No | No |
| Finance Manager | Yes | Yes | Yes |
| Accounts Manager | Yes | No | Yes |

## Raise one advice

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

## Get Outstanding Documents

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

## Batch by supplier — the Builder

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

## From the Purchase Invoice list

Tick invoices → **Actions → Create Payment Advice (by supplier)**. They are grouped by supplier
and one draft advice is raised each. Anything already on a live advice is skipped and named.
All ticked invoices must belong to one company.

## Approval

### With the PM Workflow (recommended)

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

### Without a workflow

Only the user linked to the **Approver** Employee may submit. A System Manager can release a
stuck advice.

## Creating the Payment Entry

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

## Scheduled runs

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

## Guards and thresholds

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

## Statuses

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

## Why a party was skipped

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

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Bank / IBAN / SWIFT blank on the advice | The supplier has no Bank Account record. Create one and the details fetch automatically |
| "Payment Amount exceeds the total payable" | You authorised more than the references total. Fetch more invoices or lower the amount |
| Reference picker empty | Set Company first — the picker is scoped to it, and to submitted documents |
| "Cancel Payment Entry … before cancelling this advice" | Cancel the Payment Entry first |
| No chime after a run | Click once anywhere per session; browsers block sound until you interact |
| No email summary | No outgoing Email Account is configured. Chime and bell still work |
| Scheduled run never fires | Check Enabled, the weekday ticks, the time, and that step 1 is on |

## Other reports in this app

| Report | What it shows |
|---|---|
| Supplier Due Payment Report | Supplier payables by due date, with overdue days, ageing buckets and the Payment Advice each invoice sits on |
| Invoice Due & Overdue Report | Sales and Purchase together, overdue-only by default, with the same advice columns |
| PDC Report | Cheque Payment Entries with cheque date, posting date and a T-3 reminder date |
| Loyalty Rewards Report | Loyalty reward journals with their Sales Invoice and customer, plus a customer-wise summary |
| DCR Report / DCR Detailed / DCR Detail | Daily collection reporting |
| Work Flow Approval | Approval state across transactions |

---

Enfono Technologies · support@enfono.com
