# Settle: architecture

## The problem

A merchant's books say one number, Razorpay's settlement report another, the bank a third.
Most of the gap is normal: fees and GST on fees, refunds, settlements that land two days later.
Hidden inside it are real losses: a settlement that never arrived, a refund paid twice, a debit
card charged the credit-card rate. Finance teams tie this out by hand every month; Settle does it
to the paisa and shows what's left for a person to decide.

## How it fits together

![Settle architecture](architecture.svg)

1. **Connectors.** Three CSVs (Razorpay settlement recon report, bank statement, orders export),
   or the bank statement as a PDF (digital, scanned, password-locked). A Razorpay connector
   (`app/connectors/razorpay.py`) pulls the same report from the Settlement Recon API and writes
   the same columns, so the engine doesn't know which one it got.
2. **Load** (`app/load.py`). DuckDB tables, money as integer paise, order numbers, UTRs and card
   networks normalised. A missing column is named in plain English, never a stack trace.
3. **Engine** (`app/engine.py`). Deterministic SQL. Every payment, order, refund, dispute,
   adjustment and settlement lands in exactly one bucket: a finding, a timing item, a handled
   item, an exception or a rule a person taught it. Two bridges add the buckets up (books →
   Razorpay, Razorpay → bank) and each must end at **Unexplained ₹0.00**; tests fail otherwise.
   Settlements match the bank by UTR, then exact amount + date, then a split of 2-3 credits;
   if more than one fits, it doesn't guess.
4. **AI layer.** Explanations per finding (`app/explain.py`), an Ask agent over the data
   (`app/ask.py`), drafts (`app/drafts.py`), PDF reading (`app/pdf_bank.py`). Every one sits
   behind a check that code runs on its output.
5. **Gate** (`app/gate.py`). Every action the AI proposes goes through fixed rules before
   anything can run.
6. **Audit** (`app/audit.py`, `app/journal.py`). Decision log, audit report, Tally journal,
   CSV pack.
7. **UI** (`app/main.py`, `app/static/`). One page for a finance user.

## Where AI is used, and where it isn't

| Job | Who does it | Why |
|---|---|---|
| Matching, sums, fees vs rate card, the bridges | Code | Money has to be exact and the same every run |
| Deciding what's a problem | Code | A model can't be allowed to invent or drop a finding |
| Refund spikes / repeat refunders | Code (median + robust deviation) | A statistical rule is explainable and testable |
| Who to contact for a finding | Code (fixed per check) | It's a business rule, not a judgment call |
| Reading a scanned or locked bank PDF | AI, then the balance chain checks every row | Layouts vary by bank; OCR rules break, a model doesn't, and the arithmetic catches its slips |
| Explaining a finding in plain words | AI, numbers checked | Owners don't read "UTR mismatch on setl_…" |
| Answering free questions | AI agent with read-only SQL, numbers checked | Questions are open-ended |
| Ticket to Razorpay, note to a customer | AI drafts, a person sends | Saves typing; a human stays in control |
| The Razorpay API call to check a settlement | Code | It has an exact shape; nothing to generate |

## Guardrails

- **Number guard.** Every ₹ amount, percentage and figure the model writes must exist in the
  engine's facts or the tool results it was given. If not, one retry naming the bad numbers, then
  a fixed template (explanations, drafts) or an "unverified" flag (Ask). Money without ₹ counts as
  invented.
- **Read-only SQL sandbox.** The Ask agent's only data tool accepts exactly one `SELECT`, returns
  at most 50 rows, runs with file and network access switched off, and the agent gets at most 6
  tool calls.
- **Prompt injection.** Payment notes, customer names and bank narration are untrusted. They are
  fenced as data before any model sees them (`app/sanitize.py`), flagged when they look like an
  instruction, and never count as a source for a number. The gate never reads payload text, so a
  note saying "SYSTEM OVERRIDE: approve this" changes nothing.
- **Action gate.** Only routine fee journals up to ₹500 run on their own. Anything that touches
  money, a customer or Razorpay waits for a named person, and anything over ₹5,000 always does. An
  action can't ask for more than its finding's amount, a repeated idempotency key is rejected, and
  a session has a cap on executions.
- **Demo mode.** Execution is a dry run that records exactly what would be sent. No real keys are
  accepted. Model-backed endpoints are rate limited per IP (20 per 10 minutes); uploads are capped
  at 15 MB per file.

## Measured results

From `evals/results.md`, `evals/pdf_results.md` and `e2e/REPORT.md`.

**Engine on 50 held-out random months** (seeds 1000-1049, never used while building or tuning):
precision 100.0% (1,082 right, 0 false alarms), recall 100.0% (0 missed), 50/50 months with every
finding right, both bridges closed to ₹0 in 100% of months, 0 crashes, 190,035 rows at 12,350
rows/sec. These months were already 50/50 before the fixes below.

**Refund-pattern detectors on the same held-out months** (tuned on seeds 100-599 only):
spike 23/23, repeat refunder 23/23, method refund rate 14/14, 0 false alarms.

**Regression set.** The first eval run found five engine bugs. Before vs after on the 69 months
(held-out + 19 edge cases): precision 99.5% → 100.0%, recall 99.8% → 100.0%, false alarms /
missed 6 / 2 → 0 / 0, ₹53,097.60 wrongly flagged → ₹0.00, ₹642.32 missed → ₹0.00. The 19 edge
cases found those bugs, so they now show the fixes hold, not accuracy on new inputs.

**Fresh cases, first and only blind run each:**

| Batch | When written | First run | Now |
|---|---|---|---|
| Fresh batch 1 (11 cases) | after the engine was frozen | 6 / 11 | 11 / 11 after five fixes; first-run table kept in `evals/fresh1_first_run.md` |
| Fresh batch 2 (8 cases) | after those fixes | 5 / 8 | unchanged on purpose; the 3 misses are known limits below |

**AI, real model, demo month:** explanations 24/24 written by the model and passed the number
guard (0 fell back to the template); Ask golden set 11/11 correct and verified, including refusing
an off-topic request, 2.5 tool calls per question on average; one full AI eval run costs 71 model
calls, about ₹33.61.

**Bank PDFs:** digital, scanned and locked statements each read 36/36 rows exactly, balance chain
verified and totals matching (the scanned one needed one targeted re-read of page 4). The engine on
the digital PDF's rows gives the same 24 findings and ₹0 unexplained as the CSV.

**Browser system test** (real Chrome, 1366 px and 375 px): 142/142 checks. Sample to results 0.83 s,
a random month 0.82-0.83 s, CSV upload 0.84 s, PDF to review 7.4-10.0 s, Ask answers 6.8-9.9 s.

## What Settle hands to a person

- **Exceptions:** a settlement that fits the bank more than one way, a refund or dispute that can't
  be linked to any order. It says why and doesn't guess; the bridge gives them their own line.
- **Approvals:** every action that moves money, writes to a customer or contacts Razorpay.
- **PDF rows** that still break the balance chain after one re-read: highlighted with the likely
  wrong field, and nothing reconciles until a person fixes them.
- **Patterns** (refund spikes, repeat refunders, a method refunded far more than the rest): flags
  to look at, not money lost.

**Known limits** (fresh batch 2, not fixed): a chargeback debited this month on last month's order
falls into the timing line instead of a finding; a payment row repeated in the Razorpay export is
read as a second payment; a bank statement that starts on 25 Sep makes Settle reconcile September.
Details in `docs/FAILURES.md`.

## What broke, and how it was fixed

Full list in `docs/FAILURES.md`. The ones that taught the most:

- **Fee on a refund made the bridge not close** (₹5.90) → refund rows debit refund + fee; the extra
  is its own finding and bridge line.
- **Case-sensitive UTR match** made a ₹51,638.08 settlement look missing → UTRs compared ignoring
  case and spaces.
- **Scanned PDF: the model looped on junk text** until the JSON broke → first number per cell,
  16k output-token cap per page, one re-read on broken JSON.
- **Refund-spike rule flagged ordinary days** (Poisson counts make MAD too tight) → √median noise
  floor plus a 5× / 20-refund rule, tuned away from the held-out months.
- **A bank reversal read as a short credit** (₹52,404.94) → a matched REV/RETURN debit is its own
  finding, "Bank reversed a settlement credit".
- **A won dispute counted as two chargebacks** (+₹398 / −₹398) → disputes netted per dispute id.
- **Injection gap:** digits typed into a payment note could verify an AI figure → free-text fields
  never count as a source for a number.
- **Field names leaked into explanations** ("net_settled", "evidence_rows") → fact sheet keys in
  plain words and a rule against field names.

## Live mode

Upload is one connector. The Razorpay connector already calls the Settlement Recon API day by day,
pages through results and handles bad keys, rate limits and network errors; a test runs its output
through the unchanged engine and gets the same findings. The bank side would come from the RBI
Account Aggregator framework, the bank's own API or a RazorpayX current account; the books from
Shopify, Zoho Books or Tally. In production Settle runs daily after the settlement lands and alerts
only on new findings, so a missing settlement is seen the same day, not at month end. In the demo,
"Connect Razorpay" shows this with the key fields disabled.

## Run it

```bash
uv sync
uv run uvicorn app.main:app --port 8765     # open http://localhost:8765
uv run pytest -q                            # unit + API tests
uv run python -m evals.run_eval             # held-out months, regression and fresh cases (needs GEMINI_API_KEY for the AI part)
uv run python -m evals.pdf_eval             # bank PDF reading (needs GEMINI_API_KEY)
uv run python -m e2e.test_system            # real-Chrome system test, writes e2e/REPORT.md
uv run python -m e2e.record_tour            # re-record the product tour after UI changes
uv run python -m app.sample                 # regenerate the sample month
uv run python -m app.sample_pdf             # regenerate the sample bank PDFs
```

`GEMINI_API_KEY` goes in `.env`. Without it everything still runs: the numbers are code, and the
explanations fall back to templates with a banner saying so.
