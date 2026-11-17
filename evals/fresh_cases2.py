"""Fresh batch 2: written after batch 1 was fixed, each expectation decided from the domain before
the batch was run once. Failures are reported as they came out and listed in docs/FAILURES.md as
known limits; nothing in the engine is changed for this table. Same shape as evals/edge_cases.py.
"""

from app.engine import RateCard
from evals.edge_cases import BANKED_BY, Case, _bank_line, _read, _shift_bank, _write


def _clean_pair(rp, orders, bank, refs, n=1, same_amount=False):
    """n settled payments for plain paid orders, each in a settlement with one UTR bank credit."""
    by_no = {o["order_no"]: o for o in orders}
    utrs = {b["ref_no"] for b in bank if b["ref_no"] and not b["debit"]}
    single = {u for u in utrs if sum(b["ref_no"] == u for b in bank) == 1}
    picks: dict[str, list] = {}
    for r in rp:
        o = by_no.get(r["order_receipt"])
        if (r["type"] == "payment" and o and o["status"] == "paid" and not o["refund_amount"]
                and r["entity_id"] not in refs and o["order_no"] not in refs
                and r["settlement_id"] not in refs and r["settlement_utr"] in single
                and int(r["settled_at"]) < BANKED_BY):  # fmt: skip
            key = r["amount"] if same_amount else "any"
            picks.setdefault(key, []).append((r, o))
            if len(picks[key]) == n:
                return picks[key]
    raise LookupError("no clean payments")


def net_zero_settlement(d, refs):
    """A settlement of two labelled adjustments that cancel: Razorpay owes nothing, the bank
    shows nothing. Nothing to flag."""
    h, rp = _read(d, "rp")
    host = next(r for r in rp if r["type"] == "payment")
    common = dict(host, type="adjustment", fee=0, tax=0, order_receipt="", payment_id="",
                  order_id="", method="", card_network="", card_type="", card_issuer="",
                  settlement_id="setl_freshZeroNet", settlement_utr="UTIBR5000000000Z",
                  amount=25000)  # fmt: skip
    rp.append(dict(common, entity_id="adj_freshFee", debit=25000, credit=0, description="Platform fee Oct"))  # fmt: skip
    rp.append(dict(common, entity_id="adj_freshWaiver", debit=0, credit=25000, description="Platform fee waived"))  # fmt: skip
    _write(d, "rp", h, rp)
    return []


def partial_capture_and_partial_refund(d, refs):
    """Order of X captured ₹100 short, then ₹400 refunded and booked as ₹400. Only the short
    capture is wrong."""
    h, rp = _read(d, "rp")
    ho, orders = _read(d, "orders")
    hb, bank = _read(d, "bank")
    [(r, o)] = _clean_pair(rp, orders, bank, refs)
    old_net = int(r["credit"])
    amt = int(r["amount"]) - 10000
    bp = RateCard().bp(r["method"], r["card_type"] or None, r["card_network"] or None)
    base = (amt * bp + 5000) // 10000
    tax = (base * 18 + 50) // 100
    r.update(amount=amt, fee=base + tax, tax=tax, credit=amt - base - tax)
    rp.append(dict(r, entity_id="rfnd_freshPartial", type="refund", debit=40000, credit=0,
                   amount=40000, fee=0, tax=0, payment_id=r["entity_id"]))  # fmt: skip
    _shift_bank(bank, r["settlement_utr"], int(r["credit"]) - old_net - 40000)
    o.update(status="partially_refunded", refund_amount="400.00", refund_date=o["order_date"])
    for k, hh, rows in (("rp", h, rp), ("orders", ho, orders), ("bank", hb, bank)):
        _write(d, k, hh, rows)
    return [("amount_mismatch", 10000)]


def bank_one_paisa_over(d, refs):
    """The bank credits a settlement one paisa more than Razorpay settled, UTR present."""
    hb, bank = _read(d, "bank")
    _, rp = _read(d, "rp")
    b = _bank_line(bank, rp, refs)
    _shift_bank(bank, b["ref_no"], 1)
    _write(d, "bank", hb, bank)
    return [("settlement_over", 1)]


def twin_refunds_same_minute(d, refs):
    """Two different orders of the same amount refunded in the same minute, both booked. Normal."""
    h, rp = _read(d, "rp")
    ho, orders = _read(d, "orders")
    hb, bank = _read(d, "bank")
    pairs = _clean_pair(rp, orders, bank, refs, n=2, same_amount=True)
    for i, (r, o) in enumerate(pairs):
        amt = int(r["amount"])
        rp.append(dict(r, entity_id=f"rfnd_freshTwin{i}", type="refund", debit=amt, credit=0,
                       amount=amt, fee=0, tax=0, payment_id=r["entity_id"],
                       created_at=pairs[0][0]["created_at"]))  # fmt: skip
        _shift_bank(bank, r["settlement_utr"], -amt)
        o.update(status="refunded", refund_amount=o["amount"], refund_date=o["order_date"])
    for k, hh, rows in (("rp", h, rp), ("orders", ho, orders), ("bank", hb, bank)):
        _write(d, k, hh, rows)
    return []


def chargeback_on_last_months_order(d, refs):
    """A chargeback debited in October on a September order. The money is gone this month: a
    chargeback, even though the order isn't in October's books."""
    h, rp = _read(d, "rp")
    hb, bank = _read(d, "bank")
    first = min(int(r["created_at"]) for r in rp if r["type"] == "payment")
    old = next(r for r in rp if r["type"] == "payment" and int(r["created_at"]) == first)
    host = next(r for r in rp if r["settlement_utr"] == _bank_line(bank, rp, refs)["ref_no"])
    amt = int(old["amount"])
    rp.append(dict(old, entity_id="disp_freshOld", type="dispute", debit=amt, credit=0, amount=amt,
                   fee=0, tax=0, payment_id=old["entity_id"], dispute_id="disp_freshOld",
                   description="Chargeback: fraud", settlement_id=host["settlement_id"],
                   settlement_utr=host["settlement_utr"], settled_at=host["settled_at"],
                   created_at=host["created_at"]))  # fmt: skip
    _shift_bank(bank, host["settlement_utr"], -amt)
    _write(d, "rp", h, rp)
    _write(d, "bank", hb, bank)
    return [("chargeback", amt)]


def three_decimal_amounts(d, refs):
    """An orders export that writes amounts with three decimals: '1499.000'."""
    h, orders = _read(d, "orders")
    for o in orders:
        for k in ("amount", "refund_amount"):
            if o[k]:
                o[k] = f"{float(o[k]):.3f}"
    _write(d, "orders", h, orders)
    return []


def duplicated_report_row(d, refs):
    """The Razorpay export repeats one payment row (same entity_id twice). The money moved once;
    a person should see the duplicate row, not a duplicate payment or a bank gap."""
    h, rp = _read(d, "rp")
    _, orders = _read(d, "orders")
    _, bank = _read(d, "bank")
    [(r, _)] = _clean_pair(rp, orders, bank, refs)
    rp.insert(rp.index(r) + 1, dict(r))
    _write(d, "rp", h, rp)
    return [("exception", int(r["credit"]))]


def statement_spans_two_months(d, refs):
    """The bank statement starts on 25 Sep: last month's lines first, then October. The month
    being reconciled is still October."""
    hb, bank = _read(d, "bank")
    first = bank[0]
    sept = [
        dict(first, txn_date="25/09/2026", narration="IMPS DR-RENT-SEP-INDIRANAGAR UNIT", ref_no="",
             debit="85000.00", credit=""),
        dict(first, txn_date="28/09/2026", narration="NEFT CR-AMAZON SELLER SERVICES-DISB", ref_no="AMZ77001",
             debit="", credit="15200.00"),
    ]  # fmt: skip
    _write(d, "bank", hb, sept + bank)
    return []


FRESH2 = [
    Case("settlement with ₹0 net", "two labelled adjustments that cancel, nothing in the bank", net_zero_settlement),
    Case("partial capture + partial refund", "₹100 short captured, then ₹400 refunded and booked", partial_capture_and_partial_refund),
    Case("bank ₹0.01 over, with UTR", "bank credit one paisa more than settled", bank_one_paisa_over),
    Case("twin refunds, same minute", "two orders of the same amount refunded together, both booked", twin_refunds_same_minute),
    Case("chargeback on last month's order", "October chargeback on a September payment", chargeback_on_last_months_order),
    Case("three-decimal amounts", "orders export with '1499.000'", three_decimal_amounts),
    Case("duplicated report row", "one payment row repeated in the Razorpay export", duplicated_report_row),
    Case("statement spans two months", "bank statement starts on 25 Sep", statement_spans_two_months),
]  # fmt: skip
