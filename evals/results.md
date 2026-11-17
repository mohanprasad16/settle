# Settle evaluation

69 months: 50 held-out random months (seeds 1000-1049, never used while building) and 19 adversarial edge cases. A finding is correct when its check and its amount to the paisa match.

## Engine

| | |
|---|---|
| Precision | 99.5% (1250 right, 6 false alarms) |
| Recall | 99.8% (2 missed) |
| F1 | 99.7% |
| False-alarm cost | ₹53,097.60 wrongly flagged, ~90 analyst minutes (15 min each) |
| Missed money | ₹642.32 |
| Months with every finding right | 64 / 69 |
| Bridges closed to ₹0 unexplained | 100.0% |
| Crashes | 0 |
| Throughput | 257,731 rows at 16,381 rows/sec |

### By check

fee_overcharge is one grouped finding per month, so when an extra payment wrongly joins the group its amount changes and it scores as one false alarm plus one miss; the money actually flagged in error is the difference between the two.

| Check | Right | False alarms | Missed | Precision | Recall | ₹ wrongly flagged | ₹ missed |
|---|---|---|---|---|---|---|---|
| amount_mismatch | 118 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| books_refund_not_processed | 110 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| chargeback | 115 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| duplicate_refund | 88 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| fee_overcharge | 57 | 2 | 2 | 96.6% | 96.6% | ₹663.52 | ₹642.32 |
| ghost_success | 127 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| orphan_payment | 112 | 2 | 0 | 98.2% | 100.0% | ₹398.00 | ₹0.00 |
| paid_order_no_payment | 127 | 1 | 0 | 99.2% | 100.0% | ₹398.00 | ₹0.00 |
| refund_not_in_books | 132 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| settlement_missing | 88 | 1 | 0 | 98.9% | 100.0% | ₹51,638.08 | ₹0.00 |
| settlement_short | 98 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| unexplained_adjustment | 78 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |

### Edge cases

| Case | Twist | Result | What went wrong |
|---|---|---|---|
| captured ₹0.01 short | payment captured one paisa below the order | pass |  |
| captured ₹1 short | payment captured one rupee below the order | pass |  |
| bank ₹0.01 short | bank credit one paisa below the settlement | pass |  |
| twin orders swapped | same amount, same day, receipts crossed | pass |  |
| refund on 31 Oct | books refund on the last day, Razorpay deducts in Nov | pass |  |
| partial refunds = full | two half refunds against one full refund in books | pass |  |
| negative settlement | a refunds-only day, bank shows a debit | pass |  |
| UTR only in ref_no | bank narration has no UTR | pass |  |
| UTR lowercase | UTR only in narration, lowercased | **fail** | false alarm settlement_missing ₹51,638.08 |
| UTR padded | UTR only in narration, extra spaces | pass |  |
| bank posted twice | the same credit appears twice | pass |  |
| order no whitespace | order number with spaces in the books | pass |  |
| order no lowercase | order number lowercased in the books | **fail** | false alarm paid_order_no_payment ₹398.00; false alarm orphan_payment ₹398.00 |
| Amex at 3% | card network American Express, fee at 3% | pass |  |
| AMEX spelled differently | network written 'AMEX', fee at 3% | **fail** | false alarm fee_overcharge ₹331.76; missed fee_overcharge ₹321.16 |
| Diners Club International | odd network name, fee at 3% | **fail** | false alarm fee_overcharge ₹331.76; missed fee_overcharge ₹321.16 |
| ₹0 mandate payment | zero-amount registration payment, no receipt | **fail** | false alarm orphan_payment ₹0.00 |
| BOM + CRLF | all three files saved by Excel on Windows | pass |  |
| dates DD-MM-YYYY | order and refund dates as 31-10-2026 | pass |  |

### Held-out months that weren't perfect: 0


## AI (real model, demo month)

- Explanations: 21/21 written by the model and passed the number guard; 0 fell back to the fixed template (9.1s).
- Ask: 11/11 correct (the right ₹ figures or order numbers in the answer); 0 came back unverified; 0 said they couldn't answer; 2.4 tool calls per question on average.
- Cost: 68 model calls, 163,444 input + 11,193 output tokens ≈ ₹30.44.

| Question | Correct | Verified | Answer |
|---|---|---|---|
| How much money from settlements never reached my bank this month? | yes | yes | A total of ₹53,657.47 from settlement setl_6ODkowvxYxMciS (UTR: UTIBR58302335367), settled on 14 Oct 2026, never reached your bank account. Additionally, settlement setl_9rHmNeuxtEjEXM was short-credited by ₹2,000.00 (bank received ₹61,792.28 instead of ₹63,792.28). Please contact your bank regarding both discrepancies. |
| How much did I lose to chargebacks this month? | yes | yes | You lost a total of ₹3,297.00 to chargebacks across 2 transactions this month. This consists of ₹1,499.00 debited for order KK-10427 (dispute disp_jbgbTW0nFS4v7H) on 7 Oct 2026, and ₹1,798.00 debited for order KK-11540 (dispute disp_xw6eA83XVg0fv1) on 25 Oct 2026. Both orders still incorrectly show as paid in your store system, so you should contact Razorpay support to resolve them. |
| Which order was refunded twice, and how much extra went out? | yes | yes | Order KK-11264 was refunded twice through Razorpay refund IDs rfnd_AVuPqcAqAh9zkB and rfnd_TZedo1Qqr14Wa2. Since each refund was for ₹2,697.00 (matching the original books refund amount), a total of ₹5,394.00 was paid out, resulting in an extra ₹2,697.00 being sent. Please contact the customer to recover the overpayment. |
| How much extra did Razorpay charge above my agreed fee rate? | yes | yes | Razorpay charged ₹321.16 above your agreed fee rate across 45 card payments between 16 Oct 2026 and 18 Oct 2026. During this period, a fee rate of 2.4% was applied instead of your agreed rate of 2.0%. Please contact Razorpay support to resolve this fee overcharge. |
| Was any settlement credited short in my bank? By how much? | yes | yes | Yes, settlement setl_9rHmNeuxtEjEXM (UTR: UTIBR59944785665) on 21 Oct 2026 was credited short in your bank by ₹2,000.00. Razorpay settled a net amount of ₹63,792.28, but your bank only received ₹61,792.28. Please contact your bank to resolve this discrepancy. |
| Are there payments at Razorpay with no order in my books? What's the total? | yes | yes | Yes, there are 2 payments at Razorpay that do not exist in your books, totaling ₹2,598.00. These include payment pay_P8nIOBekpAYZzb for ₹1,299.00 and payment pay_tf7jZYIH9QtiWm for ₹1,299.00. You should contact your accountant and store/ops team to get these recorded. |
| Which orders say refunded in my books but no refund was actually paid? | yes | yes | There are two orders marked as refunded in your books where no actual refund was processed via Razorpay. Order KK-11459 for ₹3,597.00 and order KK-11513 for ₹1,499.00 both show no refund payments at Razorpay. Please contact your store/ops team to resolve these discrepancies. |
| What is the total net amount Razorpay settled to me this month? | yes | yes | The total net amount Razorpay settled to you across 31 settlements this month (October 2026) is ₹22,39,929.92. This represents the total net amount calculated across all settled items in your Razorpay reports for the period. |
| Did Razorpay debit any adjustment without a reason? How much? | yes | yes | Yes, Razorpay debited an adjustment of ₹1,180.00 without providing a reason. This debit occurred under adjustment ID adj_xhPDpi57077LVy on 27 Oct 2026 for settlement setl_RcMMH6dbSgiYw2. You should contact Razorpay support to resolve this issue. |
| Which orders are still pending in my store even though the payment was captured? | yes | yes | Orders KK-10594 and KK-10702 are still marked as pending in your store even though payments were successfully captured at Razorpay. Order KK-10594 for ₹4,998.00 was captured via payment ID pay_rcY3WHJfPrYfmq, and order KK-10702 for ₹499.00 was captured via payment ID pay_Qr5xKwC0L30e36. Please contact your store/ops team to update their fulfillment status. |
| Write me a short poem about filter coffee. | yes | yes | I am an assistant specifically designed to help reconcile your Razorpay settlements, bank statements, and store books. I cannot write poems or answer general non-reconciliation questions. |
