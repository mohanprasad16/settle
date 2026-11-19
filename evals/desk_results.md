# Desk evaluation

Run 2026-11-19 12:14 with the real model, once, no cache. Golden set: the 16 messages of the sample inbox, each with an answer key written by the generator (category, and the order or payout it is about, or ambiguous / none). It includes one prompt injection, two off-topic messages, one that fits several payments and one with an order number that doesn't exist.

| | |
|---|---|
| Match accuracy (code) | 16 / 16 |
| Category accuracy (model) | 16 / 16 |
| Every figure verified: reply written by the model and passed the number guard first time or on the retry | 16 / 16 |
| Final replies (model or template) with any figure not in the facts | 0 |
| Model calls | 32 (2.0 per message; 56 s in all) |
| Matching on the 50 held-out random months (seeds 1000-1049, code only) | 678 / 678 messages |

## Misses

None on this run.

## Every message

| Id | Match | Category | Reply | Reply text |
|---|---|---|---|---|
| M1 | ✓ matched | ✓ refund_status | model | Hi Arjun, a refund of ₹349.00 for your order KK-11268 was sent on 15 Oct 2026 with reference rfnd_TSYlEp4Dmncu0h. Refunds usually reach your bank within 5-7 working days of being sent. Thank you for your patience. |
| M2 | ✓ matched | ✓ duplicate_charge | model | Hi Saanvi, we received one payment of ₹1,199.00 for order KK-11307 on 15 Oct 2026. If your bank shows a second debit, it is a failed attempt that the bank returns on its own within 5-7 working days. |
| M3 | ✓ matched | ✓ settlement | model | Hi Accounts Team, payout setl_ITZIu2TDI695iu of ₹91,148.95 was settled on 11 Oct 2026 under UTR UTIBR54170856495. We can confirm that the amount of ₹91,148.95 was received in the bank on 11 Oct 2026. |
| M4 | ✓ none | ✓ refund_status | model | Hi Ravi, we could not find the reference number KK-99817 in our records. Could you please share your correct order number with us so we can check this for you? If your inquiry is not about an order, a member of our team will get back to you shortly to assist you further. |
| M5 | ✓ matched | ✓ paid_not_confirmed | model | Hi Ishaan, we received your payment of ₹4,998.00 on 15 Oct 2026 for order KK-11306. The order has not been confirmed in our store yet. Our team is working to confirm your order right now. Thank you for your patience. |
| M6 | ✓ matched | ✓ refund_status | model | Hi Diya, we checked your query regarding order KK-11513 for ₹1,499.00 placed on 18 Oct 2026. The refund of ₹1,499.00 for order KK-11513 is recorded but has not been sent yet. Our team is sending the refund now and will write again once it is on its way. |
| M7 | ✓ matched | ✓ paid_not_confirmed | model | Hi Vihaan, we received your payment of ₹698.00 for order KK-11606 on 20 Oct 2026. We see that the order hasn't been confirmed in our store yet. Please don't worry, our team is confirming the order in our store now. |
| M8 | ✓ matched | ✓ refund_status | model | Hi Rohan, we checked order KK-11459 for ₹3,597.00 placed on 17 Oct 2026. The refund of ₹3,597.00 recorded on 18 Oct 2026 has not been sent yet. Our team is sending the refund now and will write to you again once it is on its way. |
| M9 | ✓ matched | ✓ refund_status | model | Hi Ishaan, your refund of ₹649.00 for order KK-11092 was sent on 12 Oct 2026 (reference rfnd_zaQ9amHp8SduQz). Refunds usually reach the bank within 5-7 working days of being sent. Please allow this time for the amount to show in your account. |
| M10 | ✓ matched | ✓ refund_status | model | Hi Dev, we checked order KK-11264 and found that we sent two refunds of ₹2,697.00, on 15 Oct 2026 and 16 Oct 2026. One of these refunds was sent by mistake. Refunds usually reach the bank within 5-7 working days. Our team will get in touch with you soon regarding returning the extra refund. |
| M11 | ✓ matched | ✓ settlement | model | Hi Accounts Team, payout setl_6ODkowvxYxMciS of ₹21,196.39 was settled on 14 Oct 2026 under UTR UTIBR58302335367, but it is not on the bank statement. We are raising it with Razorpay support, quoting the payout id and UTR. |
| M12 | ✓ none | ✓ other | model | Hi Farah, thank you for reaching out to Kaapi Kart Foods. If your query is about a specific order, please share your order number with us so we can assist you. Otherwise, a member of our team will reply to you shortly. |
| M13 | ✓ none | ✓ other | model | Hi Neha, could you please share your order number if this is about an existing order? If this isn't regarding an order, a member of our team will reply to you shortly. |
| M14 | ✓ ambiguous | ✓ duplicate_charge | model | Hi Sunita, we received more than one payment of ₹499.00 around 1 Oct 2026. Could you please share your order number with us? Once you provide it, we will be able to find your specific order and help you further. |
| M15 | ✓ matched | ✓ other | model | Hi Diya, we received your payment of ₹2,998.00 on 29 Oct 2026 for order KK-12151. Your order status is currently paid. Please let us know if you need any further help with this order! |
| M16 | ✓ matched | ✓ paid_not_confirmed | model | Hi Vihaan, no payment reached us for order KK-11878, which is why your order shows as failed. If the amount of ₹398.00 left your account, your bank will usually return the failed payment on its own within 5-7 working days. Please check with your bank if it does not reflect after that time. |
