## Bugs the system test found

| # | What the test saw | Root cause | Fix |
|---|---|---|---|
| 1 | "What should I do first?" and "Which day had the most refunds?" answered correctly but showed no proof | Ask only auto-cited a finding when the answer named its id (F1); these answers named the settlement id, UTR or the exact amount | `1ad2ec5`: cite any finding the answer names by id, reference or exact rupee amount (unit test added) |
| 2 | The "Owed to a customer" badge measured 4.14:1 contrast, below WCAG AA | `--customer` amber too light on its tinted background | `111b0b4`: darker amber, 4.94:1 |
| 3 | On the PDF review screen the narration was cut at about 18 characters, so a person fixing a flagged row couldn't tell which line it was | Narration input had the same width as the amount inputs | `2b1ab92`: narration column 34 characters wide |
| 4 | A rate-limited AI couldn't be tested without spending 20 model calls | The per-IP limit was a hard-coded constant | `SETTLE_AI_CALLS` environment setting (default 20) |

Checked and not a bug: a drawer screenshot opened mid-way down. A two-drawer repro showed every new drawer
starts at the top (the "Opening…" placeholder resets it); the test had clicked Copy before taking the shot.

Noted, not changed (model wording, not UI): for the missing settlement the AI says "contact your bank"
while the drafted fix is a ticket to Razorpay. Both are reasonable (a UTR was issued, so the bank is a fair
first call) but they should agree; tighten the explanation prompt's contact rule.
