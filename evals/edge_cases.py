"""Adversarial months: the demo month with one nasty, real-world twist each.

Every case states what a correct reconciler should report, decided from the domain, not from
what the engine happens to do. Expected = the month's planted problems, plus or minus the twist.
"""

import codecs
import csv
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app import sample

Expect = list[tuple[str, int]]  # (check, paise) added to the planted set; [] = "no new finding"


@dataclass
class Case:
    name: str
    what: str
    mutate: Callable[[Path, set[str]], Expect]


def _read(d: Path, key: str) -> tuple[list[str], list[dict]]:
    with open(d / sample.FILES[key], newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return list(rows[0].keys()), rows


def _write(d: Path, key: str, header: list[str], rows: list[dict]) -> None:
    with open(d / sample.FILES[key], "w", newline="") as f:
        w = csv.DictWriter(f, header, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


BANKED_BY = int(datetime(2026, 10, 30, tzinfo=sample.IST).timestamp())  # later: in transit


def _clean_payment(rp, orders, refs, method=None) -> tuple[dict, dict]:
    """A settled payment for a plain paid order that no planted problem touches."""
    by_no = {o["order_no"]: o for o in orders}
    for r in rp:
        o = by_no.get(r["order_receipt"])
        if (r["type"] == "payment" and o and o["status"] == "paid" and not o["refund_amount"]
                and r["entity_id"] not in refs and o["order_no"] not in refs
                and r["settlement_id"] not in refs and int(r["settled_at"]) < BANKED_BY
                and (method is None or r["method"] == method)):  # fmt: skip
            return r, o
    raise LookupError("no clean payment")


def _bank_line(bank, rp, refs) -> dict:
    """The single bank credit of a settlement no planted problem touches."""
    sid = {r["settlement_utr"]: r["settlement_id"] for r in rp}
    for b in bank:
        if (b["ref_no"] in sid and sid[b["ref_no"]] not in refs and not b["debit"]
                and sum(x["ref_no"] == b["ref_no"] for x in bank) == 1):  # fmt: skip
            return b
    raise LookupError("no clean bank line")


def _shift_bank(bank, utr: str, paise: int) -> None:
    b = next(x for x in bank if x["ref_no"] == utr)
    b["credit"] = f"{(round(float(b['credit']) * 100) + paise) / 100:.2f}"


# --- the cases ---------------------------------------------------------------------------------


def _capture_short(d, refs, by: int) -> Expect:
    """Captured `by` paise below the order. Razorpay's fee and credit follow the captured amount,
    so the settlement and its bank credit drop too; only the order is off."""
    h, rp = _read(d, "rp")
    hb, bank = _read(d, "bank")
    _, orders = _read(d, "orders")
    r, _ = _clean_payment(rp, orders, refs, "upi")
    amount = int(r["amount"]) - by
    fee, tax = sample._fee(amount, sample.RATE_BP["default"])
    _shift_bank(bank, r["settlement_utr"], amount - fee - int(r["credit"]))
    r.update(amount=amount, fee=fee, tax=tax, credit=amount - fee)
    _write(d, "rp", h, rp)
    _write(d, "bank", hb, bank)
    return [("amount_mismatch", by)]


def captured_one_paisa_short(d, refs):
    return _capture_short(d, refs, 1)


def captured_one_rupee_short(d, refs):
    return _capture_short(d, refs, 100)


def bank_one_paisa_short(d, refs):
    hb, bank = _read(d, "bank")
    _, rp = _read(d, "rp")
    b = _bank_line(bank, rp, refs)
    _shift_bank(bank, b["ref_no"], -1)
    _write(d, "bank", hb, bank)
    return [("settlement_short", 1)]


def twin_orders_same_amount_swapped(d, refs):
    """Two orders, same amount, same day: the payments point at each other's receipts.
    The money per order is still right, so nothing is wrong."""
    h, rp = _read(d, "rp")
    _, orders = _read(d, "orders")
    by_no = {o["order_no"]: o for o in orders}
    pays = [r for r in rp if r["type"] == "payment" and r["order_receipt"] in by_no
            and by_no[r["order_receipt"]]["status"] == "paid" and not by_no[r["order_receipt"]]["refund_amount"]
            and r["entity_id"] not in refs and r["order_receipt"] not in refs]  # fmt: skip
    seen = {}
    for r in pays:
        key = (by_no[r["order_receipt"]]["order_date"], r["amount"])
        if key in seen:
            a, b = seen[key], r
            a["order_receipt"], b["order_receipt"] = b["order_receipt"], a["order_receipt"]
            _write(d, "rp", h, rp)
            return []
        seen[key] = r
    raise LookupError("no twins")


def refund_on_last_day(d, refs):
    """Refunded in the books on 31 Oct; Razorpay deducts it in November. Timing, not a problem."""
    h, orders = _read(d, "orders")
    _, rp = _read(d, "rp")
    _, o = _clean_payment(rp, orders, refs)
    o.update(status="refunded", refund_amount=o["amount"], refund_date="2026-10-31")
    _write(d, "orders", h, orders)
    return []


def two_partial_refunds_make_full(d, refs):
    h, rp = _read(d, "rp")
    ho, orders = _read(d, "orders")
    hb, bank = _read(d, "bank")
    r, o = _clean_payment(rp, orders, refs)
    amt = int(r["amount"])
    first, second = amt // 2, amt - amt // 2
    utr = _bank_line(bank, rp, refs)["ref_no"]
    host = next(x for x in rp if x["settlement_utr"] == utr)
    for i, part in enumerate((first, second)):
        rp.append(dict(r, entity_id=f"rfnd_edgePartial{i}", type="refund", debit=part, credit=0,
                       amount=part, fee=0, tax=0, payment_id=r["entity_id"],
                       settlement_id=host["settlement_id"], settlement_utr=host["settlement_utr"],
                       settled_at=host["settled_at"], created_at=host["created_at"]))  # fmt: skip
    _shift_bank(bank, host["settlement_utr"], -amt)
    o.update(status="refunded", refund_amount=f"{amt / 100:.2f}", refund_date=o["order_date"])
    _write(d, "rp", h, rp)
    _write(d, "orders", ho, orders)
    _write(d, "bank", hb, bank)
    return []


def negative_settlement(d, refs):
    """A day with only refunds: Razorpay debits the account. Bank shows a debit with the UTR."""
    h, rp = _read(d, "rp")
    ho, orders = _read(d, "orders")
    hb, bank = _read(d, "bank")
    r, o = _clean_payment(rp, orders, refs)
    amt = int(r["amount"])
    settled = str(int(r["settled_at"]) + 2 * 86400)
    rp.append(dict(r, entity_id="rfnd_edgeNegative", type="refund", debit=amt, credit=0,
                   amount=amt, fee=0, tax=0, payment_id=r["entity_id"], settlement_id="setl_edgeNegative",
                   settlement_utr="UTIBR5NEGATIVE01", settled_at=settled,
                   created_at=str(int(r["created_at"]) + 86400)))  # fmt: skip
    o.update(status="refunded", refund_amount=f"{amt / 100:.2f}", refund_date=o["order_date"])
    day = datetime.fromtimestamp(int(settled), sample.IST)
    bank.append({"txn_date": day.strftime("%d/%m/%Y"),
                 "narration": "NEFT DR-RAZORPAY SOFTWARE PVT LTD-UTIBR5NEGATIVE01",
                 "ref_no": "UTIBR5NEGATIVE01", "debit": f"{amt / 100:.2f}", "credit": "",
                 "balance": ""})  # fmt: skip
    _write(d, "rp", h, rp)
    _write(d, "orders", ho, orders)
    _write(d, "bank", hb, bank)
    return []


def utr_only_in_ref_no(d, refs):
    hb, bank = _read(d, "bank")
    _, rp = _read(d, "rp")
    _bank_line(bank, rp, refs)["narration"] = "NEFT CR-YESB0000001-RAZORPAY SOFTWARE PVT LTD"
    _write(d, "bank", hb, bank)
    return []


def utr_lowercase_in_narration(d, refs):
    hb, bank = _read(d, "bank")
    _, rp = _read(d, "rp")
    b = _bank_line(bank, rp, refs)
    b["narration"], b["ref_no"] = b["narration"].lower(), ""
    _write(d, "bank", hb, bank)
    return []


def utr_padded_in_narration(d, refs):
    hb, bank = _read(d, "bank")
    _, rp = _read(d, "rp")
    b = _bank_line(bank, rp, refs)
    b["narration"] = b["narration"].replace(b["ref_no"], f"  {b['ref_no']}  ")
    b["ref_no"] = ""
    _write(d, "bank", hb, bank)
    return []


def bank_posted_twice(d, refs):
    """The bank shows the same credit twice: Razorpay paid once. The excess is a mismatch."""
    hb, bank = _read(d, "bank")
    _, rp = _read(d, "rp")
    b = _bank_line(bank, rp, refs)
    bank.insert(bank.index(b) + 1, dict(b))
    _write(d, "bank", hb, bank)
    return [("settlement_short", -round(float(b["credit"]) * 100))]


def order_no_whitespace(d, refs):
    h, orders = _read(d, "orders")
    _, rp = _read(d, "rp")
    _, o = _clean_payment(rp, orders, refs)
    o["order_no"] = f"  {o['order_no']} "
    _write(d, "orders", h, orders)
    return []


def order_no_lowercase(d, refs):
    h, orders = _read(d, "orders")
    _, rp = _read(d, "rp")
    _, o = _clean_payment(rp, orders, refs)
    o["order_no"] = o["order_no"].lower()
    _write(d, "orders", h, orders)
    return []


def _as_network(d, refs, network: str, bp: int) -> Expect:
    h, rp = _read(d, "rp")
    hb, bank = _read(d, "bank")
    _, orders = _read(d, "orders")
    r, _ = _clean_payment(rp, orders, refs, "upi")
    fee, tax = sample._fee(int(r["amount"]), bp)
    credit = int(r["amount"]) - fee
    _shift_bank(bank, r["settlement_utr"], credit - int(r["credit"]))
    r.update(
        method="card", card_network=network, card_type="credit", fee=fee, tax=tax, credit=credit
    )
    _write(d, "rp", h, rp)
    _write(d, "bank", hb, bank)
    return []


def amex_at_three_percent(d, refs):
    return _as_network(d, refs, "American Express", 300)


def amex_spelled_differently(d, refs):
    return _as_network(d, refs, "AMEX", 300)


def diners_international(d, refs):
    return _as_network(d, refs, "Diners Club International", 300)


def zero_amount_payment(d, refs):
    """A ₹0 card-mandate registration: no money moved, nothing to reconcile."""
    h, rp = _read(d, "rp")
    _, orders = _read(d, "orders")
    r, _ = _clean_payment(rp, orders, refs)
    rp.append(dict(r, entity_id="pay_edgeZeroMandate", amount=0, fee=0, tax=0, credit=0,
                   debit=0, order_receipt="", order_id="", method="card"))  # fmt: skip
    _write(d, "rp", h, rp)
    return []


def bom_and_crlf(d, refs):
    """Files saved by Excel on Windows: a byte-order mark and CRLF line endings."""
    for key in sample.FILES:
        h, rows = _read(d, key)
        with open(d / sample.FILES[key], "w", newline="", encoding="utf-8") as f:
            f.write(codecs.BOM_UTF8.decode())
            w = csv.DictWriter(f, h, lineterminator="\r\n")
            w.writeheader()
            w.writerows(rows)
    return []


def dates_dd_mm_yyyy(d, refs):
    h, orders = _read(d, "orders")
    for o in orders:
        for k in ("order_date", "refund_date"):
            if o[k]:
                y, m, dd = o[k].split("-")
                o[k] = f"{dd}-{m}-{y}"
    _write(d, "orders", h, orders)
    return []


CASES = [
    Case("captured ₹0.01 short", "payment captured one paisa below the order", captured_one_paisa_short),
    Case("captured ₹1 short", "payment captured one rupee below the order", captured_one_rupee_short),
    Case("bank ₹0.01 short", "bank credit one paisa below the settlement", bank_one_paisa_short),
    Case("twin orders swapped", "same amount, same day, receipts crossed", twin_orders_same_amount_swapped),
    Case("refund on 31 Oct", "books refund on the last day, Razorpay deducts in Nov", refund_on_last_day),
    Case("partial refunds = full", "two half refunds against one full refund in books", two_partial_refunds_make_full),
    Case("negative settlement", "a refunds-only day, bank shows a debit", negative_settlement),
    Case("UTR only in ref_no", "bank narration has no UTR", utr_only_in_ref_no),
    Case("UTR lowercase", "UTR only in narration, lowercased", utr_lowercase_in_narration),
    Case("UTR padded", "UTR only in narration, extra spaces", utr_padded_in_narration),
    Case("bank posted twice", "the same credit appears twice", bank_posted_twice),
    Case("order no whitespace", "order number with spaces in the books", order_no_whitespace),
    Case("order no lowercase", "order number lowercased in the books", order_no_lowercase),
    Case("Amex at 3%", "card network American Express, fee at 3%", amex_at_three_percent),
    Case("AMEX spelled differently", "network written 'AMEX', fee at 3%", amex_spelled_differently),
    Case("Diners Club International", "odd network name, fee at 3%", diners_international),
    Case("₹0 mandate payment", "zero-amount registration payment, no receipt", zero_amount_payment),
    Case("BOM + CRLF", "all three files saved by Excel on Windows", bom_and_crlf),
    Case("dates DD-MM-YYYY", "order and refund dates as 31-10-2026", dates_dd_mm_yyyy),
]  # fmt: skip
