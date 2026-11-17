import duckdb
import pytest

from app import sample
from app.engine import KINDS, Finding, run
from app.explain import (
    Explanation,
    cache_key,
    explain,
    explain_all,
    fact_sheet,
    load_cache,
    save_cache,
    template,
    unsupported,
)
from app.llm import LLMError
from app.load import load


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    out = tmp_path_factory.mktemp("sample")
    sample.write(out)
    con = duckdb.connect()
    period = load(
        con, out / "razorpay_settlements.csv", out / "bank_statement.csv", out / "orders.csv"
    )
    return run(con, period)


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def json(self, system, prompt, schema, **kw):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _exp(text: str) -> Explanation:
    return Explanation(
        headline="Settlement missing",
        what_happened=text,
        likely_cause="Most likely the transfer bounced.",
        what_to_do=["Raise a ticket."],
        who_to_contact="Razorpay support",
    )


def _missing(result) -> Finding:
    return next(f for f in result.findings if f.check == "settlement_missing")


def test_correct_answer_passes(result):
    f = _missing(result)
    amount = fact_sheet(f)["net_settled"]
    llm = FakeLLM([_exp(f"Razorpay settled {amount} on {fact_sheet(f)['settled_on']}.")])
    exp = explain(f, llm)
    assert exp.source == "model" and llm.calls == 1


def test_invented_number_retries_then_falls_back(result):
    f = _missing(result)
    llm = FakeLLM([_exp("You lost ₹99,999.00 here."), _exp("About 37% of sales vanished.")])
    exp = explain(f, llm)
    assert llm.calls == 2 and exp.source == "template"


def test_retry_can_recover(result):
    f = _missing(result)
    good = _exp(f"Razorpay settled {fact_sheet(f)['net_settled']}.")
    llm = FakeLLM([_exp("You lost ₹99,999.00 here."), good])
    assert explain(f, llm).source == "model"


def test_model_error_falls_back(result):
    exp = explain(_missing(result), FakeLLM([LLMError("503")]))
    assert exp.source == "template"


def test_every_check_has_a_template():
    for check in KINDS:
        assert template(Finding(check, 100, ["x"], {}, {})).headline


def test_templates_pass_the_number_guard(result):
    for f in result.findings:
        assert unsupported(template(f), fact_sheet(f)) == [], f.check


def test_cache_round_trips(result, tmp_path):
    cache = {}
    first = explain_all(result, None, cache)
    save_cache(tmp_path / "c.json", cache)
    loaded = load_cache(tmp_path / "c.json")
    llm = FakeLLM([])  # any model call would fail: everything must come from the cache
    again = explain_all(result, llm, loaded)
    assert again == first and llm.calls == 0
    assert set(loaded) == {cache_key(f) for f in result.findings}
