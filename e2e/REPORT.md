# Settle system test

Run 2026-11-18 00:34 · real Chrome via Playwright · desktop 1366×900 and phone 375×812 · 252/252 checks passed.

| Area | Passed | Failed |
|---|---|---|
| 0 Setup | 2 | 0 |
| 1 Landing | 21 | 0 |
| 10 Quality | 22 | 0 |
| 11 AI-native | 27 | 0 |
| 11 Guidance | 10 | 0 |
| 11 Settle AI floater | 20 | 0 |
| 2 Sample results | 11 | 0 |
| 3 Finding drawers | 9 | 0 |
| 3b Settlements | 5 | 0 |
| 4 Actions and rules | 15 | 0 |
| 5 Settle AI answers | 40 | 0 |
| 6 Chaos | 6 | 0 |
| 7 Upload | 40 | 0 |
| 8 Downloads | 6 | 0 |
| 9 Failure handling | 18 | 0 |

## Timings

| Step | Seconds |
|---|---|
| sample (click to results) | 0.82 |
| Settle AI: Why did a settlement never reach my bank? | 0.86 |
| Settle AI: How much did I lose to chargebacks this month? | 0.93 |
| Settle AI: Which day had the most refunds? | 0.57 |
| Settle AI: What should I do first? | 0.53 |
| chaos month 1 | 0.84 |
| chaos month 2 | 0.84 |
| chaos month 3 | 0.83 |
| CSV upload (submit to results) | 0.84 |
| locked PDF (submit to review) | 0.13 |
| scanned PDF (submit to review) | 0.13 |
| digital PDF (submit to review) | 0.13 |

## Every check

### 0 Setup

- ✅ server A has the AI on
- ✅ server B has the AI off

### 1 Landing

- ✅ headline visible
- ✅ button 'Try with sample data' visible
- ✅ button 'Upload your files' visible
- ✅ button 'Connect your sources' visible
- ✅ tour video plays (or its fallback shows)
- ✅ How it works has 3 steps
- ✅ trust strip has 4 points
- ✅ hero shows the product tour with a poster
- ✅ headline is three-way, not Razorpay-only
- ✅ Connections page shows 3 source cards
- ✅ key / consent fields are disabled in the demo
- ✅ 'How it connects' covers all three sources
- ✅ Back returns to the landing page
- ✅ Run a demo sync reconciles, labelled as sample data
- ✅ demo sync: Money lost = ₹33,361.49
- ✅ demo sync: Owed to customers = ₹5,096.00
- ✅ demo sync: Book corrections = ₹10,291.00
- ✅ demo sync: Reconciled by value = 92.1%
- ✅ demo sync: Unexplained = ₹0.00
- ✅ demo sync: both bridges end at ₹0.00 unexplained
- ✅ after a sync the cards show when it last synced

### 10 Quality

- ✅ landing: every control has an accessible name
- ✅ keyboard: Tab reaches 'Try with sample data'
- ✅ focus is visible
- ✅ results: every control has an accessible name
- ✅ keyboard: Tab reaches 'Issues to fix' in the sidebar
- ✅ keyboard: Tab reaches the first problem
- ✅ keyboard: Enter opens it, focus moves into the drawer
- ✅ drawer: every control has an accessible name
- ✅ keyboard: Esc closes, focus back on the problem
- ✅ text contrast on badges, figures and buttons ≥ 4.5:1
- ✅ phone landing: no sideways scroll
- ✅ phone upload: no sideways scroll
- ✅ phone results: no sideways scroll
- ✅ phone header fits on 2 lines
- ✅ phone header: every action is still reachable
- ✅ phone issues: no sideways scroll
- ✅ phone drawer: no sideways scroll
- ✅ desktop 1280px: top bar stays one row on every page
- ✅ selection bar stays clear of the open Settle AI
- ✅ selection bar stays clear of the sidebar
- ✅ selection bar clear of the sidebar with Settle AI closed
- ✅ no console errors or page errors anywhere

### 11 AI-native

- ✅ tour video above the fold at 1366x768
- ✅ tour video above the fold at 1440x900
- ✅ results get a route
- ✅ browser Back returns to the previous page
- ✅ Forward works too
- ✅ a deep link opens the issue after a refresh
- ✅ logo goes to Overview
- ✅ ⌘K opens an issue by id
- ✅ slash-command menu
- ✅ @-mention suggests issues
- ✅ Settle AI echo path keeps the typed text
- ✅ Stop cancels a running Settle AI request and says nothing changed
- ✅ selecting a line shows the action bar
- ✅ change card shows before → after and its checks
- ✅ Always allow isn't offered above the cap
- ✅ Allow once needs a name
- ✅ manual match: unmatched line gone, Unexplained ₹0.00
- ✅ undo brings the line back and the bridge still ties
- ✅ What Settle remembers suggests a rule
- ✅ the suggested rule resolves its issues and the bridge ties
- ✅ locked kinds can't be changed
- ✅ after setting manual matches to Never, they're refused
- ✅ policy edits are logged with who and what changed
- ✅ an attached sheet gets a mapping card with a row preview
- ✅ the sheet, used as orders, reconciles to the same issues
- ✅ Approvals offers bulk Settle AI prompts
- ✅ decision log filters by who

### 11 Guidance

- ✅ tour starts at step 1 with a title
- ✅ Next moves to step 2
- ✅ Back returns to step 1
- ✅ last step offers Finish
- ✅ Finish closes the tour
- ✅ Skip closes the tour
- ✅ Esc closes the tour
- ✅ focus stays inside the tour
- ✅ every page has a heading with a one-line purpose
- ✅ Start here starts at 0 of 3 for a new month

### 11 Settle AI floater

- ✅ the Settle AI bubble is on every page
- ✅ Ask a question is no longer in the sidebar
- ✅ ✦ Ask on a KPI tile pre-fills the question with that figure
- ✅ ✦ Ask on a KPI tile shows what it's about and sends nothing yet
- ✅ a waterfall bar opens Settle AI about that bar
- ✅ ✦ Ask on a settlement row pre-fills that payout
- ✅ Settle AI is on every page with its own quick actions
- ✅ quick actions read the page: Overview names the money lost
- ✅ quick actions read the page: Approvals offers bulk approval
- ✅ #/ask opens Settle AI over the Overview
- ✅ ⌘K finds the 21 Oct payout by date
- ✅ ⌘K: a typed question puts Ask Settle AI first
- ✅ ⌘K → Ask Settle AI opens the floater and asks the question
- ✅ select-all shows part-ticked as indeterminate
- ✅ row checkboxes are at most 20px and centred in their row
- ✅ the select-all box sits inside the header row
- ✅ the selection bar doesn't overlap the Settle AI bubble
- ✅ a toast doesn't cover the open Settle AI window
- ✅ reduced motion turns every animation off (landing, floater, pages, figures)
- ✅ on a phone Settle AI is a full-screen sheet

### 2 Sample results

- ✅ results in under 2 s
- ✅ sample: Money lost = ₹33,361.49
- ✅ sample: Owed to customers = ₹5,096.00
- ✅ sample: Book corrections = ₹10,291.00
- ✅ sample: Reconciled by value = 92.1%
- ✅ sample: Unexplained = ₹0.00
- ✅ sample: both bridges end at ₹0.00 unexplained
- ✅ every KPI has a help tooltip button
- ✅ hovering a KPI's ? explains it in plain words
- ✅ the ₹ reconciled KPI explains itself
- ✅ the bank bridge is headed as a BRS

### 3 Finding drawers

- ✅ all 22 issues to fix listed
- ✅ sidebar badge matches the Issues list
- ✅ every drawer has an explanation
- ✅ every explanation shows the verified badge
- ✅ every drawer shows proof rows
- ✅ every money finding shows the trace chain
- ✅ draft tabs all have text, Copy works
- ✅ Esc closes and focus returns to the line every time
- ✅ every drawer opens at the top, not where the last one was scrolled

### 3b Settlements

- ✅ lists all 31 settlements
- ✅ total net settled = start of the bank bridge
- ✅ total received = Received in your bank
- ✅ status filter shows only that status
- ✅ a row opens the payout: trail, contents, link to its issue

### 4 Actions and rules

- ✅ Start here ticks 'Review the biggest issue' after opening one
- ✅ settlement drawer proposes fixes
- ✅ approve without a name is blocked
- ✅ double-clicking Approve records it once, with no error
- ✅ approve with a name gives a dry-run record
- ✅ after approving, the confirmation says what's next
- ✅ reject works
- ✅ no stray null / undefined text after decisions
- ✅ running the same action twice is blocked with a reason
- ✅ over-cap action blocked (tests/test_act.py::test_gate_routes_and_blocks; no UI path can create one)
- ✅ Start here ticks 'Approve a fix' after approving
- ✅ mark as expected removes the matching findings
- ✅ bridge still ties out after the rule
- ✅ handled list shows the rule and note
- ✅ Saved rules lists the new rule

### 5 Settle AI answers

- ✅ Settle AI opens on a home view: ask about this page, reconcile something, upload a sheet
- ✅ the message box caps typing at 800 characters
- ✅ Shift+Enter makes a new line, Enter sends
- ✅ an empty message isn't sent
- ✅ 'Why did a settlement never reach my bank?' answered
- ✅ 'Why did a settlement never reach my bank?' answer has ₹ figures
- ✅ 'Why did a settlement never reach my bank?' answer cites proof
- ✅ 'Why did a settlement never reach my bank?' answer offers 2-3 follow-ups
- ✅ 'Why did a settlement never reach my bank?' answer has 👍 / 👎, copy and a time
- ✅ 'Why did a settlement never reach my bank?' How I worked it out starts collapsed
- ✅ 'Why did a settlement never reach my bank?' trace timeline opens
- ✅ 'How much did I lose to chargebacks this month?' answered
- ✅ 'How much did I lose to chargebacks this month?' answer has ₹ figures
- ✅ 'How much did I lose to chargebacks this month?' answer cites proof
- ✅ 'How much did I lose to chargebacks this month?' answer offers 2-3 follow-ups
- ✅ 'How much did I lose to chargebacks this month?' answer has 👍 / 👎, copy and a time
- ✅ 'How much did I lose to chargebacks this month?' How I worked it out starts collapsed
- ✅ 'Which day had the most refunds?' answered
- ✅ 'Which day had the most refunds?' answer has ₹ figures
- ✅ 'Which day had the most refunds?' answer cites proof
- ✅ 'Which day had the most refunds?' answer offers 2-3 follow-ups
- ✅ 'Which day had the most refunds?' answer has 👍 / 👎, copy and a time
- ✅ 'Which day had the most refunds?' How I worked it out starts collapsed
- ✅ 'What should I do first?' answered
- ✅ 'What should I do first?' answer has ₹ figures
- ✅ 'What should I do first?' answer cites proof
- ✅ 'What should I do first?' answer offers 2-3 follow-ups
- ✅ 'What should I do first?' answer has 👍 / 👎, copy and a time
- ✅ 👎 asks what was wrong, with chips
- ✅ 👍 and 👎 are recorded and exported in feedback.csv
- ✅ ratings land in the decision log
- ✅ a follow-up chip asks that question
- ✅ injection in Settle AI doesn't claim to approve anything
- ✅ injection in Settle AI changes no action
- ✅ 900-character message refused politely
- ✅ 600-character question refused politely
- ✅ scroll-to-bottom appears when scrolled up, and goes back down
- ✅ Esc closes Settle AI and focus returns to the bubble
- ✅ the conversation is still there when reopened
- ✅ New conversation goes back to the home view

### 6 Chaos

- ✅ Random month #499277: every hidden problem found, nothing extra
- ✅ Random month #499277: unexplained ₹0.00 on both bridges
- ✅ Random month #877594: every hidden problem found, nothing extra
- ✅ Random month #877594: unexplained ₹0.00 on both bridges
- ✅ Random month #725475: every hidden problem found, nothing extra
- ✅ Random month #725475: unexplained ₹0.00 on both bridges

### 7 Upload

- ✅ submitting with no files says which are missing
- ✅ three sample CSVs: Money lost = ₹33,361.49
- ✅ three sample CSVs: Owed to customers = ₹5,096.00
- ✅ three sample CSVs: Book corrections = ₹10,291.00
- ✅ three sample CSVs: Reconciled by value = 92.1%
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
- ✅ locked PDF: Reconciled by value = 92.1%
- ✅ locked PDF: Unexplained = ₹0.00
- ✅ locked PDF: both bridges end at ₹0.00 unexplained
- ✅ scanned PDF reaches the review step
- ✅ scanned PDF: every running balance verified
- ✅ scanned PDF: Money lost = ₹33,361.49
- ✅ scanned PDF: Owed to customers = ₹5,096.00
- ✅ scanned PDF: Book corrections = ₹10,291.00
- ✅ scanned PDF: Reconciled by value = 92.1%
- ✅ scanned PDF: Unexplained = ₹0.00
- ✅ scanned PDF: both bridges end at ₹0.00 unexplained
- ✅ digital PDF reaches the review step
- ✅ digital PDF: every running balance verified
- ✅ edited balance: continue is blocked with the row count
- ✅ the broken row is highlighted
- ✅ digital PDF: Money lost = ₹33,361.49
- ✅ digital PDF: Owed to customers = ₹5,096.00
- ✅ digital PDF: Book corrections = ₹10,291.00
- ✅ digital PDF: Reconciled by value = 92.1%
- ✅ digital PDF: Unexplained = ₹0.00
- ✅ digital PDF: both bridges end at ₹0.00 unexplained

### 8 Downloads

- ✅ audit report opens and shows Unexplained ₹0.00
- ✅ audit report has the sign-off block
- ✅ Start here ticks 'Download the audit report' after exporting it
- ✅ BRS export ends at 'Balance as per bank' = Received in your bank
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
- ✅ AI off: Reconciled by value = 92.1%
- ✅ AI off: Unexplained = ₹0.00
- ✅ AI off: both bridges end at ₹0.00 unexplained
- ✅ AI off: Settle AI is disabled, not broken
- ✅ Saved rules empty state explains how to make the first rule
- ✅ AI off: explanations come from templates, labelled
- ✅ AI rate limit: friendly message, results stay

## Screenshots

- `e2e/screenshots/01_landing_desktop.png`
- `e2e/screenshots/02_connections_page.png`
- `e2e/screenshots/03_results_overview_desktop.png`
- `e2e/screenshots/04_results_full_desktop.png`
- `e2e/screenshots/05_start_here_card.png`
- `e2e/screenshots/06_waterfall_desktop.png`
- `e2e/screenshots/07_help_tooltip.png`
- `e2e/screenshots/08_bridges_desktop.png`
- `e2e/screenshots/09_patterns_desktop.png`
- `e2e/screenshots/10_issues_table_desktop.png`
- `e2e/screenshots/11_tour_step_1.png`
- `e2e/screenshots/12_tour_step_2.png`
- `e2e/screenshots/13_settlements_desktop.png`
- `e2e/screenshots/14_settlement_drawer_desktop.png`
- `e2e/screenshots/15_finding_drawer_desktop.png`
- `e2e/screenshots/16_finding_trace_tab.png`
- `e2e/screenshots/17_action_approved_dry_run.png`
- `e2e/screenshots/18_rule_marked_expected.png`
- `e2e/screenshots/19_settle_ai_answer.png`
- `e2e/screenshots/20_chaos_answer_key.png`
- `e2e/screenshots/21_upload_desktop.png`
- `e2e/screenshots/22_error_wrong_box.png`
- `e2e/screenshots/23_error_locked_pdf_password.png`
- `e2e/screenshots/24_pdf_review_verified.png`
- `e2e/screenshots/25_pdf_review_broken_row.png`
- `e2e/screenshots/26_audit_report.png`
- `e2e/screenshots/27_error_network_retry.png`
- `e2e/screenshots/28_empty_state_rules.png`
- `e2e/screenshots/29_ai_off_template_explanation.png`
- `e2e/screenshots/30_error_rate_limit.png`
- `e2e/screenshots/31_landing_hero_video.png`
- `e2e/screenshots/32_command_palette.png`
- `e2e/screenshots/33_settle_ai_home.png`
- `e2e/screenshots/34_change_card_before_confirm.png`
- `e2e/screenshots/35_change_card_after_confirm.png`
- `e2e/screenshots/36_suggested_rules.png`
- `e2e/screenshots/37_settings_policy.png`
- `e2e/screenshots/38_excel_transform_preview.png`
- `e2e/screenshots/39_approvals_bulk.png`
- `e2e/screenshots/40_settle_ai_launcher.png`
- `e2e/screenshots/41_settle_ai_kpi_ask.png`
- `e2e/screenshots/42_palette_ask_settle_ai.png`
- `e2e/screenshots/43_settlements_two_ticked.png`
- `e2e/screenshots/44_settle_ai_phone.png`
- `e2e/screenshots/45_landing_phone.png`
- `e2e/screenshots/46_phone_header.png`
- `e2e/screenshots/47_results_phone.png`
- `e2e/screenshots/48_results_phone_full.png`
- `e2e/screenshots/49_finding_drawer_phone.png`

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

