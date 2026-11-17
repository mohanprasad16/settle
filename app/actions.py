"""Fixes a person approves with one click. Nothing here sends anything; it builds the request."""

import hashlib
import hmac
import json
from datetime import timedelta, timezone

from app.engine import Finding

IST = timezone(timedelta(hours=5, minutes=30))
DEMO_WEBHOOK_SECRET = "settle_demo_webhook_secret"  # demo only; live mode uses the merchant's own


def webhook_replay(f: Finding, secret: str = DEMO_WEBHOOK_SECRET) -> dict:
    """Razorpay's payment.captured webhook for a ghost success, signed the way Razorpay signs
    it: X-Razorpay-Signature is the hex HMAC-SHA256 of the raw body, keyed by the webhook secret.
    Posting it to the store's webhook URL marks the order paid."""
    if f.check != "ghost_success":
        raise ValueError(f"webhook replay is for ghost successes, not {f.check}")
    x = f.facts
    created = int(x["captured_on"].replace(tzinfo=IST).timestamp())
    payment = {
        "id": x["payment_id"], "entity": "payment", "amount": x["amount"], "currency": "INR",
        "status": "captured", "order_id": x["razorpay_order_id"], "method": x["method"],
        "captured": True, "notes": {"receipt": x["order_no"]}, "created_at": created,
    }  # fmt: skip
    event = {
        "entity": "event", "account_id": "acc_demo", "event": "payment.captured",
        "contains": ["payment"], "payload": {"payment": {"entity": payment}}, "created_at": created,
    }  # fmt: skip
    body = json.dumps(event, separators=(",", ":"))
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return {
        "body": body,
        "headers": {"Content-Type": "application/json", "X-Razorpay-Signature": signature},
    }
