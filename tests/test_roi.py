import duckdb

from app import sample
from app.engine import run
from app.llm import Usage
from app.load import load
from app.roi import INR_PER_USD, MINUTES_PER_FINDING, MINUTES_PER_ROW_BY_HAND, roi


def test_roi_adds_up(tmp_path):
    sample.write(tmp_path)
    con = duckdb.connect()
    d = tmp_path
    result = run(con, load(con, d / sample.FILES["rp"], d / sample.FILES["bank"], d / sample.FILES["orders"]))  # fmt: skip
    r = roi(result, [Usage(input_tokens=1_000_000, output_tokens=0)])
    t = result.totals
    assert r["leakage_found"] == t["loss"] + t["customer"]
    rows = sum(t["rows"][k] for k in ("razorpay", "orders", "bank"))
    hours = (rows * MINUTES_PER_ROW_BY_HAND - len(result.findings) * MINUTES_PER_FINDING) / 60
    assert r["hours_saved"] == round(hours, 1)
    assert r["ai_cost"] == round(1.5 * INR_PER_USD * 100)  # 1M input tokens at $1.50
    assert r["found_per_rupee_of_ai"] == round(r["leakage_found"] / r["ai_cost"])
    assert "rows ×" in r["formulas"]["hours_saved"]
    assert roi(result, [])["found_per_rupee_of_ai"] is None  # no AI spend, no ratio
