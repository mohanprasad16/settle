"""Reads the three sample statement PDFs with the real model and scores them against the CSV.

uv run python -m evals.pdf_eval      # needs GEMINI_API_KEY; writes evals/pdf_results.md
"""

import csv
import tempfile
import time
from pathlib import Path

import duckdb

from app.engine import run
from app.llm import GeminiLLM
from app.load import load
from app.money import to_paise
from app.pdf_bank import extract, write_csv
from app.sample_pdf import PASSWORD

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"
FILES = [("bank_statement.pdf", None), ("bank_statement_scanned.pdf", None),
         ("bank_statement_locked.pdf", PASSWORD)]  # fmt: skip


def _key(r: dict) -> tuple:
    return (r["txn_date"], "".join(r["narration"].split()), r["ref_no"],
            to_paise(r["debit"]), to_paise(r["credit"]), to_paise(r["balance"]))  # fmt: skip


def main() -> None:
    truth = list(csv.DictReader((SAMPLE / "bank_statement.csv").read_text().splitlines()))
    llm = GeminiLLM()
    out = ["# PDF statement eval", "",
           "| File | Pages | Rows (expected) | Exact rows | Balance chain | Totals | Breaks | Re-read | Calls | Seconds |",
           "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    digital_rows = None
    for name, password in FILES:
        started = time.perf_counter()
        report = extract((SAMPLE / name).read_bytes(), llm, password)
        secs = time.perf_counter() - started
        got = [
            _key({**r, "debit": f"{r['debit'] / 100:.2f}", "credit": f"{r['credit'] / 100:.2f}",
                  "balance": f"{r['balance'] / 100:.2f}"})
            for r in report.rows
        ]  # fmt: skip
        exact = sum(a == b for a, b in zip(got, [_key(r) for r in truth]))
        chain = "verified" if not report.breaks else f"{len(report.breaks)} breaks"
        out.append(
            f"| {name} | {report.pages} | {len(report.rows)} ({len(truth)}) | {exact} | {chain} "
            f"| {report.totals_ok} | {[(b.row, b.likely_field) for b in report.breaks]} "
            f"| {report.reread_pages} | {report.model_calls} | {secs:.1f} |"
        )
        print(out[-1])
        if name == "bank_statement.pdf":
            digital_rows = report.rows

    with tempfile.TemporaryDirectory() as tmp:
        write_csv(digital_rows, Path(tmp) / "bank.csv")
        con = duckdb.connect()
        period = load(con, SAMPLE / "razorpay_settlements.csv", Path(tmp) / "bank.csv",
                      SAMPLE / "orders.csv")  # fmt: skip
        result = run(con, period)
    out += ["", (f"Engine on the digital PDF's rows: {len(result.findings)} findings, "
            f"unexplained {result.totals['unexplained']} paise.")]  # fmt: skip
    print(out[-1])
    (Path(__file__).parent / "pdf_results.md").write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
