# Settle system test

Run 2026-11-17 15:33 · real Chrome via Playwright · desktop 1366×900 and phone 375×812 · 143/143 checks passed.

| Area | Passed | Failed |
|---|---|---|
| 0 Setup | 2 | 0 |
| 1 Landing | 15 | 0 |
| 10 Quality | 14 | 0 |
| 2 Sample results | 7 | 0 |
| 3 Finding drawers | 8 | 0 |
| 4 Actions and rules | 10 | 0 |
| 5 Ask | 20 | 0 |
| 6 Chaos | 6 | 0 |
| 7 Upload | 40 | 0 |
| 8 Downloads | 4 | 0 |
| 9 Failure handling | 17 | 0 |

## Timings

| Step | Seconds |
|---|---|
| sample (click to results) | 0.83 |
| ask: Why did a settlement never reach my bank? | 9.26 |
| ask: How much did I lose to chargebacks this month? | 12.62 |
| ask: Which day had the most refunds? | 10.61 |
| ask: What should I do first? | 8.00 |
| chaos month 1 | 0.83 |
| chaos month 2 | 0.83 |
| chaos month 3 | 0.80 |
| CSV upload (submit to results) | 0.85 |
| locked PDF (submit to review) | 10.41 |
| scanned PDF (submit to review) | 8.95 |
| digital PDF (submit to review) | 7.92 |

## Every check

### 0 Setup

- ✅ server A has the AI on
- ✅ server B has the AI off

### 1 Landing

- ✅ headline visible
- ✅ button 'Try with sample data' visible
- ✅ button 'Upload your files' visible
- ✅ button 'Connect Razorpay' visible
- ✅ tour video plays (or its fallback shows)
- ✅ How it works has 3 steps
- ✅ trust strip has 4 points
- ✅ hero net sales matches the sample (₹22,87,307.00)
- ✅ hero received in bank matches the sample (₹21,26,760.32)
- ✅ hero missing settlement matches the sample (₹21,196.39)
- ✅ Connect Razorpay panel opens
- ✅ key fields are disabled
- ✅ 'How it connects' diagram visible
- ✅ panel closes with 'Got it'
- ✅ panel closes with Esc

### 10 Quality

- ✅ landing: every control has an accessible name
- ✅ keyboard: Tab reaches 'Try with sample data'
- ✅ focus is visible
- ✅ results: every control has an accessible name
- ✅ keyboard: Tab reaches the first problem
- ✅ keyboard: Enter opens it, focus moves into the drawer
- ✅ drawer: every control has an accessible name
- ✅ keyboard: Esc closes, focus back on the problem
- ✅ text contrast on badges, figures and buttons ≥ 4.5:1
- ✅ phone landing: no sideways scroll
- ✅ phone upload: no sideways scroll
- ✅ phone results: no sideways scroll
- ✅ phone drawer: no sideways scroll
- ✅ no console errors or page errors anywhere

### 2 Sample results

- ✅ results in under 2 s
- ✅ sample: Money lost = ₹33,361.49
- ✅ sample: Owed to customers = ₹5,096.00
- ✅ sample: Book corrections = ₹10,291.00
- ✅ sample: Auto-reconciled = 95.1%
- ✅ sample: Unexplained = ₹0.00
- ✅ sample: both bridges end at ₹0.00 unexplained

### 3 Finding drawers

- ✅ all 24 findings listed
- ✅ every drawer has an explanation
- ✅ every explanation shows the verified badge
- ✅ every drawer shows proof rows
- ✅ every money finding shows the trace chain
- ✅ draft tabs all have text, Copy works
- ✅ Esc closes and focus returns to the line every time
- ✅ every drawer opens at the top, not where the last one was scrolled

### 4 Actions and rules

- ✅ settlement drawer proposes fixes
- ✅ approve without a name is blocked
- ✅ approve with a name gives a dry-run record
- ✅ reject works
- ✅ no stray null / undefined text after decisions
- ✅ running the same action twice is blocked with a reason
- ✅ over-cap action blocked (tests/test_act.py::test_gate_routes_and_blocks; no UI path can create one)
- ✅ mark as expected removes the matching findings
- ✅ bridge still ties out after the rule
- ✅ handled list shows the rule and note

### 5 Ask

- ✅ 'Why did a settlement never reach my bank?' answered
- ✅ 'Why did a settlement never reach my bank?' answer has ₹ figures
- ✅ 'Why did a settlement never reach my bank?' answer cites proof
- ✅ 'Why did a settlement never reach my bank?' trace timeline opens
- ✅ 'How much did I lose to chargebacks this month?' answered
- ✅ 'How much did I lose to chargebacks this month?' answer has ₹ figures
- ✅ 'How much did I lose to chargebacks this month?' answer cites proof
- ✅ 'How much did I lose to chargebacks this month?' trace timeline opens
- ✅ 'Which day had the most refunds?' answered
- ✅ 'Which day had the most refunds?' answer has ₹ figures
- ✅ 'Which day had the most refunds?' answer cites proof
- ✅ 'Which day had the most refunds?' trace timeline opens
- ✅ 'What should I do first?' answered
- ✅ 'What should I do first?' answer has ₹ figures
- ✅ 'What should I do first?' answer cites proof
- ✅ injection in Ask doesn't claim to approve anything
- ✅ injection in Ask changes no action
- ✅ 600-character question refused politely
- ✅ ask box caps typing at 500 characters
- ✅ empty question gets 'Type a question first'

### 6 Chaos

- ✅ Random month #329760: every hidden problem found, nothing extra
- ✅ Random month #329760: unexplained ₹0.00 on both bridges
- ✅ Random month #616164: every hidden problem found, nothing extra
- ✅ Random month #616164: unexplained ₹0.00 on both bridges
- ✅ Random month #283885: every hidden problem found, nothing extra
- ✅ Random month #283885: unexplained ₹0.00 on both bridges

### 7 Upload

- ✅ submitting with no files says which are missing
- ✅ three sample CSVs: Money lost = ₹33,361.49
- ✅ three sample CSVs: Owed to customers = ₹5,096.00
- ✅ three sample CSVs: Book corrections = ₹10,291.00
- ✅ three sample CSVs: Auto-reconciled = 95.1%
- ✅ three sample CSVs: Unexplained = ₹0.00
- ✅ three sample CSVs: both bridges end at ₹0.00 unexplained
- ✅ orders file in the bank box: plain error naming the box
- ✅ the wrong box is highlighted
- ✅ missing column named in the error
- ✅ empty file: plain error
- ✅ file over 15 MB: plain error
- ✅ locked PDF without password asks for it
- ✅ wrong password: plain message
- ✅ locked PDF reaches the review step
- ✅ locked PDF: every running balance verified
- ✅ locked PDF: Money lost = ₹33,361.49
- ✅ locked PDF: Owed to customers = ₹5,096.00
- ✅ locked PDF: Book corrections = ₹10,291.00
- ✅ locked PDF: Auto-reconciled = 95.1%
- ✅ locked PDF: Unexplained = ₹0.00
- ✅ locked PDF: both bridges end at ₹0.00 unexplained
- ✅ scanned PDF reaches the review step
- ✅ scanned PDF: every running balance verified
- ✅ scanned PDF: Money lost = ₹33,361.49
- ✅ scanned PDF: Owed to customers = ₹5,096.00
- ✅ scanned PDF: Book corrections = ₹10,291.00
- ✅ scanned PDF: Auto-reconciled = 95.1%
- ✅ scanned PDF: Unexplained = ₹0.00
- ✅ scanned PDF: both bridges end at ₹0.00 unexplained
- ✅ digital PDF reaches the review step
- ✅ digital PDF: every running balance verified
- ✅ edited balance: continue is blocked with the row count
- ✅ the broken row is highlighted
- ✅ digital PDF: Money lost = ₹33,361.49
- ✅ digital PDF: Owed to customers = ₹5,096.00
- ✅ digital PDF: Book corrections = ₹10,291.00
- ✅ digital PDF: Auto-reconciled = 95.1%
- ✅ digital PDF: Unexplained = ₹0.00
- ✅ digital PDF: both bridges end at ₹0.00 unexplained

### 8 Downloads

- ✅ audit report opens and shows Unexplained ₹0.00
- ✅ audit report has the sign-off block
- ✅ CSV pack zip downloads and unzips
- ✅ Tally journal: total Dr equals total Cr

### 9 Failure handling

- ✅ network error: plain message with Try again
- ✅ Try again recovers
- ✅ refresh keeps the session's results
- ✅ unknown finding id: friendly 404
- ✅ no session: friendly 'Nothing is loaded yet'
- ✅ /api/finding/F999: no stack trace leaks
- ✅ /api/ask: no stack trace leaks
- ✅ AI off: banner says so
- ✅ AI off: Money lost = ₹33,361.49
- ✅ AI off: Owed to customers = ₹5,096.00
- ✅ AI off: Book corrections = ₹10,291.00
- ✅ AI off: Auto-reconciled = 95.1%
- ✅ AI off: Unexplained = ₹0.00
- ✅ AI off: both bridges end at ₹0.00 unexplained
- ✅ AI off: Ask is disabled, not broken
- ✅ AI off: explanations come from templates, labelled
- ✅ AI rate limit: friendly message, results stay

## Screenshots

- `e2e/screenshots/01_landing_desktop.png`
- `e2e/screenshots/02_connect_razorpay_panel.png`
- `e2e/screenshots/03_results_overview_desktop.png`
- `e2e/screenshots/04_bridges_desktop.png`
- `e2e/screenshots/05_results_full_desktop.png`
- `e2e/screenshots/06_finding_drawer_desktop.png`
- `e2e/screenshots/07_action_approved_dry_run.png`
- `e2e/screenshots/08_rule_marked_expected.png`
- `e2e/screenshots/09_ask_with_trace.png`
- `e2e/screenshots/10_chaos_answer_key.png`
- `e2e/screenshots/11_upload_desktop.png`
- `e2e/screenshots/12_error_wrong_box.png`
- `e2e/screenshots/13_error_locked_pdf_password.png`
- `e2e/screenshots/14_pdf_review_verified.png`
- `e2e/screenshots/15_pdf_review_broken_row.png`
- `e2e/screenshots/16_audit_report.png`
- `e2e/screenshots/17_error_network_retry.png`
- `e2e/screenshots/18_ai_off_template_explanation.png`
- `e2e/screenshots/19_error_rate_limit.png`
- `e2e/screenshots/20_landing_phone.png`
- `e2e/screenshots/21_results_phone.png`
- `e2e/screenshots/22_results_phone_full.png`
- `e2e/screenshots/23_finding_drawer_phone.png`

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

