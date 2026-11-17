import hashlib
import hmac
import json

import duckdb

from app import sample
from app.actions import DEMO_WEBHOOK_SECRET, webhook_replay
from app.engine import run
from app.load import load


def test_webhook_replay_is_signed_like_razorpay(tmp_path):
    sample.write(tmp_path)
    con = duckdb.connect()
    period = load(con, *(tmp_path / v for v in sample.FILES.values()))
    ghost = next(f for f in run(con, period).findings if f.check == "ghost_success")
    req = webhook_replay(ghost)
    expected = hmac.new(DEMO_WEBHOOK_SECRET.encode(), req["body"].encode(), hashlib.sha256)
    assert hmac.compare_digest(req["headers"]["X-Razorpay-Signature"], expected.hexdigest())
    pay = json.loads(req["body"])["payload"]["payment"]["entity"]
    assert pay["id"] == ghost.facts["payment_id"] and pay["status"] == "captured"
    assert pay["amount"] == ghost.amount and pay["notes"]["receipt"] == ghost.facts["order_no"]
