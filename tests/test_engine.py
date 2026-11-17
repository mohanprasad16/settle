import csv
import json
from pathlib import Path

import duckdb
import pytest

from app import sample
from app.engine import run
from app.load import LoadError, load
from app.money import rupees, to_paise
from app.rules import Rule, add_rule, load_rules


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("sample")
    truth = sample.write(out)
    con = duckdb.connect()
    period = load(
        con, out / "razorpay_settlements.csv", out / "bank_statement.csv", out / "orders.csv"
    )
    return out, truth, run(con, period)


def test_every_planted_problem_found_with_exact_amount(world):
    _, truth, result = world
    found = {(f.check, f.amount, f.refs[0]) for f in result.findings}
    for p in truth["planted"]:
        hit = [
            f for f in result.findings
            if f.check == p["check"] and f.amount == p["amount"] and p["refs"][0] in f.refs
        ]  # fmt: skip
        assert hit, f"missed {p['check']} {p['refs'][0]} {p['amount']}; found {sorted(found)}"


def test_nothing_else_flagged(world):
    _, truth, result = world
    assert len(result.findings) == len(truth["planted"])


def test_fee_overcharge_lists_exactly_the_planted_payments(world):
    _, truth, result = world
    planted = next(p for p in truth["planted"] if p["check"] == "fee_overcharge")
    found = next(f for f in result.findings if f.check == "fee_overcharge")
    assert sorted(found.refs) == planted["refs"]


def test_both_bridges_close_to_zero(world):
    _, _, result = world
    for bridge in (result.books_to_razorpay, result.razorpay_to_bank):
        assert next(s for s in bridge if s.kind == "residual").amount == 0
        start, end = bridge[0].amount, bridge[-1].amount
        assert start + sum(s.amount for s in bridge[1:-1]) == end


def test_normal_month_noise_is_handled_not_flagged(world):
    _, _, result = world
    labels = {h["label"] for h in result.handled}
    assert "Settlements that arrived as more than one bank credit" in labels
    assert "Partial refunds that match the books" in labels
    timing = {s.label for s in result.razorpay_to_bank if s.kind == "timing"}
    assert "Settled on the last day, reaches the bank next month" in timing
    # Last month's settlement credited on 1 Oct is surfaced, not counted as a problem.
    assert len(result.totals["unmatched_razorpay_credits"]) == 1


def test_period_is_the_bank_statement_month(world):
    _, _, result = world
    assert str(result.period.start) == "2026-10-01" and str(result.period.end) == "2026-10-31"


def test_missing_column_is_a_clear_error(world, tmp_path):
    out, _, _ = world
    rows = list(csv.reader((out / "bank_statement.csv").read_text().splitlines()))
    with open(tmp_path / "bank.csv", "w", newline="") as f:
        csv.writer(f).writerows([r[:2] + r[3:] for r in rows])  # drop ref_no
    with pytest.raises(LoadError, match="ref_no"):
        load(duckdb.connect(), out / "razorpay_settlements.csv", tmp_path / "bank.csv",
             out / "orders.csv")  # fmt: skip


def test_money_formats():
    assert to_paise("1,234.50") == 123450
    assert to_paise("₹ 99") == 9900
    assert to_paise("") == 0
    assert rupees(123456789) == "₹12,34,567.89"
    assert rupees(-5) == "-₹0.05"
    assert rupees(99900) == "₹999.00"


def test_sample_on_disk_matches_generator():
    """data/sample is what the app ships; regenerate it if the generator changes."""
    disk = Path(__file__).resolve().parent.parent / "data" / "sample" / "truth.json"
    assert (
        json.loads(disk.read_text())["planted"]
        == sample.write(Path("/tmp/settle-check"))["planted"]
    )


def test_ghost_success_and_abandoned_checkouts(world):
    _, _, result = world
    ghosts = [f for f in result.findings if f.check == "ghost_success"]
    assert len(ghosts) == 2 and all(f.facts["books_status"] == "pending" for f in ghosts)
    labels = {h["label"]: h["count"] for h in result.handled}
    assert labels["Unpaid checkouts (pending / failed) with no payment, not sales"] == 4


def test_auto_reconciled_share(world):
    _, _, result = world
    a = result.totals["auto_reconciled"]
    assert a["of"] == sum(result.totals["rows"][k] for k in ("razorpay", "orders", "bank"))
    assert 90 < a["pct"] < 100 and a["rows"] < a["of"]


def test_rule_resolves_expected_findings_and_bridge_still_closes(world, tmp_path):
    out, _, before = world
    path = tmp_path / "rules.json"
    rules = add_rule(path, Rule("amount_mismatch", "coupon at checkout", max_amount=10000))
    assert load_rules(path) == rules
    con = duckdb.connect()
    period = load(con, out / "razorpay_settlements.csv", out / "bank_statement.csv", out / "orders.csv")  # fmt: skip
    after = run(con, period, rules=rules)
    assert not [f for f in after.findings if f.check == "amount_mismatch"]
    assert len(after.findings) == len(before.findings) - 2 and len(after.resolved) == 2
    assert {"label": "Resolved by your rule: coupon at checkout", "count": 2} in after.handled
    for bridge in (after.books_to_razorpay, after.razorpay_to_bank):
        assert next(s for s in bridge if s.kind == "residual").amount == 0
    assert any(s.label == "Resolved by your rules" for s in after.books_to_razorpay)
