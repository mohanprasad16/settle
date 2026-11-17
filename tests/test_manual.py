"""Manual matches: a person (or the Copilot, confirmed by a person) links bank lines the engine
wouldn't guess. Code checks the tie; the bridge still closes; an amount never changes."""

import duckdb

from app.engine import check_manual, run
from app.load import load
from app.rules import ManualMatch, suggest_rules
from tests.test_matching import FILES, _closes, _key, _mutated


def test_unmatched_prior_month_credit_explained(demo):
    out, _, con, result = demo
    row = result.totals["unmatched_razorpay_credits"][0]["row_no"]
    assert check_manual(con, result, None, [row]) is None
    m = ManualMatch("Prior-month settlement credited on 1 Oct", "Priya", [row])
    con2 = duckdb.connect()
    after = run(con2, load(con2, *(out / f for f in FILES)), manual=[m])
    assert not after.totals["unmatched_razorpay_credits"]
    assert {"label": "Matched by Priya: Prior-month settlement credited on 1 Oct", "count": 1} in after.handled  # fmt: skip
    assert _key(after) == _key(result) and _closes(after)
    assert after.totals["auto_reconciled"]["rows"] == result.totals["auto_reconciled"]["rows"] + 1


def test_ambiguous_settlement_linked_by_a_person(demo, tmp_path):
    def double_post(rows):
        i = next(i for i, r in enumerate(rows) if r["credit"] and not r["ref_no"])
        rows.insert(i + 1, dict(rows[i]))

    con, result = _mutated(demo, tmp_path, "bank_statement.csv", double_post)
    exc = result.exceptions[0]
    rows = [
        n
        for (n,) in con.execute(
            "SELECT row_no FROM bank WHERE credit - debit = ? ORDER BY row_no", [exc["amount"]]
        ).fetchall()
    ]
    assert len(rows) == 2
    assert check_manual(con, result, exc["settlement_id"], rows[:1]) is None
    m = ManualMatch("The first credit is this payout", "Priya", rows[:1], exc["settlement_id"])
    con2 = duckdb.connect()
    after = run(con2, load(con2, *(tmp_path / f for f in FILES)), manual=[m])
    assert not after.exceptions and _closes(after)
    got = con2.execute(
        "SELECT matched_by FROM settlement_bank WHERE settlement_id = ?", [exc["settlement_id"]]
    ).fetchone()[0]
    assert got == "manual"
    # the second credit is now an unmatched Razorpay credit, shown for a person, never hidden
    assert [u["row_no"] for u in after.totals["unmatched_razorpay_credits"]][-1] == rows[1]


def test_a_match_that_doesnt_tie_is_refused(demo):
    _, _, con, result = demo
    missing = next(f for f in result.findings if f.check == "settlement_missing")
    row = result.totals["unmatched_razorpay_credits"][0]["row_no"]
    why = check_manual(con, result, missing.refs[0], [row])
    assert why and "must tie exactly" in why
    # and the engine itself ignores a non-tying link even if one slips through
    m = ManualMatch("wrong", "x", [row], missing.refs[0])
    con2 = duckdb.connect()
    out = demo[0]
    after = run(con2, load(con2, *(out / f for f in FILES)), manual=[m])
    assert any(f.check == "settlement_missing" for f in after.findings) and _closes(after)


def test_already_matched_line_is_refused(demo):
    _, _, con, result = demo
    (row,) = con.execute("SELECT bank_rows[1] FROM settlement_bank WHERE matched_by = 'utr' LIMIT 1").fetchone()  # fmt: skip
    assert "already matched" in check_manual(con, result, None, [row])


def test_suggested_rules_only_repeat_books_checks(demo):
    sug = suggest_rules(demo[3].findings)
    checks = {s["check"] for s in sug}
    assert "amount_mismatch" in checks
    am = next(s for s in sug if s["check"] == "amount_mismatch")
    assert am["count"] == 2 and am["max_amount"] == 10000
    assert not checks & {"settlement_missing", "duplicate_refund", "chargeback"}
