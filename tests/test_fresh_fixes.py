"""The five fresh cases that failed their blind run, now fixed: each must come out exactly as
expected with both bridges at ₹0. Plus a reversal whose bank line carries no UTR."""

import shutil

import duckdb
import pytest

from app import sample
from app.engine import run
from app.load import load
from evals.edge_cases import _bank_line, _read, _write
from evals.fresh_cases import FRESH

FIXED = ["bank reverses a credit", "fee on a refund", "dispute won", "adjustment credit, no reason",
         "refund with no payment link"]  # fmt: skip


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    truth = sample.write(d)
    return d, truth


def _run(d):
    con = duckdb.connect()
    return run(con, load(con, d / sample.FILES["rp"], d / sample.FILES["bank"], d / sample.FILES["orders"]))  # fmt: skip


def _got(result):
    return sorted([(f.check, f.amount) for f in result.findings]
                  + [("exception", e["amount"]) for e in result.exceptions])  # fmt: skip


@pytest.mark.parametrize("name", FIXED)
def test_fixed_case(demo, tmp_path, name):
    base, truth = demo
    case = next(c for c in FRESH if c.name == name)
    d = tmp_path / "m"
    shutil.copytree(base, d)
    refs = {r for p in truth["planted"] for r in p["refs"]}
    extra = case.mutate(d, refs)
    result = _run(d)
    assert _got(result) == sorted([(p["check"], p["amount"]) for p in truth["planted"]] + extra)
    assert result.totals["unexplained"] == 0


def test_reversal_without_utr_on_the_debit(demo, tmp_path):
    base, truth = demo
    d = tmp_path / "m"
    shutil.copytree(base, d)
    hb, bank = _read(d, "bank")
    _, rp = _read(d, "rp")
    b = _bank_line(bank, rp, {r for p in truth["planted"] for r in p["refs"]})
    bank.insert(bank.index(b) + 1, dict(b, narration="NEFT RETURN - BENEFICIARY ACCOUNT CLOSED",
                                        ref_no="", credit="", debit=b["credit"]))  # fmt: skip
    _write(d, "bank", hb, bank)
    result = _run(d)
    rev = [f for f in result.findings if f.check == "settlement_reversed"]
    assert len(rev) == 1 and rev[0].amount == round(float(b["credit"]) * 100)
    assert not [
        f for f in result.findings if f.check == "settlement_short" and f.refs[1] == b["ref_no"]
    ]
    assert result.totals["unexplained"] == 0
