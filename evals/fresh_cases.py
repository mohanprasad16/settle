"""Fresh unseen cases: written after the engine was frozen for this eval, with each expectation
decided from the domain before running it once. Failures here are reported, not fixed: they go
to docs/FAILURES.md as known limits. Same shape as evals/edge_cases.py.

Expected check names describe what a correct reconciler should say; where Settle has no check
for it yet (refund_fee, exception), the case can only pass by flagging it some other way.
"""

from datetime import datetime

from evals.edge_cases import Case, _bank_line, _clean_payment, _read, _shift_bank, _write


def _host(rp, utr):
    return next(x for x in rp if x["settlement_utr"] == utr)


def split_across_two_days_no_utr(d, refs):
    """One settlement lands as two credits on consecutive days, neither carries the UTR."""
    hb, bank = _read(d, "bank")
    _, rp = _read(d, "rp")
    b = _bank_line(bank, rp, refs)
    total = round(float(b["credit"]) * 100)
    day = datetime.strptime(b["txn_date"], "%d/%m/%Y")
    second = dict(b, txn_date=f"{day.day + 1:02d}/{day:%m/%Y}", ref_no="",
                  narration="NEFT CR-RAZORPAY SOFTWARE PVT LTD PART 2",
                  credit=f"{(total - total // 2) / 100:.2f}")  # fmt: skip
    b.update(ref_no="", narration="NEFT CR-RAZORPAY SOFTWARE PVT LTD", credit=f"{total // 2 / 100:.2f}")  # fmt: skip
    bank.insert(bank.index(b) + 1, second)
    _write(d, "bank", hb, bank)
    return []


def refund_larger_than_payment(d, refs):
    """Razorpay refunds ₹100 more than the order; the books refund the order amount."""
    h, rp = _read(d, "rp")
    ho, orders = _read(d, "orders")
    hb, bank = _read(d, "bank")
    r, o = _clean_payment(rp, orders, refs)
    host = _host(rp, _bank_line(bank, rp, refs)["ref_no"])
    amt = int(r["amount"]) + 10000
    rp.append(dict(r, entity_id="rfnd_freshTooBig", type="refund", debit=amt, credit=0, amount=amt,
                   fee=0, tax=0, payment_id=r["entity_id"], settlement_id=host["settlement_id"],
                   settlement_utr=host["settlement_utr"], settled_at=host["settled_at"],
                   created_at=host["created_at"]))  # fmt: skip
    _shift_bank(bank, host["settlement_utr"], -amt)
    o.update(status="refunded", refund_amount=o["amount"], refund_date=o["order_date"])
    for k, hh, rows in (("rp", h, rp), ("orders", ho, orders), ("bank", hb, bank)):
        _write(d, k, hh, rows)
    return [("refund_mismatch", 10000)]


def created_after_settled(d, refs):
    """A payment whose created_at is a day after its settled_at: a data error, no money wrong."""
    h, rp = _read(d, "rp")
    _, orders = _read(d, "orders")
    r, _ = _clean_payment(rp, orders, refs)
    r["created_at"] = str(int(r["settled_at"]) + 86400)
    _write(d, "rp", h, rp)
    return []


def bank_reverses_a_credit(d, refs):
    """The bank credits a settlement, then reverses it two days later: the money isn't there."""
    hb, bank = _read(d, "bank")
    _, rp = _read(d, "rp")
    b = _bank_line(bank, rp, refs)
    net = round(float(b["credit"]) * 100)
    day = datetime.strptime(b["txn_date"], "%d/%m/%Y")
    bank.insert(bank.index(b) + 1, dict(b, txn_date=f"{day.day + 2:02d}/{day:%m/%Y}",
                narration=f"REV NEFT CR RETURNED-{b['ref_no']}", credit="", debit=b["credit"]))  # fmt: skip
    _write(d, "bank", hb, bank)
    return [("settlement_missing", net)]


def imps_reference_instead_of_utr(d, refs):
    """The bank prints its own IMPS reference, not Razorpay's UTR."""
    hb, bank = _read(d, "bank")
    _, rp = _read(d, "rp")
    b = _bank_line(bank, rp, refs)
    b.update(narration="IMPS/P2A/629104471125/RAZORPAY SOFTWARE", ref_no="629104471125")
    _write(d, "bank", hb, bank)
    return []


def fee_on_a_refund(d, refs):
    """Razorpay deducts a ₹5 + GST fee on a refund. Refunds carry no fee under the contract."""
    h, rp = _read(d, "rp")
    ho, orders = _read(d, "orders")
    hb, bank = _read(d, "bank")
    r, o = _clean_payment(rp, orders, refs)
    host = _host(rp, _bank_line(bank, rp, refs)["ref_no"])
    amt = int(r["amount"])
    rp.append(dict(r, entity_id="rfnd_freshFee", type="refund", debit=amt + 590, credit=0,
                   amount=amt, fee=590, tax=90, payment_id=r["entity_id"],
                   settlement_id=host["settlement_id"], settlement_utr=host["settlement_utr"],
                   settled_at=host["settled_at"], created_at=host["created_at"]))  # fmt: skip
    _shift_bank(bank, host["settlement_utr"], -(amt + 590))
    o.update(status="refunded", refund_amount=o["amount"], refund_date=o["order_date"])
    for k, hh, rows in (("rp", h, rp), ("orders", ho, orders), ("bank", hb, bank)):
        _write(d, k, hh, rows)
    return [("refund_fee", 590)]


def dispute_won(d, refs):
    """A chargeback is debited and then credited back when the merchant wins. Net zero."""
    h, rp = _read(d, "rp")
    _, orders = _read(d, "orders")
    hb, bank = _read(d, "bank")
    r, _ = _clean_payment(rp, orders, refs)
    host = _host(rp, _bank_line(bank, rp, refs)["ref_no"])
    amt = int(r["amount"])
    common = dict(r, type="dispute", amount=amt, fee=0, tax=0, payment_id=r["entity_id"],
                  settlement_id=host["settlement_id"], settlement_utr=host["settlement_utr"],
                  settled_at=host["settled_at"], created_at=host["created_at"],
                  dispute_id="disp_freshWon", description="Chargeback")  # fmt: skip
    rp.append(dict(common, entity_id="disp_freshWon", debit=amt, credit=0))
    rp.append(dict(common, entity_id="disp_freshWonBack", debit=0, credit=amt, description="Dispute won"))  # fmt: skip
    _write(d, "rp", h, rp)
    _write(d, "bank", hb, bank)
    return []


def adjustment_credit_no_reason(d, refs):
    """Razorpay credits ₹500 with no description: money you can't explain, for a person to check."""
    h, rp = _read(d, "rp")
    hb, bank = _read(d, "bank")
    host = _host(rp, _bank_line(bank, rp, refs)["ref_no"])
    rp.append(dict(host, entity_id="adj_freshCredit", type="adjustment", debit=0, credit=50000,
                   amount=50000, fee=0, tax=0, description="", order_receipt="", payment_id=""))  # fmt: skip
    _shift_bank(bank, host["settlement_utr"], 50000)
    _write(d, "rp", h, rp)
    _write(d, "bank", hb, bank)
    return [("unexplained_adjustment", 50000)]


def refund_booked_on_the_twin_order(d, refs):
    """Same customer, two orders of the same amount: Razorpay refunds order A, the books record
    the refund on order B. Both orders are now wrong in the books."""
    h, rp = _read(d, "rp")
    ho, orders = _read(d, "orders")
    hb, bank = _read(d, "bank")
    a_pay, a = _clean_payment(rp, orders, refs)
    b = next(o for o in orders if o["amount"] == a["amount"] and o["order_no"] != a["order_no"]
             and o["status"] == "paid" and not o["refund_amount"] and o["order_no"] not in refs
             and o["payment_method"] == "razorpay")  # fmt: skip
    b["customer"] = a["customer"]
    host = _host(rp, _bank_line(bank, rp, refs)["ref_no"])
    amt = int(a_pay["amount"])
    rp.append(dict(a_pay, entity_id="rfnd_freshTwin", type="refund", debit=amt, credit=0,
                   amount=amt, fee=0, tax=0, payment_id=a_pay["entity_id"],
                   settlement_id=host["settlement_id"], settlement_utr=host["settlement_utr"],
                   settled_at=host["settled_at"], created_at=host["created_at"]))  # fmt: skip
    _shift_bank(bank, host["settlement_utr"], -amt)
    b.update(status="refunded", refund_amount=b["amount"], refund_date=b["order_date"])
    for k, hh, rows in (("rp", h, rp), ("orders", ho, orders), ("bank", hb, bank)):
        _write(d, k, hh, rows)
    return [("refund_not_in_books", amt), ("books_refund_not_processed", amt)]


def orders_with_currency_column_and_rupee_symbols(d, refs):
    """An export with an extra currency column and amounts like '₹1,499.00'."""
    h, orders = _read(d, "orders")
    for o in orders:
        o["currency"] = "INR"
        for k in ("amount", "refund_amount"):
            if o[k]:
                rupees, paise = f"{float(o[k]):.2f}".split(".")
                o[k] = f"₹{int(rupees):,}.{paise}"
    _write(d, "orders", [*h, "currency"], orders)
    return []


def refund_with_no_payment_link(d, refs):
    """A refund row with no payment_id and no receipt: Settle can't tie it to an order, so a
    correct reconciler should put it in front of a person, not drop it into a timing bucket."""
    h, rp = _read(d, "rp")
    hb, bank = _read(d, "bank")
    host = _host(rp, _bank_line(bank, rp, refs)["ref_no"])
    rp.append(dict(host, entity_id="rfnd_freshNoLink", type="refund", debit=89900, credit=0,
                   amount=89900, fee=0, tax=0, payment_id="", order_receipt="", order_id="",
                   created_at=host["created_at"]))  # fmt: skip
    _shift_bank(bank, host["settlement_utr"], -89900)
    _write(d, "rp", h, rp)
    _write(d, "bank", hb, bank)
    return [("exception", 89900)]


FRESH = [
    Case("split over two days, no UTR", "one settlement as two credits on consecutive days", split_across_two_days_no_utr),
    Case("refund larger than payment", "Razorpay refunds ₹100 more than the order", refund_larger_than_payment),
    Case("created after settled", "payment timestamp a day after its settlement", created_after_settled),
    Case("bank reverses a credit", "credit, then a REV debit of it two days later", bank_reverses_a_credit),
    Case("IMPS reference, no UTR", "bank prints its own IMPS reference", imps_reference_instead_of_utr),
    Case("fee on a refund", "₹5 + GST fee deducted on a refund", fee_on_a_refund),
    Case("dispute won", "chargeback debited and credited back", dispute_won),
    Case("adjustment credit, no reason", "₹500 credited with no description", adjustment_credit_no_reason),
    Case("refund booked on the twin order", "same customer, same amount, refund on the wrong order", refund_booked_on_the_twin_order),
    Case("currency column and ₹ amounts", "orders export with currency column, '₹1,499.00'", orders_with_currency_column_and_rupee_symbols),
    Case("refund with no payment link", "refund row with no payment_id or receipt", refund_with_no_payment_link),
]  # fmt: skip
