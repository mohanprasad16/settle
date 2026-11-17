import csv

import duckdb
import httpx
import pytest

from app import sample
from app.connectors.razorpay import ConnectorError, fetch_recon, write_csv
from app.engine import run
from app.load import load

INTS = {"debit", "credit", "amount", "fee", "tax", "created_at", "settled_at"}


def _api_items(path) -> list[dict]:
    """The sample file turned back into what the API returns (ints, bools, nulls)."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        item = {}
        for k, v in r.items():
            if v == "":
                item[k] = None
            elif k in INTS:
                item[k] = int(v)
            elif v in ("true", "false"):
                item[k] = v == "true"
            else:
                item[k] = v
        out.append(item)
    return out


def _client(items, seen=None):
    def handler(request: httpx.Request):
        p = request.url.params
        if seen is not None:
            seen.append((int(p["day"]), int(p["skip"])))
        if request.headers.get("authorization") != "Basic cnpwX2tleTpzZWNyZXQ=":
            return httpx.Response(401, json={"error": {"description": "bad key"}})
        if p["day"] != "1":  # everything on day 1, to exercise paging
            return httpx.Response(200, json={"entity": "collection", "count": 0, "items": []})
        skip, count = int(p["skip"]), int(p["count"])
        page = items[skip : skip + count]
        return httpx.Response(200, json={"entity": "collection", "count": len(page), "items": page})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    out = tmp_path_factory.mktemp("sample")
    sample.write(out)
    return out


def test_pages_until_short_page(files):
    items, seen = _api_items(files / "razorpay_settlements.csv"), []
    got = fetch_recon("rzp_key", "secret", 2026, 10, _client(items, seen))
    assert len(got) == len(items) > 1000
    assert (1, 0) in seen and (1, 1000) in seen and len(seen) == 31 + 1


def test_bad_keys_are_a_clear_error(files):
    with pytest.raises(ConnectorError, match="rejected the API key"):
        fetch_recon("rzp_key", "wrong", 2026, 10, _client([]))


def test_api_data_runs_through_the_same_engine(files, tmp_path):
    items = fetch_recon("rzp_key", "secret", 2026, 10, _client(_api_items(files / "razorpay_settlements.csv")))  # fmt: skip
    write_csv(items, tmp_path / "rp.csv")
    con = duckdb.connect()
    result = run(con, load(con, tmp_path / "rp.csv", files / "bank_statement.csv", files / "orders.csv"))  # fmt: skip
    truth = sample.write(tmp_path / "again")
    assert len(result.findings) == len(truth["planted"])
    assert result.totals["unexplained"] == 0
