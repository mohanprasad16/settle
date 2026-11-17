"""👍 / 👎 on Settle AI's answers, and the follow-up chips: both built so the browser can't put
words in anyone's mouth."""

import csv
import io
import zipfile

from app import copilot
from app.ask import Answer, Citation
from tests.test_copilot import _say, _with, client  # noqa: F401  (fixture)


def test_rating_is_stored_logged_and_exported(client, monkeypatch):  # noqa: F811
    _with(monkeypatch, [], answer="The 14 Oct payout never reached your bank.")
    out = _say(client, "why is money lost?", page="overview")
    aid = out["answer_id"]
    assert (
        client.post("/api/feedback", json={"answer_id": aid, "rating": "up"}).json()["count"] == 1
    )
    # Changing your mind replaces the rating rather than counting twice.
    r = client.post("/api/feedback", json={"answer_id": aid, "rating": "down", "reason": "wrong_number", "note": "  says 14 Oct,\n should be 21  "})  # fmt: skip
    assert r.json()["count"] == 1
    log = client.get("/api/audit").json()["log"]
    assert any(e["action"] == "rated an answer" and "Wrong number" in e["detail"] for e in log)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/export").content))
    rows = list(csv.DictReader(io.StringIO(z.read("feedback.csv").decode())))
    assert len(rows) == 1
    assert rows[0]["rating"] == "down" and rows[0]["reason"] == "Wrong number"
    assert rows[0]["note"] == "says 14 Oct, should be 21"
    # The question and answer come from the session, not from the browser.
    assert rows[0]["question"] == "why is money lost?" and rows[0]["page"] == "overview"
    assert rows[0]["answer"] == "The 14 Oct payout never reached your bank."


def test_rating_rejects_unknown_answers_and_values(client, monkeypatch):  # noqa: F811
    _with(monkeypatch, [])
    aid = _say(client, "hi")["answer_id"]
    assert client.post("/api/feedback", json={"answer_id": "nope", "rating": "up"}).status_code == 404  # fmt: skip
    assert client.post("/api/feedback", json={"answer_id": aid, "rating": "meh"}).status_code == 422  # fmt: skip
    assert client.post("/api/feedback", json={"answer_id": aid, "rating": "down", "reason": "=HYPERLINK(1)"}).status_code == 422  # fmt: skip


def test_feedback_csv_cells_cannot_run_as_formulas(client, monkeypatch):  # noqa: F811
    _with(monkeypatch, [])
    aid = _say(client, "=cmd|'/c calc'!A1")["answer_id"]
    client.post("/api/feedback", json={"answer_id": aid, "rating": "down", "reason": "other", "note": "=HYPERLINK(\"x\")"})  # fmt: skip
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/export").content))
    text = z.read("feedback.csv").decode()
    assert "\n=" not in text and ",=" not in text


def test_follow_ups_come_from_code_not_the_model(client):  # noqa: F811
    from app.main import SESSIONS

    result = next(iter(SESSIONS.values())).result
    f = next(x for x in result.findings if x.kind == "loss")
    got = copilot.follow_ups(result, "overview", [Citation(source="finding", ref=f.id)], [], "x")
    assert got[0] == f"Draft the fix for {f.id}" and len(got) == 3
    # An id the engine never produced (say, typed into a payment note) is never turned into a chip.
    assert all("F999" not in s for s in copilot.follow_ups(result, "issues", [Citation(source="finding", ref="F999")], [], ""))  # fmt: skip
    # Never repeats what was just asked, and falls back to the page's own suggestions.
    asked = copilot.PAGE_FOLLOW["settlements"][0]
    assert asked not in copilot.follow_ups(result, "settlements", [], [], asked)
    assert copilot.follow_ups(result, "nowhere", [], [], "") == copilot.PAGE_FOLLOW["overview"][:3]


def test_answer_model_unchanged_contract():
    # The chips are code-made; the model's Answer has no field for them.
    assert set(Answer.model_fields) == {"answer", "citations"}
