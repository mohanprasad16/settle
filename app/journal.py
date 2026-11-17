"""Journal entries a bookkeeper would post for the month, built from the loaded data and the
engine's findings. Every entry is checked to balance (debits = credits) when it's created, so an
unbalanced entry can never be shown or exported.

GST on gateway fees is one "Input GST" line; split it into CGST + SGST (same state) or IGST
when posting, depending on where Razorpay bills from.
"""

from dataclasses import dataclass
from pathlib import Path

from app.engine import Result
from app.money import rupees
from app.sanitize import CsvWriter

CLEARING = "Razorpay Clearing"
CUSTOMER = "Sundry Debtors (customers)"


@dataclass
class Entry:
    narration: str
    lines: list[tuple[str, int, int]]  # (ledger, debit paise, credit paise)
    finding_id: str | None = None

    def __post_init__(self):
        for ledger, dr, cr in self.lines:
            if dr < 0 or cr < 0 or (dr > 0) == (cr > 0):
                raise ValueError(f"line {ledger!r} must have exactly one positive side")
        dr, cr = self.debit, self.credit
        if dr != cr:
            raise ValueError(f"unbalanced entry {self.narration!r}: Dr {dr} != Cr {cr}")

    @property
    def debit(self) -> int:
        return sum(dr for _, dr, _ in self.lines)

    @property
    def credit(self) -> int:
        return sum(cr for _, _, cr in self.lines)


def _pair(narration: str, dr_ledger: str, cr_ledger: str, amount: int, fid=None) -> Entry | None:
    """A two-line entry; a negative amount swaps the sides, zero means no entry."""
    if amount < 0:
        dr_ledger, cr_ledger, amount = cr_ledger, dr_ledger, -amount
    if amount == 0:
        return None
    return Entry(narration, [(dr_ledger, amount, 0), (cr_ledger, 0, amount)], fid)


# Correcting entries for findings where only the books are wrong: ledger to debit, to credit.
CORRECTIONS = {
    "refund_not_in_books": ("Sales Returns", CUSTOMER),
    "refund_mismatch": ("Sales Returns", CUSTOMER),
    "orphan_payment": (CLEARING, "Sales"),
    "ghost_success": (CLEARING, "Sales"),
    "amount_mismatch": ("Discount Allowed", CUSTOMER),
}


def entries(con, result: Result) -> list[Entry]:
    period = f"{result.period.start:%b %Y}"
    base, gst = con.execute(
        "SELECT COALESCE(sum(fee - tax), 0), COALESCE(sum(tax), 0) FROM rp WHERE type = 'payment'"
    ).fetchone()
    refunds, disputes = con.execute(
        """SELECT COALESCE(sum(amount) FILTER (WHERE type = 'refund'), 0),
                  COALESCE(sum(debit - credit) FILTER (WHERE type = 'dispute'), 0) FROM rp"""
    ).fetchone()
    received = next(s.amount for s in result.razorpay_to_bank if s.kind == "end")

    out: list[Entry | None] = []
    if base or gst:
        fees = [("Payment Gateway Charges", base, 0), ("Input GST", gst, 0), (CLEARING, 0, base + gst)]  # fmt: skip
        out.append(
            Entry(f"Razorpay fees and GST on fees, {period}", [x for x in fees if x[1] or x[2]])
        )
    out.append(
        _pair(f"Razorpay settlements received in bank, {period}", "Bank", CLEARING, received)
    )
    out.append(
        _pair(f"Refunds paid through Razorpay, {period}", "Sales Returns", CLEARING, refunds)
    )
    out.append(_pair(f"Chargebacks debited by Razorpay, {period}", "Chargeback Loss", CLEARING, disputes))  # fmt: skip
    for f in result.findings:
        if f.check in CORRECTIONS:
            dr, cr = CORRECTIONS[f.check]
            ref = f.refs[0] if f.refs else ""
            out.append(_pair(f"{f.id} {f.title} ({ref})", dr, cr, f.amount, f.id))
    return [e for e in out if e]


def to_csv(es: list[Entry], path: Path, on) -> None:
    """Tally-friendly voucher rows: one row per ledger line, entries numbered JV-1, JV-2..."""
    with open(path, "w", newline="") as fh:
        w = CsvWriter(fh)
        w.writerow(["Date", "Voucher Type", "Voucher No", "Ledger", "Dr", "Cr", "Narration"])
        for i, e in enumerate(es, 1):
            for ledger, dr, cr in e.lines:
                w.writerow([f"{on:%d-%m-%Y}", "Journal", f"JV-{i}", ledger,
                            f"{dr / 100:.2f}" if dr else "", f"{cr / 100:.2f}" if cr else "",
                            e.narration])  # fmt: skip


def show(e: Entry) -> str:
    rows = [f"  Dr {ledger:<28} {rupees(dr):>14}" if dr else f"     Cr {ledger:<24} {rupees(cr):>14}"
            for ledger, dr, cr in e.lines]  # fmt: skip
    return "\n".join([e.narration, *rows])
