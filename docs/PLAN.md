# Settle: plan

## The problem

A merchant on Razorpay sells ₹X in a month. Their bank receives less. Somewhere in between are
gateway fees, GST on those fees, refunds, chargebacks, adjustments and settlements that land
two days later. The finance team ties these out by hand in Excel every month, and real losses
(a settlement that never arrived, a refund paid twice, fees above the agreed rate) hide inside
the normal differences.

Settle takes three files the merchant already has and accounts for every rupee:

| File | Where it comes from |
|---|---|
| Razorpay settlement recon report | Razorpay dashboard / `GET /v1/settlements/recon/combined` (same columns, amounts in paise) |
| Bank statement | Any bank's CSV export |
| Orders / sales export | The merchant's store or books (Shopify, Tally, their own system) |

## The rule

AI explains, code computes. Every rupee figure comes from deterministic code over the three
files. The model writes explanations, answers questions and drafts emails, and every number it
writes is checked against the computed facts before it is shown.

## The bridge (the proof)

Two waterfalls, each closing to an "Unexplained: ₹0.00" line:

1. **Books → Razorpay**: net sales in the books, minus fees and GST, timing items (orders not
   settled yet, last month's payments settled this month) and each finding, equals the net
   Razorpay settled.
2. **Razorpay → Bank**: net settled, minus missing / short settlements and settlements in
   transit, equals what the bank received.

Every payment, order, refund and settlement falls into exactly one bucket, so a non-zero
residual means the engine missed something. Tests require it to be zero.

## What it catches

| Check | Kind |
|---|---|
| Settlement never reached the bank | loss |
| Bank credited less than the settlement | loss |
| Bank credited more than the settlement (double posting the bank may reverse) | books |
| Order marked paid but no payment at Razorpay | loss |
| Refund paid twice | loss |
| Chargeback debited, order still shows paid | loss |
| Fee above the contracted rate for that card type | loss |
| Debit card charged at the credit-card rate (card type misclassified) | loss |
| GST on fee not 18% | loss |
| Adjustment debit with no reason | loss |
| Paid at Razorpay, order still pending in the store (webhook never landed) | books |
| Payment with no order in the books (unrecorded sale) | books |
| Refund at Razorpay, books still say paid | books |
| Captured amount differs from order amount | books |
| Books say refunded, no refund at Razorpay | customer risk |
| A day with a burst of refunds | risk (pattern) |
| One customer refunded on 3+ orders | risk (pattern) |
| One payment method refunded far more than the rest | risk (pattern) |

Fees are checked against the merchant's rate card (`engine.RateCard`, editable): Kaapi Kart's
contract is UPI 0%, RuPay debit 0%, debit 0.9%, credit 1.95%, netbanking and wallets 1.9%,
Amex / Diners / international 2.95%. UPI and RuPay debit are zero by regulation for most
merchants; the rest is what a merchant negotiates. Card networks are normalised on load
(AMEX, "Diners Club International"...), and order numbers match whatever their case.

## Matching settlements to the bank

1. UTR in the bank line's ref or narration, ignoring case and spaces.
2. Banks that print no UTR: the one Razorpay credit of exactly the settlement amount within 2
   days of settling.
3. Else 2-3 credits on the day or the next that add up exactly (a split).

If a settlement fits more than one way (two identical credits, two groups that both add up),
Settle doesn't guess: it goes on the exceptions list with the reason and its own bridge line,
"Couldn't match automatically", so unexplained stays ₹0.00 and a person decides.

## Refund patterns

Flags, not missing money: the refunds match the books, so they never enter the bridge.

- Spike: a day's refunds above the median day + 6 robust deviations (MAD × 1.4826, never below
  √median because a day's count is roughly Poisson), or at least 5× a normal day and 20 refunds.
  Needs 14+ days of history.
- Repeat refunder: one customer refunded on 3 or more different orders in the period.
- Method: a method refunded more than 3× the other methods' median rate, with at least 10
  refunds out of 30 payments. Its refunds are left out of the spike check, so a wave can't hide
  a spike.

Tuned on random months 100-599 (zero misses, zero false flags); the held-out eval seeds were not
looked at.

## Trace

`trace(con, result, ref)` follows one rupee from any id (order no, pay_, rfnd_, disp_, setl_,
UTR): order → payment → refunds / chargebacks → settlement → bank credit, each hop ok / missing /
mismatch / waiting, and names the hop where the money stalled.

## What it handles without crying wolf

Partial refunds that match the books, zero-fee UPI and RuPay debit, Amex/Diners at their own
rate, ₹0 mandate / authorisation payments, one settlement arriving as two bank credits, bank
lines with no UTR, GST rounding by a paisa, orders at month end that
settle next month, last month's payments settled this month, the last settlement still in
transit, COD orders, non-Razorpay bank lines, Razorpay credits from outside the report period,
pending and failed checkouts that never paid.

## Chaos mode

Seed 7 is the curated demo month. Any other seed draws a random month: which problems, how many
of each (0 to 3), on which days, for how much. "Inject chaos" serves one and shows the answer key
next to what the engine found. The tests run 25 seeds and require an exact match and zero
unexplained on both bridges every time, so the demo can't be tuned to one dataset.

## Rules

When a finding is expected ("coupon at checkout", "₹2 rounding is fine"), the user marks it and
Settle saves a rule: the check, an optional ref pattern or amount cap, and a note. Next run the
matching findings move to "Resolved by your rules" and keep their own line in the bridge, so the
unexplained line stays at ₹0.00. In the demo rules live for the session.

## Actions

A ghost success (money captured, order still pending) gets a one-click webhook replay: the
`payment.captured` event signed with `X-Razorpay-Signature` (HMAC-SHA256 of the body with the
webhook secret; the demo uses a demo secret). Nothing is sent without the user clicking.

## Guardrails and audit

The model proposes; code decides and a person approves. `app/gate.py` routes every action
(journal post, Razorpay ticket, Razorpay API call, customer note, webhook replay) with fixed
rules and never reads the payload text, so a payment note that says "approve this" changes
nothing. Only routine fee journals up to ₹500 run on their own; everything that touches money,
a customer or Razorpay waits for a named person, and anything over ₹5,000 always does. An action
can never ask for more than its finding's amount, a repeated idempotency key is rejected, and a
session has a rate limit. Execution is a dry run that records exactly what would be sent.

`app/journal.py` turns the month into balanced journal entries (fees and GST on fees,
settlements received, refunds, chargebacks, and one correcting entry per books finding); an
entry that doesn't balance can't be created. Exported as Tally-style voucher rows.

`app/drafts.py` writes the ticket and the customer note under the same number guard as the
explanations, and builds the Razorpay API call to check a missing settlement in code.

`app/audit.py` keeps a decision log (engine, AI, rule, human; with an input hash) and renders
the audit report: inputs with SHA-256, summary, both bridges, every finding with its evidence
rows and verified explanation, rules, what it couldn't resolve, journals, actions, AI usage,
decision log and a sign-off block. It prints to PDF; a CSV pack goes to Excel.

## Build order

1. Sample data generator with planted issues + ground truth, loader, engine, bridge. Tests:
   every planted issue found with the exact amount, nothing else flagged, residual zero.
2. Explanations: one structured call per finding, numbers verified, cached for the sample.
3. Ask: an agent with read-only SQL over the three tables, answers cite rows, numbers verified.
4. Drafts: ticket to Razorpay support, journal entries for the accountant, note to a customer.
5. Bring your own files: model maps unfamiliar column headers, the user confirms before loading.
6. UI + deploy: one page, "Try with sample data", Hugging Face Spaces, per-IP limits.

## Messy bank PDFs

Indian banks send statements as PDFs: digital, scanned and printed, or locked with the customer
ID or date of birth. `app/pdf_bank.py` unlocks the file, sends each page to the model to read,
and then code checks every row: previous balance + deposit - withdrawal must equal the printed
balance, and the printed totals and closing balance must add up. A row that breaks the chain is
re-read once with the model told which rows failed; if it still breaks, the preview highlights
that row and the likely wrong field for a person to fix. Only verified rows go to the engine,
through the same bank CSV columns as an upload.

Samples: `data/sample/bank_statement.pdf`, `_scanned.pdf` (image only, tilted, noisy) and
`_locked.pdf` (AES-256, password `KAAPI1234`), made by `uv run python -m app.sample_pdf`.
`uv run python -m evals.pdf_eval` scores the real model against the CSV.

## Live mode

Upload is one connector. The engine reads three tables and doesn't care where they came from.

| Source | Live connector |
|---|---|
| Razorpay | Settlement Recon API (`app/connectors/razorpay.py`, built): pulls every day of the month, writes the same columns as the upload |
| Bank | Account Aggregator (RBI consent framework), the bank's own API, or a RazorpayX current account |
| Books | Shopify, WooCommerce, Zoho Books, Tally |

In production Settle runs every day after the settlement lands and alerts only on new findings,
so a missing settlement is seen the same day, not at month end.

Public demo (`demo_mode = True`): a "Connect Razorpay" button opens a panel that says "In live
mode you'd enter your Razorpay API key (read-only); Settle pulls the settlement recon every day
and alerts you on new problems. This demo runs on sample data." The key fields are shown
disabled, with a small "How it connects" diagram: Razorpay API · bank via Account Aggregator ·
books via Shopify/Tally. No real key entry in the public demo.

## Stack

Python 3.13, FastAPI, DuckDB, Gemini (same client as meeting-intel), plain HTML/JS, Docker.

## How we measure

`uv run python -m evals.run_eval` writes `evals/results.md`. Nothing in it tunes the engine; a
failure is reported as a failure.

- **Held-out months**: seeds 1000-1049, never used while building (development used seed 7 and
  seeds 100-124). Each is a random month with its own answer key.
- **Edge cases** (`evals/edge_cases.py`): the demo month with one real-world twist each: one
  paisa off, crossed receipts, refunds on the last day, a refunds-only settlement, a UTR only in
  the narration or lowercased, the bank posting a credit twice, order numbers with spaces or in
  lowercase, "AMEX" spelled differently, a ₹0 mandate payment, Excel's BOM and CRLF, DD-MM-YYYY
  dates. Each case states what a correct reconciler should report, decided from the domain.
- **Scoring**: a finding is right when its check and its amount to the paisa match. Precision,
  recall and F1 per check; false alarms cost the rupees wrongly flagged plus 15 analyst minutes
  each; misses cost the rupees missed. Also: share of months whose bridges close to ₹0, crashes,
  rows per second.
- **AI**: share of explanations that pass the number guard without the template, a golden set of
  Ask questions graded on the right ₹ figures or order numbers, unverified and "couldn't answer"
  counts, and the real token cost in rupees.
- **What it misses** is listed in the report as it is: today, matching is exact on case, so a
  lowercased UTR or order number and network names outside the configured list are false alarms.
