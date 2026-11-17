### Fresh batch 1, first and only blind run (kept as it came out)

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

