"""Bank statement PDFs (digital, scanned or password-locked) -> the same bank CSV the engine reads.

The model reads each page. Code checks what it read: every row must satisfy
previous balance + deposit - withdrawal = printed balance, and the statement's own totals must
add up. A row that breaks the chain is re-read once; if it still breaks, it is handed to a
person with the exact row and the likely wrong field, instead of being guessed.
"""

import csv
import io
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from app.llm import LLM, LLMError, Usage
from app.money import to_paise
from app.sample import BANK_COLUMNS

SYSTEM = """You transcribe one page of an Indian bank statement into structured rows.
Return every transaction row on the page, top to bottom, none skipped.
Copy values exactly as printed; never compute, round or reformat a number.
Join a narration that wraps onto several lines into one string, adding nothing between the pieces.
Leave withdrawal or deposit empty when the cell is empty.
Column headers, page headers, footers and the summary block are not rows.
Fill opening_balance, closing_balance, total_withdrawals and total_deposits only when printed on this page."""

PROMPT = "Transcribe this statement page."
RETRY = """Transcribe this statement page again. The balance check failed on these rows; read
each of their amounts and balances digit by digit: {rows}"""

_HAS_DATE = re.compile(r"\d{1,2}[/\-. ][0-9A-Za-z]{2,3}[/\-. ]\d{2,4}")
_AMOUNT = re.compile(r"-?\d[\d,]*(?:\.\d{1,2})?")


def _paise(text: str) -> int:
    """The first amount in a cell. Models sometimes run on past the number (seen on a scan);
    anything wrong that slips through still has to survive the balance chain."""
    m = _AMOUNT.search(text or "")
    return to_paise(m.group()) if m else 0


class PdfRow(BaseModel):
    date: str
    narration: str
    ref_no: str = ""
    withdrawal: str = ""
    deposit: str = ""
    balance: str


class PdfPage(BaseModel):
    rows: list[PdfRow]
    opening_balance: str = ""
    closing_balance: str = ""
    total_withdrawals: str = ""
    total_deposits: str = ""


class PdfLocked(ValueError):
    """User-facing: the PDF needs a password, or the one given is wrong."""


@dataclass
class Break:
    row: int  # 1-based position in the extracted rows
    expected_balance: int
    printed_balance: int
    likely_field: str  # withdrawal | deposit | balance


@dataclass
class ExtractionReport:
    rows: list[dict]  # txn_date, narration, ref_no, debit, credit (paise), balance (paise), page
    pages: int
    opening_balance: int
    breaks: list[Break] = field(default_factory=list)
    totals_ok: bool | None = None  # None when the statement prints no totals
    reread_pages: list[int] = field(default_factory=list)
    model_calls: int = 0

    @property
    def verified(self) -> bool:
        return not self.breaks and self.totals_ok is not False


def pages(data: bytes, password: str | None = None) -> list[bytes]:
    """Unlock and split into one single-page PDF per page."""
    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError as e:
        raise PdfLocked("That file isn't a readable PDF.") from e
    if reader.is_encrypted:
        if not password:
            raise PdfLocked(
                "This statement is password-protected. Enter the password your bank gave you "
                "(often your customer ID or date of birth)."
            )
        if not reader.decrypt(password):
            raise PdfLocked("That password didn't open the statement.")
    out = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        out.append(buf.getvalue())
    return out


def _read(llm: LLM, page: bytes, prompt: str, usage: Usage) -> PdfPage:
    try:
        return llm.json_file(SYSTEM, page, "application/pdf", prompt, PdfPage, usage=usage)
    except LLMError:  # broken or cut-off JSON from one page: one more try before giving up
        return llm.json_file(SYSTEM, page, "application/pdf", prompt, PdfPage, usage=usage)


def _rows(parsed: list[PdfPage]) -> list[dict]:
    rows = []
    for n, page in enumerate(parsed, 1):
        for r in page.rows:
            if not _HAS_DATE.search(r.date):  # a repeated header or a summary line
                continue
            rows.append({
                "txn_date": r.date.strip(), "narration": " ".join(r.narration.split()),
                "ref_no": r.ref_no.strip(), "debit": _paise(r.withdrawal),
                "credit": _paise(r.deposit), "balance": _paise(r.balance), "page": n,
            })  # fmt: skip
    return rows


def check(rows: list[dict], opening: int) -> list[Break]:
    """The balance chain. A break names the row and which field most likely misread."""
    breaks, prev = [], opening
    for i, r in enumerate(rows):
        expected = prev + r["credit"] - r["debit"]
        if expected == r["balance"]:
            prev = r["balance"]
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        # If the next row agrees with this printed balance, the balance is right and the amount
        # on this row was misread; otherwise the balance itself was misread.
        balance_ok = (
            nxt is not None and r["balance"] + nxt["credit"] - nxt["debit"] == nxt["balance"]
        )
        if balance_ok:
            likely = "deposit" if r["credit"] else "withdrawal"
            prev = r["balance"]
        else:
            likely, prev = "balance", expected
        breaks.append(Break(i + 1, expected, r["balance"], likely))
    return breaks


def _opening(parsed: list[PdfPage], rows: list[dict]) -> int:
    printed = next((p.opening_balance for p in parsed if p.opening_balance.strip()), "")
    if printed:
        return _paise(printed)
    first = rows[0]
    return first["balance"] - first["credit"] + first["debit"]


def _totals_ok(parsed: list[PdfPage], rows: list[dict]) -> bool | None:
    debit = next((p.total_withdrawals for p in parsed if p.total_withdrawals.strip()), "")
    credit = next((p.total_deposits for p in parsed if p.total_deposits.strip()), "")
    closing = next((p.closing_balance for p in parsed if p.closing_balance.strip()), "")
    if not (debit or credit or closing):
        return None
    ok = True
    if debit:
        ok &= _paise(debit) == sum(r["debit"] for r in rows)
    if credit:
        ok &= _paise(credit) == sum(r["credit"] for r in rows)
    if closing and rows:
        ok &= _paise(closing) == rows[-1]["balance"]
    return ok


def extract(data: bytes, llm: LLM, password: str | None = None) -> ExtractionReport:
    split = pages(data, password)
    usage = Usage()
    try:
        with ThreadPoolExecutor(4) as pool:
            parsed = list(pool.map(lambda p: _read(llm, p, PROMPT, usage), split))
    except LLMError as e:
        raise PdfLocked(f"Couldn't read the statement right now ({e}). Try again.") from e

    rows = _rows(parsed)
    if not rows:
        raise PdfLocked("No transactions found in this PDF. Is it a bank statement?")
    opening = _opening(parsed, rows)
    breaks = check(rows, opening)

    # One targeted re-read of each page that broke the chain.
    reread = sorted({rows[b.row - 1]["page"] for b in breaks})
    for n in reread:
        flagged = [
            f"{r['txn_date']} {r['narration'][:40]}"
            for b in breaks
            if (r := rows[b.row - 1])["page"] == n
        ]
        try:
            parsed[n - 1] = _read(llm, split[n - 1], RETRY.format(rows="; ".join(flagged)), usage)
        except LLMError:
            continue
    if reread:
        rows = _rows(parsed)
        opening = _opening(parsed, rows)
        breaks = check(rows, opening)

    return ExtractionReport(
        rows, len(split), opening, breaks, _totals_ok(parsed, rows), reread, usage.calls
    )


def write_csv(rows: list[dict], path: Path) -> None:
    """Same columns as an uploaded bank CSV, so app/load.py reads it unchanged."""
    rupee = lambda p: f"{p / 100:.2f}" if p else ""
    with open(path, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(BANK_COLUMNS)
        for r in rows:
            wr.writerow([r["txn_date"], r["narration"], r["ref_no"], rupee(r["debit"]),
                         rupee(r["credit"]), rupee(r["balance"])])  # fmt: skip
