"""Random months: different problems, counts, days and amounts per seed. The engine must find
exactly what was planted, with exact amounts, and both bridges must close to zero."""

import duckdb
import pytest

from app import sample
from app.engine import run
from app.load import load

SEEDS = range(100, 125)


@pytest.mark.parametrize("seed", SEEDS)
def test_random_month(seed):
    files, truth = sample.chaos(seed)
    con = duckdb.connect()
    result = run(con, load(con, files["rp"], files["bank"], files["orders"]))
    found = sorted((f.check, f.amount) for f in result.findings)
    assert found == sorted((p["check"], p["amount"]) for p in truth["planted"])
    for p in truth["planted"]:
        assert any(
            f.check == p["check"] and f.amount == p["amount"] and p["refs"][0] in f.refs
            for f in result.findings
        ), p
    assert result.totals["unexplained"] == 0
    for bridge in (result.books_to_razorpay, result.razorpay_to_bank):
        assert next(s for s in bridge if s.kind == "residual").amount == 0


def test_seeds_really_differ():
    plans = {repr(sample.plan_for(s)) for s in SEEDS}
    assert len(plans) == len(SEEDS)
