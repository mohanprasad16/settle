import csv
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"


@pytest.fixture
def client():
    return TestClient(app)


def _files(**override):
    files = {
        "razorpay": (
            "razorpay.csv",
            (SAMPLE / "razorpay_settlements.csv").read_bytes(),
            "text/csv",
        ),
        "bank": ("bank.csv", (SAMPLE / "bank_statement.csv").read_bytes(), "text/csv"),
        "orders": ("orders.csv", (SAMPLE / "orders.csv").read_bytes(), "text/csv"),
    }
    return files | override


def _error(r, status):
    assert r.status_code == status, r.text
    body = r.json()
    assert body["error"] and "hint" in body and "Traceback" not in r.text
    return body


def test_nothing_loaded_is_a_plain_error(client):
    assert client.get("/api/result").json() == {"loaded": False}
    _error(client.get("/api/report"), 409)


def test_sample_ties_out_with_no_model_calls(client, monkeypatch):
    monkeypatch.setattr(main, "_llm", lambda: pytest.fail("loading must not call the model"))
    v = client.post("/api/sample").json()
    assert v["totals"]["unexplained"] == 0 and v["findings"]
    assert all(
        s["amount"] == 0 for s in v["bridges"]["books_to_razorpay"] if s["kind"] == "residual"
    )
    assert v["roi"]["formulas"]["hours_saved"] and v["handled"]
    assert client.get("/api/result").json()["totals"] == v["totals"]  # session cookie works


def test_finding_detail_has_explanation_proof_trace_and_actions(client):
    v = client.post("/api/sample").json()
    missing = next(f for f in v["findings"] if f["check"] == "settlement_missing")
    d = client.get(f"/api/finding/{missing['id']}").json()
    assert d["explanation"]["headline"] and d["evidence"][0]["rows"]
    assert d["trace"]["stalled_at"] == "Bank credit" and d["api_call"]["method"] == "GET"
    assert {a["type"] for a in d["actions"]} >= {"razorpay_ticket", "razorpay_api_call"}
    _error(client.get("/api/finding/F999"), 404)


def test_chaos_returns_answer_key_all_found(client):
    v = client.post("/api/chaos", json={"seed": 4242}).json()
    assert v["seed"] == 4242 and v["chaos"]
    assert all(k["found"] for k in v["chaos"])  # every planted problem found, nothing extra


def test_upload_csvs_reconciles(client):
    r = client.post("/api/upload", files=_files())
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "upload" and r.json()["totals"]["unexplained"] == 0


def test_upload_missing_column_says_which(client):
    rows = list(csv.reader((SAMPLE / "bank_statement.csv").read_text().splitlines()))
    buf = io.StringIO()
    csv.writer(buf).writerows([r[:2] + r[3:] for r in rows])  # drop ref_no
    body = _error(client.post("/api/upload", files=_files(bank=("bank.csv", buf.getvalue().encode(), "text/csv"))), 422)  # fmt: skip
    assert "ref_no" in body["error"]


def test_upload_file_in_the_wrong_box(client):
    orders = (SAMPLE / "orders.csv").read_bytes()
    body = _error(client.post("/api/upload", files=_files(bank=("x.csv", orders, "text/csv"))), 422)
    assert "orders export" in body["error"] and body["slot"] == "bank"


def test_locked_pdf_asks_for_password_before_any_model_call(client, monkeypatch):
    monkeypatch.setattr(main, "_llm", lambda: pytest.fail("no model call before the PDF opens"))
    pdf = ("statement.pdf", (SAMPLE / "bank_statement_locked.pdf").read_bytes(), "application/pdf")
    body = _error(client.post("/api/upload", files=_files(bank=pdf)), 422)
    assert body["needs_password"] is True
    body = _error(
        client.post("/api/upload", files=_files(bank=pdf), data={"password": "wrong"}), 422
    )
    assert "didn't open" in body["error"]


def test_rule_marks_expected_and_bridge_still_ties_out(client):
    v = client.post("/api/sample").json()
    mm = [f for f in v["findings"] if f["check"] == "amount_mismatch"]
    after = client.post("/api/rules", json={"finding_id": mm[0]["id"], "note": "coupon at checkout", "scope": "similar"}).json()  # fmt: skip
    assert not [f for f in after["findings"] if f["check"] == "amount_mismatch"]
    assert after["totals"]["unexplained"] == 0 and len(after["resolved"]) == len(mm)
    loss = next(f for f in v["findings"] if f["kind"] == "loss")
    r = client.post("/api/rules", json={"finding_id": loss["id"], "note": "x", "scope": "similar"})
    _error(r, 400)


def test_gate_needs_a_name_runs_dry_and_rejects(client):
    v = client.post("/api/sample").json()
    fid = next(f["id"] for f in v["findings"] if f["check"] == "settlement_missing")
    acts = client.get(f"/api/finding/{fid}").json()["actions"]
    a, b = acts[0], acts[1]
    assert a["status"] == "needs_approval"
    _error(client.post(f"/api/actions/{a['id']}/approve", json={"approver": " "}), 400)
    done = client.post(f"/api/actions/{a['id']}/approve", json={"approver": "Asha"}).json()[
        "action"
    ]
    assert done["status"] == "executed" and done["sent"]["dry_run"] is True
    _error(client.post(f"/api/actions/{a['id']}/approve", json={"approver": "Asha"}), 403)
    assert client.post(f"/api/actions/{b['id']}/reject", json={"approver": "Asha"}).json()["action"]["status"] == "rejected"  # fmt: skip
    _error(client.post(f"/api/actions/{b['id']}/approve", json={"approver": "Asha"}), 403)
    trail = client.get("/api/audit").json()
    mine = {x["id"]: x for x in trail["actions"]}
    assert mine[a["id"]]["status"] == "executed" and mine[b["id"]]["finding_id"] == fid
    assert any(e["actor"] == "human" and e["action"] == "rejected" for e in trail["log"])


def test_sample_has_refunds_per_day(client):
    days = client.post("/api/sample").json()["refunds_by_day"]
    assert len(days) > 20 and all(n > 0 and paise > 0 for _, n, paise in days)


def test_downloads(client):
    client.post("/api/sample")
    page = client.get("/api/report").text
    assert "Sign-off" in page and "Kaapi Kart" in page
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/export").content))
    assert {"findings.csv", "bridges.csv", "journals.csv", "audit_report.html"} <= set(z.namelist())
    assert client.get("/api/journal").text.startswith("Date,")


def test_ai_off_and_rate_limit_are_plain_errors(client, monkeypatch):
    client.post("/api/sample")
    monkeypatch.setattr(main.settings, "gemini_api_key", "")
    _error(client.post("/api/ask", json={"question": "why?"}), 503)
    monkeypatch.setattr(main.settings, "gemini_api_key", "set")
    monkeypatch.setattr(main, "AI_CALLS_PER_WINDOW", 0)
    _error(client.post("/api/ask", json={"question": "why?"}), 429)
    _error(client.post("/api/ask", json={"question": "  "}), 400)


def test_bad_json_is_a_plain_error(client):
    client.post("/api/sample")
    _error(client.post("/api/ask", content=b"not json", headers={"content-type": "application/json"}), 400)  # fmt: skip


def test_settlements_tie_to_the_bank_bridge(client):
    client.post("/api/sample")
    body = client.get("/api/settlements").json()
    rows, bridge = body["settlements"], client.get("/api/result").json()["bridges"]
    rtb = bridge["razorpay_to_bank"]
    assert body["totals"]["net"] == rtb[0]["amount"]  # Net settled by Razorpay
    assert body["totals"]["received"] == rtb[-1]["amount"]  # Received in your bank
    status = {r["status"] for r in rows}
    assert {"matched", "missing", "short", "in_transit"} <= status
    assert {"utr", "amount_date", "split"} <= {r["matched_by"] for r in rows}
    missing = next(r for r in rows if r["status"] == "missing")
    assert missing["finding_id"] and missing["received"] == 0 and missing["bank_rows"] == []
    for r in rows:  # the per-type breakdown in the slide-over adds up to the payout
        assert sum(r["by_type"].values()) == r["net"]
    value = client.get("/api/result").json()["totals"]["value_reconciled"]
    matched = sum(r["net"] for r in rows if r["status"] == "matched")
    assert value["amount"] == matched and value["of"] == body["totals"]["net"]


def test_brs_csv_ends_at_the_bank_balance(client):
    client.post("/api/sample")
    lines = list(csv.reader(io.StringIO(client.get("/api/brs").text)))
    assert lines[0][0].startswith("Bank Reconciliation Statement")
    assert lines[2][0] == "Balance as per Razorpay settlements"
    assert ["Unexplained difference", "0.00"] in lines
    received = client.get("/api/result").json()["bridges"]["razorpay_to_bank"][-1]["amount"]
    assert (
        lines[-1][0].startswith("Balance as per bank") and lines[-1][1] == f"{received / 100:.2f}"
    )
