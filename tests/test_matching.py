"""Rate card, bank matching without UTRs, refund patterns, trace, and the edge cases the held-out
eval found (each test is the eval's repro: the demo month with one mutation)."""

import csv
import shutil

import duckdb
import pytest

from app import sample
from app.ask import Citation, _valid
from app.engine import RateCard, run
from app.load import load
from app.trace import trace

FILES = ("razorpay_settlements.csv", "bank_statement.csv", "orders.csv")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    truth = sample.write(out)
    con = duckdb.connect()
    return out, truth, con, run(con, load(con, *(out / f for f in FILES)))


def _mutated(demo, tmp_path, name, change):
    """Copy the demo month, let change() edit one file's rows, run the engine on it."""
    out = demo[0]
    for f in FILES:
        shutil.copy(out / f, tmp_path / f)
    with open(tmp_path / name, newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows = change(rows) or rows
    with open(tmp_path / name, "w", newline="") as fh:
        w = csv.DictWriter(fh, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    con = duckdb.connect()
    return con, run(con, load(con, *(tmp_path / f for f in FILES)))


def _key(result):
    return sorted((f.check, f.amount) for f in result.findings)


def _closes(result):
    return all(next(s for s in b if s.kind == "residual").amount == 0
               for b in (result.books_to_razorpay, result.razorpay_to_bank))  # fmt: skip


def test_rate_card():
    r = RateCard()
    assert r.bp("upi", None, None) == 0
    assert r.bp("card", "debit", "RuPay") == 0
    assert r.bp("card", "debit", "Visa") == 90
    assert r.bp("card", "credit", "RuPay") == 195
    assert r.bp("card", "debit", "American Express") == 295
    assert r.bp("netbanking", None, None) == r.bp("wallet", None, None) == 190


def test_debit_cards_charged_as_credit(demo):
    f = next(f for f in demo[3].findings if f.check == "rate_misclassified")
    assert f.kind == "loss" and f.facts["contract_rate_pct"] == 0.9
    assert f.facts["charged_rate_pct"] == 1.95


def test_bank_lines_without_utr_are_matched(demo):
    _, truth, con, result = demo
    got = dict(con.execute("SELECT settlement_id, matched_by FROM settlement_bank").fetchall())
    assert {s: got[s] for s in truth["matched_by"]} == truth["matched_by"]
    assert set(truth["matched_by"].values()) == {"amount_date", "split"}
    assert not result.exceptions


def test_two_credits_that_fit_one_settlement_are_left_for_a_person(demo, tmp_path):
    def double_post(rows):
        i = next(i for i, r in enumerate(rows) if r["credit"] and not r["ref_no"])
        rows.insert(i + 1, dict(rows[i]))

    _, result = _mutated(demo, tmp_path, "bank_statement.csv", double_post)
    assert len(result.exceptions) == 1 and "2 bank credits" in result.exceptions[0]["why"]
    assert _key(result) == _key(demo[3])  # no guess: not missing, not matched
    assert _closes(result)
    assert any(s.kind == "exception" for s in result.razorpay_to_bank)


def test_utr_match_ignores_case_and_spaces(demo, tmp_path):
    def lower(rows):
        r = next(r for r in rows if r["ref_no"].startswith("UTIBR5") and "PART" not in r["narration"])  # fmt: skip
        r["narration"] = r["narration"].replace(r["ref_no"], " ".join(r["ref_no"].lower()))
        r["ref_no"] = ""

    assert _key(_mutated(demo, tmp_path, "bank_statement.csv", lower)[1]) == _key(demo[3])


def test_order_numbers_match_whatever_the_case(demo, tmp_path):
    def lower(rows):
        r = next(r for r in rows if r["status"] == "paid" and r["payment_method"] == "razorpay")
        r["order_no"] = f"  {r['order_no'].lower()} "

    assert _key(_mutated(demo, tmp_path, "orders.csv", lower)[1]) == _key(demo[3])


def test_card_network_aliases(demo, tmp_path):
    def rename(rows):
        for r in rows:
            r["card_network"] = {"American Express": "AMEX", "Diners Club": "Diners Club International"}.get(r["card_network"], r["card_network"])  # fmt: skip

    assert _key(_mutated(demo, tmp_path, "razorpay_settlements.csv", rename)[1]) == _key(demo[3])


def test_zero_amount_payment_is_not_a_finding(demo, tmp_path):
    def mandate(rows):
        r = dict(next(r for r in rows if r["type"] == "payment"))
        r |= {"entity_id": "pay_ZeroMandate001", "amount": "0", "credit": "0", "fee": "0",
              "tax": "0", "order_receipt": "", "order_id": ""}  # fmt: skip
        rows.append(r)

    _, result = _mutated(demo, tmp_path, "razorpay_settlements.csv", mandate)
    assert _key(result) == _key(demo[3]) and _closes(result)
    assert {"label": "Zero-amount payments (card mandates / authorisations)", "count": 1} in result.handled  # fmt: skip


def test_bank_double_posting_is_an_over_credit(demo, tmp_path):
    posted = {}

    def double(rows):
        i = next(i for i, r in enumerate(rows) if r["ref_no"].startswith("UTIBR5") and r["txn_date"] == "06/10/2026")  # fmt: skip
        posted["amount"] = rows[i]["credit"]
        rows.insert(i + 1, dict(rows[i]))

    _, result = _mutated(demo, tmp_path, "bank_statement.csv", double)
    over = [f for f in result.findings if f.check == "settlement_over"]
    assert len(over) == 1 and over[0].kind == "books"
    assert over[0].amount == round(float(posted["amount"]) * 100)
    assert not [f for f in result.findings if f.check == "settlement_short" and f.amount < 0]
    assert _closes(result)


def test_refund_patterns_are_flags_not_money(demo):
    _, _, _, result = demo
    risk = {f.check: f for f in result.findings if f.kind == "risk"}
    assert set(risk) == {"refund_spike", "repeat_refunder"}
    spike = risk["refund_spike"]
    assert spike.facts["refunds"] > 4 * spike.facts["usual_per_day"]
    assert risk["repeat_refunder"].facts["customer"] == sample.REPEAT_NAME
    labels = {s.label for b in (result.books_to_razorpay, result.razorpay_to_bank) for s in b}
    assert "Unusual spike in refunds" not in labels and _closes(result)


def test_trace_normal_order_is_all_ok(demo):
    _, _, con, result = demo
    order = con.execute("""SELECT o.order_no FROM order_pay o JOIN pay p ON p.order_receipt = o.order_no
        JOIN settlement_bank s USING (settlement_id) WHERE o.n_pay = 1 AND o.paid = o.amount
        AND o.status = 'paid' AND s.matched_by = 'utr' AND s.received = s.net LIMIT 1""").fetchone()[0]  # fmt: skip
    t = trace(con, result, order)
    assert t["stalled_at"] is None and [h["status"] for h in t["hops"]] == ["ok"] * 4


def test_trace_stops_at_the_bank_for_a_missing_settlement(demo):
    _, _, con, result = demo
    sid = next(f for f in result.findings if f.check == "settlement_missing").refs[0]
    order = con.execute(f"""SELECT order_receipt FROM rp WHERE settlement_id = '{sid}' AND type = 'payment'
        AND order_receipt IN (SELECT order_no FROM books) LIMIT 1""").fetchone()[0]  # fmt: skip
    t = trace(con, result, order)
    assert t["stalled_at"] == "Bank credit"
    assert [h["status"] for h in t["hops"]][:2] == ["ok", "ok"]


def test_trace_stops_at_the_payment_for_an_unpaid_order(demo):
    _, _, con, result = demo
    order = next(f for f in result.findings if f.check == "paid_order_no_payment").refs[0]
    t = trace(con, result, order.lower())
    assert t["stalled_at"] == "Payment at Razorpay"
    assert trace(con, result, "no-such-id")["found"] is False


def test_ask_cites_findings_it_names(demo):
    _, _, con, result = demo
    kept = _valid(con, result, [Citation(source="finding", ref="F2")], "Start with F1, then F2 and F1.")  # fmt: skip
    assert [(c.source, c.ref) for c in kept] == [("finding", "F2"), ("finding", "F1")]
