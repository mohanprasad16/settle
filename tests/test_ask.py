import json

import duckdb
import pytest

from app import sample
from app.ask import MAX_CALLS, Answer, Citation, ask, run_sql
from app.engine import run
from app.llm import AgentReply, ToolCall
from app.load import load
from app.money import rupees


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("sample")
    sample.write(out)
    con = duckdb.connect()
    period = load(
        con, out / "razorpay_settlements.csv", out / "bank_statement.csv", out / "orders.csv"
    )
    return con, run(con, period)


class FakeLLM:
    """Scripted agent: plays back tool calls, then final answers."""

    def __init__(self, steps, answers):
        self.steps, self.answers = list(steps), list(answers)
        self.agent_calls = self.json_calls = 0

    def agent_step(self, system, history, tools, *, final=False, usage=None):
        self.agent_calls += 1
        return self.steps.pop(0) if self.steps else AgentReply("done", [])

    def json(self, system, prompt, schema, **kw):
        self.json_calls += 1
        return self.answers.pop(0)


def _sql(q: str) -> AgentReply:
    return AgentReply("", [ToolCall("run_sql", {"query": q})])


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO orders (order_no) VALUES ('x')",
        "DROP TABLE orders",
        "ATTACH 'evil.db'",
        "COPY orders TO '/tmp/out.csv'",
        "SELECT 1; DROP TABLE orders",
        "PRAGMA version",
    ],
)
def test_sql_tool_is_read_only(data, query):
    con, _ = data
    assert run_sql(con, query).startswith("error: only a single SELECT")
    assert con.execute("SELECT count(*) FROM orders").fetchone()[0] > 0


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_text('/etc/hosts')",
        "SELECT * FROM read_csv('https://example.com/x.csv')",
        "SELECT * FROM read_parquet('s3://bucket/x.parquet')",
        "SELECT * FROM glob('/Users/*')",
    ],
)
def test_sql_tool_has_no_file_or_network_access(data, query):
    con, _ = data
    assert run_sql(con, query).startswith("error:")


def test_sql_tool_caps_rows(data):
    con, _ = data
    out = json.loads(run_sql(con, "SELECT row_no FROM rp"))
    assert len(out["rows"]) == 50 and "truncated" in out


def test_answer_with_citations(data):
    con, result = data
    missing = next(f for f in result.findings if f.check == "settlement_missing")
    llm = FakeLLM(
        [_sql("SELECT settlement_id, net, received FROM settlement_bank WHERE received = 0 AND NOT in_transit")],
        [Answer(answer=f"Settlement of {rupees(missing.amount)} never reached your bank.",
                citations=[Citation(source="finding", ref=missing.id),
                           Citation(source="rp", ref="999999")])],
    )  # fmt: skip
    a = ask(con, result, "Why is a settlement missing?", llm)
    assert a.verified and rupees(missing.amount) in a.answer
    assert [(c.source, c.ref) for c in a.citations] == [("finding", missing.id)]  # bad row dropped
    assert a.steps[0]["tool"] == "run_sql"


def test_invented_number_is_caught(data):
    con, result = data
    llm = FakeLLM(
        [_sql("SELECT count(*) FROM findings")],
        [Answer(answer="You lost ₹9,99,999.00.", citations=[]),
         Answer(answer="You lost ₹8,88,888.00.", citations=[])],
    )  # fmt: skip
    a = ask(con, result, "How much did I lose?", llm)
    assert not a.verified and llm.json_calls == 2


def test_step_cap_holds(data):
    con, result = data
    llm = FakeLLM(
        [_sql("SELECT 1") for _ in range(MAX_CALLS + 5)],
        [Answer(answer="Done.", citations=[])],
    )
    a = ask(con, result, "Loop forever", llm)
    assert len(a.steps) == MAX_CALLS and llm.agent_calls == MAX_CALLS


def test_trace_records_each_tool_call(data):
    con, result = data
    llm = FakeLLM(
        [AgentReply("", [ToolCall("get_finding", {"id": "F1"})]),
         _sql("SELECT row_no FROM rp LIMIT 3"), _sql("DROP TABLE rp")],
        [Answer(answer="Done.", citations=[])],
    )  # fmt: skip
    a = ask(con, result, "trace me", llm)
    assert [(t["step"], t["tool"], t["rows_returned"]) for t in a.trace] == [
        (1, "get_finding", 1), (2, "run_sql", 3), (3, "run_sql", 0)
    ]  # fmt: skip
    assert a.trace[1]["input"] == "SELECT row_no FROM rp LIMIT 3" and "error" in a.trace[2]


def test_money_without_rupee_sign_is_sent_back(data):
    con, result = data
    missing = next(f for f in result.findings if f.check == "settlement_missing")
    amount = rupees(missing.amount)
    llm = FakeLLM([], [Answer(answer=f"It was {amount[1:]}.", citations=[]),
                       Answer(answer=f"It was {amount}.", citations=[])])  # fmt: skip
    a = ask(con, result, "How much was the missing settlement?", llm)
    assert a.verified and a.answer == f"It was {amount}." and llm.json_calls == 2


def test_findings_and_contacts_are_given_up_front(data):
    from app.ask import system_prompt
    from app.explain import explain_all

    _, result = data
    prompt = system_prompt(result, explain_all(result, None))
    assert "UTIBR" in prompt and "contact: Razorpay support" in prompt


def test_rupee_float_from_sql_verifies():
    from app.ask import _research_allowed

    ok = _research_allowed(
        [("run_sql", '{"columns": ["total"], "rows": [[2598.0]], "text_columns": []}')]
    )
    assert "2598" in ok


def test_finding_named_by_reference_or_amount_is_cited(data):
    """An answer that names a settlement id or a finding's exact amount, but not F1, still
    shows the finding as proof (seen in the browser test: 'What should I do first?')."""
    from app.ask import _valid

    con, result = data
    missing = next(f for f in result.findings if f.check == "settlement_missing")
    spike = next(f for f in result.findings if f.check == "refund_spike")
    by_ref = _valid(con, result, [], f"Chase {missing.refs[0]} with your bank.")
    by_amount = _valid(con, result, [], f"12 Oct had 28 refunds, {rupees(spike.amount)} in all.")
    assert [c.ref for c in by_ref] == [missing.id]
    assert spike.id in [c.ref for c in by_amount]
    assert _valid(con, result, [], "Nothing here names a problem.") == []
