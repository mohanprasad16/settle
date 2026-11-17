"""Follow one rupee: order -> payment -> refunds / disputes -> settlement -> bank credit.

Give it any id a person has in hand (order no, pay_, rfnd_, disp_, setl_ or a UTR) after
engine.run, and it returns each hop with ok / missing / mismatch / waiting, so the screen can
show where the money stalled: the first missing hop, else the first mismatch. A hop is a
mismatch when a finding cites its row.
"""

from datetime import timedelta

from app.engine import Result, _norm, _q
from app.money import rupees


def trace(con, result: Result, ref: str) -> dict:
    ref = ref.strip()
    # Rows a finding is about. Settlement findings cite every item in the settlement and risk
    # flags are patterns, so neither makes a single payment's hop a mismatch.
    item_findings = [f for f in result.findings if f.kind != "risk" and not f.check.startswith("settlement_")]  # fmt: skip
    cited = {(src, n) for f in item_findings for src, rows in f.rows.items() for n in rows}
    late = result.period.end - timedelta(days=2)

    def hop(name, status, amount=None, ids=(), detail="", rows=(), flag=True):
        if flag and status == "ok" and any(r in cited for r in rows):
            status = "mismatch"
        return {"hop": name, "status": status, "amount": amount, "ids": list(ids),
                "detail": detail, "rows": [list(r) for r in rows]}  # fmt: skip

    key = ref.upper()
    sid = None
    order = _q(con, "SELECT * FROM orders WHERE order_no = ?", [key])
    if not order:
        item = _q(con, "SELECT * FROM other WHERE entity_id = ? UNION ALL BY NAME "
                       "SELECT *, order_receipt AS receipt FROM rp WHERE entity_id = ? AND type = 'payment'",
                  [ref, ref])  # fmt: skip
        if item and item[0]["receipt"]:
            order = _q(con, "SELECT * FROM orders WHERE order_no = ?", [item[0]["receipt"]])
            key = item[0]["receipt"]
        elif item:
            sid = item[0]["settlement_id"]
        else:
            s = _q(con, "SELECT settlement_id FROM settlement_bank WHERE settlement_id = ? "
                        "OR upper(replace(utr, ' ', '')) = ?", [ref, _norm(ref)])  # fmt: skip
            if not s:
                return {"ref": ref, "found": False, "hops": [], "stalled_at": None}
            sid = s[0]["settlement_id"]

    hops = []
    if order or sid is None:
        if order:
            o = order[0]
            hops.append(hop("Order in your books", "ok", o["amount"], [o["order_no"]],
                            f"{o['status']} on {o['order_date']}", [("orders", o["row_no"])]))  # fmt: skip
        else:
            hops.append(hop("Order in your books", "missing", None, [key], "no order with this number"))  # fmt: skip
        pays = _q(con, "SELECT * FROM rp WHERE type = 'payment' AND order_receipt = ? ORDER BY created_at", [key])  # fmt: skip
        if not pays:
            waiting = order and order[0]["order_date"] > late
            hops.append(hop("Payment at Razorpay", "waiting" if waiting else "missing", None, [],
                            "settles next month" if waiting else "no payment captured"))  # fmt: skip
        for p in pays:
            hops.append(hop("Payment at Razorpay", "ok", p["amount"], [p["entity_id"]],
                            f"{p['method']}, fee {rupees(p['fee'])}", [("rp", p["row_no"])]))  # fmt: skip
        for r in _q(con, "SELECT * FROM other WHERE receipt = ? AND type IN ('refund', 'dispute') ORDER BY created_at", [key]):  # fmt: skip
            label = "Refund" if r["type"] == "refund" else "Chargeback"
            hops.append(hop(label, "ok", -r["amount"], [r["entity_id"]], "", [("rp", r["row_no"])]))
        sids = list(dict.fromkeys(p["settlement_id"] for p in pays))
    else:
        sids = [sid]

    for s in _q(con, f"SELECT * FROM settlement_bank WHERE settlement_id IN ({', '.join('?' * len(sids)) or 'NULL'})", sids):  # fmt: skip
        hops.append(hop("Razorpay settlement", "ok", s["net"], [s["settlement_id"], s["utr"]],
                        f"settled {s['settled_at']:%d %b %Y}, {s['n_items']} items",
                        [("rp", n) for n in s["rp_rows"]], flag=False))  # fmt: skip
        if s["n_credits"]:
            how = {"utr": "by UTR", "amount_date": "by amount and date", "split": "as a split"}
            status = "ok" if s["received"] == s["net"] else "mismatch"
            hops.append(hop("Bank credit", status, s["received"], [s["utr"]],
                            f"matched {how[s['matched_by']]}", [("bank", n) for n in s["bank_rows"]]))  # fmt: skip
        elif s["unmatched_reason"]:
            hops.append(hop("Bank credit", "missing", None, [], f"couldn't match: {s['unmatched_reason']}"))  # fmt: skip
        elif s["in_transit"]:
            hops.append(hop("Bank credit", "waiting", None, [], "reaches the bank next month"))
        else:
            hops.append(hop("Bank credit", "missing", None, [], "no bank credit for this settlement"))  # fmt: skip

    stalled = next((h["hop"] for h in hops if h["status"] in ("missing", "waiting")), None)
    stalled = stalled or next((h["hop"] for h in hops if h["status"] == "mismatch"), None)
    return {"ref": ref, "found": True, "hops": hops, "stalled_at": stalled}
