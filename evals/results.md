# Settle evaluation

Headline: 50 held-out random months (seeds 1000-1049, never used while building or tuning). They were already 50/50 perfect before the eval-bug fixes below. A finding is correct when its check and its amount to the paisa match.

The 19 adversarial edge cases are a **regression set**, not unseen data: they found five engine bugs, which were then fixed, so passing them now shows the fixes hold, not accuracy on new inputs. The 11 **fresh cases** at the end were written afterwards and run once; nothing was fixed for them.

## Engine on held-out months

| | |
|---|---|
| Precision | 100.0% (928 right, 0 false alarms) |
| Recall | 100.0% (0 missed) |
| F1 | 100.0% |
| False-alarm cost | ₹0.00 wrongly flagged, ~0 analyst minutes (15 min each) |
| Missed money | ₹0.00 |
| Months with every finding right | 50 / 50 |
| Bridges closed to ₹0 unexplained | 100.0% |
| Crashes | 0 |
| Throughput | 189,833 rows at 13,208 rows/sec |

### Before and after the five eval-bug fixes (held-out + regression set, 69 months)

The first run found five engine bugs (case-sensitive UTR and order-number matching, network spellings, a ₹0 finding, a double-posted credit reported as short). They were fixed at the root with a test each; the held-out seeds were not used to tune anything. The engine also gained new checks between the two runs (rate card, refund patterns, bank lines without a UTR), so the 'after' column scores more findings.

| | Before | After |
|---|---|---|
| Precision | 99.5% | 100.0% |
| Recall | 99.8% | 100.0% |
| False alarms / missed | 6 / 2 | 0 / 0 |
| ₹ wrongly flagged | ₹53,097.60 | ₹0.00 |
| ₹ missed | ₹642.32 | ₹0.00 |
| Months with every finding right | 64 / 69 | 69 / 69 |

### Refund-pattern detectors on the held-out months

These flag patterns for a person (kind 'risk'), not money. They were tuned on seeds 100-599; these are seeds 1000-1049 only.

| Detector | Right | False alarms | Missed | Precision | Recall |
|---|---|---|---|---|---|
| method_refund_rate | 14 | 0 | 0 | 100.0% | 100.0% |
| refund_spike | 23 | 0 | 0 | 100.0% | 100.0% |
| repeat_refunder | 23 | 0 | 0 | 100.0% | 100.0% |

### By check

fee_overcharge is one grouped finding per month, so when an extra payment wrongly joins the group its amount changes and it scores as one false alarm plus one miss; the money actually flagged in error is the difference between the two.

| Check | Right | False alarms | Missed | Precision | Recall | ₹ wrongly flagged | ₹ missed |
|---|---|---|---|---|---|---|---|
| amount_mismatch | 68 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| books_refund_not_processed | 92 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| chargeback | 93 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| duplicate_refund | 64 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| fee_overcharge | 31 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| ghost_success | 64 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| method_refund_rate | 14 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| orphan_payment | 69 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| paid_order_no_payment | 78 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| rate_misclassified | 22 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| refund_not_in_books | 57 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| refund_spike | 23 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| repeat_refunder | 23 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| settlement_missing | 69 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| settlement_short | 77 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |
| unexplained_adjustment | 84 | 0 | 0 | 100.0% | 100.0% | ₹0.00 | ₹0.00 |

### Regression set (edge cases that found the five bugs)

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
| UTR lowercase | UTR only in narration, lowercased | pass |  |
| UTR padded | UTR only in narration, extra spaces | pass |  |
| bank posted twice | the same credit appears twice | pass |  |
| order no whitespace | order number with spaces in the books | pass |  |
| order no lowercase | order number lowercased in the books | pass |  |
| Amex at the premium rate | card network American Express, fee at the contract premium rate | pass |  |
| AMEX spelled differently | network written 'AMEX', fee at the premium rate | pass |  |
| Diners Club International | odd network name, fee at the premium rate | pass |  |
| ₹0 mandate payment | zero-amount registration payment, no receipt | pass |  |
| BOM + CRLF | all three files saved by Excel on Windows | pass |  |
| dates DD-MM-YYYY | order and refund dates as 31-10-2026 | pass |  |

### Fresh unseen cases (run once, nothing fixed for them)

6 of 11 right. 'Expected' is what a correct reconciler should say, decided before the run; 'exception' means put in front of a person. Failures are listed in docs/FAILURES.md as known limits.

| Case | Twist | Expected (beyond the planted problems) | Got | Result |
|---|---|---|---|---|
| split over two days, no UTR | one settlement as two credits on consecutive days | nothing new | as expected | pass |
| refund larger than payment | Razorpay refunds ₹100 more than the order | refund_mismatch ₹100.00 | as expected | pass |
| created after settled | payment timestamp a day after its settlement | nothing new | as expected | pass |
| bank reverses a credit | credit, then a REV debit of it two days later | settlement_missing ₹52,404.94 | settlement_short ₹52,404.94; missing settlement_missing ₹52,404.94 | **fail** |
| IMPS reference, no UTR | bank prints its own IMPS reference | nothing new | as expected | pass |
| fee on a refund | ₹5 + GST fee deducted on a refund | refund_fee ₹5.90 | bridge didn't close; missing refund_fee ₹5.90 | **fail** |
| dispute won | chargeback debited and credited back | nothing new | chargeback ₹398.00, chargeback -₹398.00 | **fail** |
| adjustment credit, no reason | ₹500 credited with no description | unexplained_adjustment ₹500.00 | missing unexplained_adjustment ₹500.00 | **fail** |
| refund booked on the twin order | same customer, same amount, refund on the wrong order | refund_not_in_books ₹398.00, books_refund_not_processed ₹398.00 | as expected | pass |
| currency column and ₹ amounts | orders export with currency column, '₹1,499.00' | nothing new | as expected | pass |
| refund with no payment link | refund row with no payment_id or receipt | exception ₹899.00 | missing exception ₹899.00 | **fail** |

### Held-out months that weren't perfect: 0


## AI (real model, demo month)

- Explanations: 24/24 written by the model and passed the number guard; 0 fell back to the fixed template (8.2s).
- Ask: 11/11 correct (the right ₹ figures or order numbers in the answer); 0 came back unverified; 1 said they couldn't answer; 2.0 tool calls per question on average.
- Cost: 66 model calls, 152,128 input + 10,755 output tokens ≈ ₹28.6.

| Question | Correct | Verified | Answer |
|---|---|---|---|
| How much money from settlements never reached my bank this month? | yes | yes | A total of ₹21,196.39 from settlement setl_6ODkowvxYxMciS (UTR: UTIBR58302335367) settled on 14 Oct 2026 never reached your bank account. You should contact Razorpay support regarding this missing payout. |
| How much did I lose to chargebacks this month? | yes | yes | You lost a total of ₹3,297.00 to chargebacks this month across 2 separate disputes. The first chargeback was for ₹1,499.00 on order KK-10427, and the second was for ₹1,798.00 on order KK-11540. You should contact Razorpay support regarding both findings. |
| Which order was refunded twice, and how much extra went out? | yes | yes | Order KK-11264 was refunded twice. An extra amount of ₹2,697.00 went out as Razorpay processed two refunds totaling ₹5,394.00 against the books refund of ₹2,697.00. |
| How much extra did Razorpay charge above my agreed fee rate? | yes | yes | Razorpay charged ₹169.54 extra above your agreed fee rate across 20 card payments between 16 Oct 2026 and 18 Oct 2026, where a rate of 2.4% was applied instead of the agreed 1.95%. You should contact Razorpay support to resolve this issue. |
| Was any settlement credited short in my bank? By how much? | yes | yes | Yes, settlement setl_9rHmNeuxtEjEXM (UTR: UTIBR59944785665) was credited short by ₹2,000.00 in your bank on 21 Oct 2026. The net settlement amount was ₹64,942.62, but your bank only received ₹62,942.62. You should contact your bank to resolve this discrepancy. |
| Are there payments at Razorpay with no order in my books? What's the total? | yes | yes | Yes, there are 2 payments at Razorpay that have no corresponding order in your books, totaling ₹2,598.00. Payment pay_P8nIOBekpAYZzb (receipt KK-WEB-900) was for ₹1,299.00, and payment pay_tf7jZYIH9QtiWm (receipt KK-WEB-901) was for ₹1,299.00. Please contact your store/ops team to review these transactions. |
| Which orders say refunded in my books but no refund was actually paid? | yes | yes | Two orders are marked as refunded in your books, but no refund was actually paid out via Razorpay. These are order KK-11459 for ₹3,597.00 and order KK-11513 for ₹1,499.00. Please contact your store/ops team to review these discrepancies. |
| What is the total net amount Razorpay settled to me this month? | yes | yes | The total net amount Razorpay settled to you this month across all settlements is ₹22,41,009.53. |
| Did Razorpay debit any adjustment without a reason? How much? | yes | yes | Yes, Razorpay debited an adjustment of ₹1,180.00 without providing a reason. This adjustment (ID: adj_xhPDpi57077LVy) was included in settlement setl_RcMMH6dbSgiYw2 on 27 Oct 2026. Please contact Razorpay support regarding this debit. |
| Which orders are still pending in my store even though the payment was captured? | yes | yes | There are 2 orders still marked as pending in your store even though payment was captured at Razorpay. Order KK-11306 for ₹4,998.00 (payment pay_XM0PKWHktRu4ST) and order KK-11606 for ₹698.00 (payment pay_chQHGhYPOcncsK) both have captured payments. Please contact your store/ops team to update their order status. |
| Write me a short poem about filter coffee. | yes | yes | I can only help reconcile your Razorpay settlements, bank statements, and store orders. I am unable to write poems. |
