import csv
import io
import re
from pathlib import Path

import duckdb
import pytest
from pypdf import PdfReader

from app.engine import run
from app.load import load
from app.money import rupees, to_paise
from app.pdf_bank import PdfLocked, PdfPage, PdfRow, check, extract, pages, write_csv

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"
TRUTH = list(csv.DictReader((SAMPLE / "bank_statement.csv").read_text().splitlines()))


def _printed(text: str) -> str:
    return rupees(to_paise(text)).replace("₹", "") if text else ""


class TruthReader:
    """Stands in for the model: returns the true rows printed on whichever page it is shown."""

    def __init__(self, corrupt_first_read_of: int | None = None, header_rows: bool = False):
        self.corrupt = corrupt_first_read_of
        self.header_rows = header_rows
        self.calls = []

    def json_file(self, system, data, mime, prompt, schema, *, think="low", usage=None):
        if usage is not None:
            usage.calls += 1
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        page_no = next(i for i, p in enumerate(PAGES, 1) if p == data)
        self.calls.append(page_no)
        rows = [
            PdfRow(
                date=r["txn_date"], narration=r["narration"], ref_no=r["ref_no"],
                withdrawal=_printed(r["debit"]), deposit=_printed(r["credit"]),
                balance=_printed(r["balance"]),
            )
            for i, r in enumerate(TRUTH) if PAGE_OF[i] == page_no
        ]  # fmt: skip
        if self.header_rows:
            rows.insert(0, PdfRow(date="Date", narration="Narration", balance="Balance"))
        if page_no == self.corrupt and self.calls.count(page_no) == 1:
            rows[1].deposit = rows[1].deposit.replace("1", "7", 1) or "1.00"
        page = PdfPage(rows=rows)
        if "Opening balance" in text and page_no == 1:
            page.opening_balance = "12,00,000.00"
        if "Closing balance" in text:
            debit = sum(to_paise(r["debit"]) for r in TRUTH)
            credit = sum(to_paise(r["credit"]) for r in TRUTH)
            page.total_withdrawals = rupees(debit).replace("₹", "")
            page.total_deposits = rupees(credit).replace("₹", "")
            page.closing_balance = _printed(TRUTH[-1]["balance"])
        return page


PAGES = pages((SAMPLE / "bank_statement.pdf").read_bytes())
_TEXT = [PdfReader(io.BytesIO(p)).pages[0].extract_text() for p in PAGES]
# Which page each true row is printed on: the first page showing its balance as a whole number.
PAGE_OF = [
    next(n for n, t in enumerate(_TEXT, 1)
         if re.search(rf"(?<![\d,]){re.escape(_printed(r['balance']))}(?!\d)", t))
    for r in TRUTH
]  # fmt: skip


def test_locked_statement_needs_the_right_password():
    data = (SAMPLE / "bank_statement_locked.pdf").read_bytes()
    with pytest.raises(PdfLocked, match="password-protected"):
        pages(data)
    with pytest.raises(PdfLocked, match="didn't open"):
        pages(data, "wrong")
    assert len(pages(data, "KAAPI1234")) == len(PAGES)


def _rows():
    return [
        {"debit": to_paise(r["debit"]), "credit": to_paise(r["credit"]),
         "balance": to_paise(r["balance"])}
        for r in TRUTH
    ]  # fmt: skip


def test_balance_chain_passes_on_true_rows():
    assert check(_rows(), 120000000) == []


def test_one_misread_deposit_is_pinned_to_its_row_without_cascading():
    rows = _rows()
    i = next(i for i, r in enumerate(rows) if r["credit"] and i > 3)
    rows[i]["credit"] += 600000  # a 1 read as a 7 in the lakhs
    [b] = check(rows, 120000000)
    assert (b.row, b.likely_field) == (i + 1, "deposit")


def test_one_misread_balance_is_flagged_as_the_balance():
    rows = _rows()
    rows[5]["balance"] += 1000
    [b] = check(rows, 120000000)
    assert (b.row, b.likely_field) == (6, "balance")


def test_extract_drops_repeated_headers_and_verifies():
    report = extract((SAMPLE / "bank_statement.pdf").read_bytes(), TruthReader(header_rows=True))
    assert len(report.rows) == len(TRUTH)
    assert report.verified and report.totals_ok is True
    assert report.pages == len(PAGES) and report.model_calls == len(PAGES)


def test_a_misread_page_is_reread_once_and_fixed():
    llm = TruthReader(corrupt_first_read_of=2)
    report = extract((SAMPLE / "bank_statement.pdf").read_bytes(), llm)
    assert report.reread_pages == [2]
    assert report.verified
    assert report.model_calls == len(PAGES) + 1


def test_extracted_statement_gives_the_same_reconciliation(tmp_path):
    report = extract((SAMPLE / "bank_statement.pdf").read_bytes(), TruthReader())
    write_csv(report.rows, tmp_path / "bank.csv")
    con = duckdb.connect()
    period = load(con, SAMPLE / "razorpay_settlements.csv", tmp_path / "bank.csv",
                  SAMPLE / "orders.csv")  # fmt: skip
    result = run(con, period)
    con2 = duckdb.connect()
    period2 = load(con2, SAMPLE / "razorpay_settlements.csv", SAMPLE / "bank_statement.csv",
                   SAMPLE / "orders.csv")  # fmt: skip
    expected = run(con2, period2)
    key = lambda r: [(f.check, f.amount, f.refs[0]) for f in r.findings]
    assert key(result) == key(expected)
    assert result.totals["unexplained"] == 0
