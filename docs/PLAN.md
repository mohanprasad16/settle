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
| Order marked paid but no payment at Razorpay | loss |
| Refund paid twice | loss |
| Chargeback debited, order still shows paid | loss |
| Fee above the agreed rate | loss |
| GST on fee not 18% | loss |
| Adjustment debit with no reason | loss |
| Paid at Razorpay, order still pending in the store (webhook never landed) | books |
| Payment with no order in the books (unrecorded sale) | books |
| Refund at Razorpay, books still say paid | books |
| Captured amount differs from order amount | books |
| Books say refunded, no refund at Razorpay | customer risk |

## What it handles without crying wolf

Partial refunds that match the books, international and Amex/Diners cards at their own rate,
one settlement arriving as two bank credits, GST rounding by a paisa, orders at month end that
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

## Build order

1. Sample data generator with planted issues + ground truth, loader, engine, bridge. Tests:
   every planted issue found with the exact amount, nothing else flagged, residual zero.
2. Explanations: one structured call per finding, numbers verified, cached for the sample.
3. Ask: an agent with read-only SQL over the three tables, answers cite rows, numbers verified.
4. Drafts: ticket to Razorpay support, journal entries for the accountant, note to a customer.
5. Bring your own files: model maps unfamiliar column headers, the user confirms before loading.
6. UI + deploy: one page, "Try with sample data", Hugging Face Spaces, per-IP limits.

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
