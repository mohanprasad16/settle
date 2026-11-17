# PDF statement eval

| File | Pages | Rows (expected) | Exact rows | Balance chain | Totals | Breaks | Re-read | Calls | Seconds |
|---|---|---|---|---|---|---|---|---|---|
| bank_statement.pdf | 4 | 36 (36) | 36 | verified | True | [] | [] | 4 | 8.4 |
| bank_statement_scanned.pdf | 4 | 36 (36) | 36 | verified | True | [] | [4] | 5 | 10.2 |
| bank_statement_locked.pdf | 4 | 36 (36) | 36 | verified | True | [] | [] | 4 | 8.1 |

Engine on the digital PDF's rows: 24 findings, unexplained 0 paise.
