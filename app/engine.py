"""The reconciliation engine. Deterministic SQL over the loaded tables, no model involved.

Every payment, order, refund, dispute, adjustment and settlement lands in exactly one bucket.
Each bucket is either a finding (a real problem) or a handled item (fees, timing, partial
refunds...). The two bridges add the buckets up; if anything was missed, the "unexplained"
line is not zero, and the tests fail.
"""

import json
from dataclasses import dataclass, field
from datetime import timedelta

import duckdb

from app.load import Period
from app.rules import Rule

KINDS = {
    "settlement_missing": ("loss", "Settlement never reached your bank"),
    "settlement_short": ("loss", "Bank credited less than the settlement"),
    "paid_order_no_payment": ("loss", "Order marked paid, but no payment at Razorpay"),
    "duplicate_refund": ("loss", "Refund paid out twice"),
    "chargeback": ("loss", "Chargeback debited, order still shows paid"),
    "fee_overcharge": ("loss", "Fees above your agreed rate"),
    "gst_mismatch": ("loss", "GST on fees isn't 18%"),
    "unexplained_adjustment": ("loss", "Adjustment debited with no reason"),
    "duplicate_payment": ("customer", "Customer charged twice for one order"),
    "books_refund_not_processed": ("customer", "Books say refunded, no refund went out"),
    "refund_mismatch": ("books", "Refund at Razorpay differs from the books"),
    "ghost_success": ("books", "Paid at Razorpay, order still pending in your store"),
    "orphan_payment": ("books", "Payment with no order in your books"),
    "refund_not_in_books": ("books", "Refunded at Razorpay, books still say paid"),
    "amount_mismatch": ("books", "Amount captured differs from the order"),
}
# Findings that make Razorpay higher than the books (step goes up the waterfall, not down).
UP = {"duplicate_payment", "orphan_payment", "ghost_success", "books_refund_not_processed"}
UNPAID = "('pending', 'failed', 'created')"  # store statuses for checkouts that aren't sales


@dataclass
class Pricing:
    default_bp: int = 200  # 2.00%
    premium_bp: int = 300  # Amex / Diners / international
    premium_networks: tuple[str, ...] = ("American Express", "Diners Club")
    gst_pct: int = 18
    fee_tolerance: int = 2  # paise per payment: rounding, not overcharging
    settlement_lag_days: int = 2


@dataclass
class Finding:
    check: str
    amount: int  # paise; the rupee impact
    refs: list[str]  # ids a person would search for: order no, pay_/rfnd_/setl_ ids
    rows: dict[str, list[int]]  # source table -> row numbers, the evidence
    facts: dict  # everything the explanation is allowed to say
    id: str = ""

    @property
    def kind(self) -> str:
        return KINDS[self.check][0]

    @property
    def title(self) -> str:
        return KINDS[self.check][1]


@dataclass
class Step:
    label: str
    amount: int  # signed paise change, applied going down the waterfall
    kind: str  # normal | timing | finding
    count: int = 0


@dataclass
class Result:
    period: Period
    findings: list[Finding]
    books_to_razorpay: list[Step]
    razorpay_to_bank: list[Step]
    totals: dict = field(default_factory=dict)
    handled: list[dict] = field(default_factory=list)
    resolved: list[tuple[Finding, Rule]] = field(default_factory=list)


def _q(con, sql: str, params=None) -> list[dict]:
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _views(con, p: Period, pr: Pricing) -> None:
    lag_cutoff = p.end - timedelta(days=pr.settlement_lag_days)
    premium = ", ".join("'" + n.replace("'", "''") + "'" for n in pr.premium_networks)
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW books AS
        SELECT * FROM orders
        WHERE payment_method = 'razorpay' AND status NOT IN ('cancelled', {UNPAID[1:-1]})
          AND order_date BETWEEN DATE '{p.start}' AND DATE '{p.end}';

        CREATE OR REPLACE TEMP VIEW pay AS
        SELECT *, CAST(created_at AS DATE) AS created_date,
               (amount * CASE WHEN card_network IN ({premium}) THEN {pr.premium_bp}
                              ELSE {pr.default_bp} END + 5000) // 10000 AS exp_base
        FROM rp WHERE type = 'payment';

        -- Every non-payment row needs the order it belongs to: its own receipt, else its payment's.
        CREATE OR REPLACE TEMP VIEW other AS
        SELECT o.*, COALESCE(o.order_receipt, p.order_receipt) AS receipt,
               p.entity_id IS NOT NULL AND CAST(p.created_at AS DATE) >= DATE '{p.start}' AS pay_in_period
        FROM rp o LEFT JOIN rp p ON p.entity_id = o.payment_id AND p.type = 'payment'
        WHERE o.type <> 'payment';

        CREATE OR REPLACE TEMP VIEW pay_class AS
        SELECT pay.*, CASE
            WHEN order_receipt IN (SELECT order_no FROM books) THEN 'matched'
            WHEN order_receipt IN (SELECT order_no FROM orders WHERE status IN {UNPAID}
                 AND order_date BETWEEN DATE '{p.start}' AND DATE '{p.end}') THEN 'ghost'
            WHEN created_date < DATE '{p.start}' THEN 'carry_in'
            ELSE 'orphan' END AS bucket
        FROM pay;

        CREATE OR REPLACE TEMP VIEW order_pay AS
        SELECT b.order_no, b.row_no, b.order_date, b.amount, b.status, b.refund_amount,
               b.refund_date, b.customer,
               count(p.entity_id) AS n_pay, COALESCE(sum(p.amount), 0) AS paid,
               list(p.entity_id ORDER BY p.created_at) AS pay_ids,
               list(p.row_no ORDER BY p.created_at) AS pay_rows,
               b.order_date > DATE '{lag_cutoff}' AS late
        FROM books b LEFT JOIN pay p ON p.order_receipt = b.order_no
        GROUP BY ALL;

        CREATE OR REPLACE TEMP VIEW refund_by_order AS
        SELECT b.order_no, b.row_no, b.status, b.refund_amount AS rb, b.refund_date,
               COALESCE(sum(r.amount), 0) AS rr, count(r.entity_id) AS n_ref,
               list(r.entity_id ORDER BY r.created_at) FILTER (WHERE r.entity_id IS NOT NULL) AS ref_ids,
               list(r.row_no ORDER BY r.created_at) FILTER (WHERE r.entity_id IS NOT NULL) AS ref_rows,
               b.refund_date > DATE '{lag_cutoff}' AS late
        FROM books b LEFT JOIN other r ON r.receipt = b.order_no AND r.type = 'refund'
        GROUP BY ALL;

        CREATE OR REPLACE TEMP VIEW settlement AS
        SELECT settlement_id, max(utr) AS utr, max(settled_at) AS settled_at,
               sum(credit) - sum(debit) AS net, count(*) AS n_items,
               list(row_no ORDER BY row_no) AS rp_rows
        FROM rp GROUP BY settlement_id;

        CREATE OR REPLACE TEMP VIEW settlement_bank AS
        SELECT s.*, COALESCE(sum(b.credit) - sum(b.debit), 0) AS received,
               count(b.row_no) AS n_credits, list(b.row_no) FILTER (WHERE b.row_no IS NOT NULL) AS bank_rows,
               CAST(s.settled_at AS DATE) >= DATE '{p.end}' AS in_transit
        FROM settlement s LEFT JOIN bank b
          ON s.utr IS NOT NULL AND (b.ref_no = s.utr OR contains(b.narration, s.utr))
        GROUP BY ALL;
        """)


def run(
    con: duckdb.DuckDBPyConnection,
    period: Period,
    pricing: Pricing | None = None,
    rules: list[Rule] | None = None,
) -> Result:
    pr = pricing or Pricing()
    _views(con, period, pr)
    f: list[Finding] = []

    # --- Orders vs payments -------------------------------------------------------------
    for o in _q(con, "SELECT * FROM order_pay ORDER BY order_date, order_no"):
        rows = {"orders": [o["row_no"]], "rp": [r for r in (o["pay_rows"] or []) if r]}
        if o["n_pay"] == 0:
            if not o["late"]:
                f.append(Finding("paid_order_no_payment", o["amount"], [o["order_no"]], rows, {
                    "order_no": o["order_no"], "order_date": o["order_date"],
                    "order_amount": o["amount"], "customer": o["customer"]}))  # fmt: skip
        elif o["paid"] != o["amount"]:
            ids = [i for i in o["pay_ids"] if i]
            facts = {"order_no": o["order_no"], "order_amount": o["amount"],
                     "captured": o["paid"], "payment_ids": ids, "difference": o["amount"] - o["paid"]}  # fmt: skip
            if o["n_pay"] > 1 and o["paid"] > o["amount"]:
                f.append(Finding("duplicate_payment", o["paid"] - o["amount"], [o["order_no"], *ids], rows, facts))  # fmt: skip
            else:
                f.append(Finding("amount_mismatch", o["amount"] - o["paid"], [o["order_no"], *ids], rows, facts))  # fmt: skip

    for p in _q(
        con,
        """SELECT p.*, o.status AS books_status, o.row_no AS order_row
                        FROM pay_class p JOIN orders o ON o.order_no = p.order_receipt
                        WHERE bucket = 'ghost' ORDER BY p.created_at""",
    ):
        f.append(Finding("ghost_success", p["amount"], [p["order_receipt"], p["entity_id"]],
            {"orders": [p["order_row"]], "rp": [p["row_no"]]}, {
            "order_no": p["order_receipt"], "books_status": p["books_status"],
            "payment_id": p["entity_id"], "amount": p["amount"], "captured_on": p["created_at"],
            "method": p["method"], "razorpay_order_id": p["order_id"]}))  # fmt: skip

    for p in _q(con, "SELECT * FROM pay_class WHERE bucket = 'orphan' ORDER BY created_at"):
        f.append(Finding("orphan_payment", p["amount"], [p["entity_id"]], {"rp": [p["row_no"]]}, {
            "payment_id": p["entity_id"], "receipt": p["order_receipt"], "amount": p["amount"],
            "created": p["created_at"], "method": p["method"]}))  # fmt: skip

    # --- Refunds --------------------------------------------------------------------------
    for r in _q(con, "SELECT * FROM refund_by_order WHERE rr <> rb ORDER BY order_no"):
        ids, rrows = r["ref_ids"] or [], r["ref_rows"] or []
        rows = {"orders": [r["row_no"]], "rp": rrows}
        facts = {"order_no": r["order_no"], "books_status": r["status"], "books_refund": r["rb"],
                 "razorpay_refunds": r["rr"], "refund_ids": ids, "refund_count": r["n_ref"]}  # fmt: skip
        if r["rr"] > r["rb"] and r["rb"] > 0 and r["n_ref"] >= 2:
            f.append(Finding("duplicate_refund", r["rr"] - r["rb"], [r["order_no"], *ids], rows, facts))  # fmt: skip
        elif r["rb"] == 0:
            f.append(Finding("refund_not_in_books", r["rr"], [r["order_no"], *ids], rows, facts))
        elif r["rr"] == 0 and not r["late"]:
            facts["books_refund_date"] = r["refund_date"]
            f.append(Finding("books_refund_not_processed", r["rb"], [r["order_no"]], rows, facts))
        elif r["rr"] != 0:
            f.append(Finding("refund_mismatch", r["rr"] - r["rb"], [r["order_no"], *ids], rows, facts))  # fmt: skip

    # --- Disputes and adjustments -----------------------------------------------------------
    for d in _q(
        con,
        """SELECT * FROM other WHERE type = 'dispute'
                        AND receipt IN (SELECT order_no FROM books) ORDER BY created_at""",
    ):
        f.append(Finding("chargeback", d["debit"] - d["credit"], [d["receipt"], d["entity_id"]],
            {"rp": [d["row_no"]]}, {"order_no": d["receipt"], "dispute_id": d["dispute_id"] or d["entity_id"],
            "payment_id": d["payment_id"], "amount": d["debit"] - d["credit"],
            "reason": d["description"], "debited_on": d["settled_at"]}))  # fmt: skip
    for a in _q(
        con,
        """SELECT * FROM rp WHERE type = 'adjustment' AND debit > 0
                        AND description IS NULL ORDER BY created_at""",
    ):
        f.append(Finding("unexplained_adjustment", a["debit"], [a["entity_id"]], {"rp": [a["row_no"]]},
            {"adjustment_id": a["entity_id"], "amount": a["debit"], "settlement_id": a["settlement_id"],
             "date": a["settled_at"]}))  # fmt: skip

    # --- Fees -------------------------------------------------------------------------------
    fee = _q(
        con,
        f"""
        SELECT *, exp_base + (exp_base * {pr.gst_pct} + 50) // 100 AS exp_fee,
               ((fee - tax) * {pr.gst_pct} + 50) // 100 AS exp_tax
        FROM pay""",
    )
    over = [p for p in fee if p["fee"] - p["exp_fee"] > pr.fee_tolerance]
    if over:
        f.append(Finding("fee_overcharge", sum(p["fee"] - p["exp_fee"] for p in over),
            [p["entity_id"] for p in over], {"rp": [p["row_no"] for p in over]}, {
            "payments": len(over), "agreed_rate_pct": pr.default_bp / 100,
            "charged_rate_pct": round(100 * sum(p["fee"] - p["tax"] for p in over) / sum(p["amount"] for p in over), 2),
            "first_day": min(p["created_at"] for p in over).date(),
            "last_day": max(p["created_at"] for p in over).date(),
            "methods": sorted({p["method"] for p in over})}))  # fmt: skip
    gst = [p for p in fee if abs(p["tax"] - p["exp_tax"]) > pr.fee_tolerance]
    if gst:
        f.append(Finding("gst_mismatch", sum(p["tax"] - p["exp_tax"] for p in gst),
            [p["entity_id"] for p in gst], {"rp": [p["row_no"] for p in gst]},
            {"payments": len(gst), "gst_pct": pr.gst_pct}))  # fmt: skip

    # --- Settlements vs bank ------------------------------------------------------------------
    sb = _q(con, "SELECT * FROM settlement_bank ORDER BY settled_at")
    for s in sb:
        rows = {"rp": s["rp_rows"], "bank": s["bank_rows"] or []}
        facts = {"settlement_id": s["settlement_id"], "utr": s["utr"], "settled_on": s["settled_at"],
                 "net_settled": s["net"], "bank_received": s["received"], "items": s["n_items"]}  # fmt: skip
        if s["n_credits"] == 0 and not s["in_transit"]:
            f.append(Finding("settlement_missing", s["net"], [s["settlement_id"], s["utr"]], rows, facts))  # fmt: skip
        elif s["n_credits"] and s["received"] != s["net"]:
            f.append(Finding("settlement_short", s["net"] - s["received"], [s["settlement_id"], s["utr"]], rows, facts))  # fmt: skip

    order = list(KINDS)
    f.sort(key=lambda x: (order.index(x.check), -x.amount))
    resolved = []  # findings a person marked as expected: out of the list, still in the bridge
    for x in list(f):
        rule = next((r for r in rules or [] if r.matches(x)), None)
        if rule:
            f.remove(x)
            resolved.append((x, rule))
    for i, x in enumerate(f, 1):
        x.id = f"F{i}"
    for i, (x, _) in enumerate(resolved, 1):
        x.id = f"R{i}"

    result = Result(period, f, *_bridges(con, f, pr, [x for x, _ in resolved]), resolved=resolved)
    result.totals = _totals(con, result)
    result.handled = _handled(con, pr, sb, resolved)
    _persist(con, f)
    return result


def _persist(con, findings: list[Finding]) -> None:
    """Findings as a table, so questions can be answered with SQL next to the source rows."""
    con.execute("""CREATE OR REPLACE TABLE findings (id VARCHAR, check_name VARCHAR,
        kind VARCHAR, title VARCHAR, amount_paise BIGINT, refs VARCHAR[], facts JSON)""")
    rows = [(x.id, x.check, x.kind, x.title, x.amount, x.refs, json.dumps(x.facts, default=str))
            for x in findings]  # fmt: skip
    if rows:
        con.executemany("INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _sum(con, sql: str) -> tuple[int, int]:
    total, n = con.execute(f"SELECT COALESCE(sum(x), 0), count(*) FROM ({sql})").fetchone()
    return int(total), int(n)


def _bridges(
    con, f: list[Finding], pr: Pricing, resolved: list[Finding]
) -> tuple[list[Step], list[Step]]:
    by = {}
    for x in f:
        by.setdefault(x.check, [0, 0])
        by[x.check][0] += x.amount
        by[x.check][1] += 1

    books, n_books = _sum(con, "SELECT amount - refund_amount AS x FROM books")
    rp_net, _ = _sum(con, "SELECT credit - debit AS x FROM rp")

    # Each entry is a signed contribution to (books - razorpay), so the steps going down the
    # waterfall are their negatives.
    exp_fee, n_pay = _sum(
        con, f"SELECT exp_base + (exp_base * {pr.gst_pct} + 50) // 100 AS x FROM pay"
    )
    all_fee, _ = _sum(con, "SELECT fee AS x FROM pay")
    over = by.get("fee_overcharge", [0, 0])[0]
    carry_out, n_out = _sum(con, "SELECT amount AS x FROM order_pay WHERE n_pay = 0 AND late")
    carry_in, n_in = _sum(con, "SELECT amount AS x FROM pay_class WHERE bucket = 'carry_in'")
    late_ref, n_lr = _sum(
        con, "SELECT -rb AS x FROM refund_by_order WHERE rr = 0 AND rb > 0 AND late"
    )
    old_ref, n_or = _sum(
        con,
        """SELECT amount AS x FROM other WHERE type = 'refund'
                                 AND (receipt IS NULL OR receipt NOT IN (SELECT order_no FROM books))""",
    )
    old_disp, n_od = _sum(
        con,
        """SELECT debit - credit AS x FROM other WHERE type = 'dispute'
                                  AND (receipt IS NULL OR receipt NOT IN (SELECT order_no FROM books))""",
    )
    adj_other, n_adj = _sum(
        con,
        """SELECT debit - credit AS x FROM rp WHERE type NOT IN
                               ('payment', 'refund', 'dispute') AND NOT (type = 'adjustment'
                               AND debit > 0 AND description IS NULL)""",
    )

    def fstep(check):
        amt, n = by.get(check, [0, 0])
        return Step(KINDS[check][1], amt if check in UP else -amt, "finding", n)

    def rstep(checks):
        mine = [x for x in resolved if x.check in checks]
        amt = sum(x.amount if x.check in UP else -x.amount for x in mine)
        return Step("Resolved by your rules", amt, "resolved", len(mine))

    bank_checks = {"settlement_missing", "settlement_short"}

    contrib = [
        Step("Razorpay fees + GST at your agreed rate", -exp_fee, "normal", n_pay),
        Step("Fee rounding (≤ 2 paise each)", -(all_fee - exp_fee - over), "normal"),
        fstep("fee_overcharge"),
        Step("Orders from the last days, settle next month", -carry_out, "timing", n_out),
        Step("Last month's payments settled this month", carry_in, "timing", n_in),
        Step("Refunds in the books, go out next month", -late_ref, "timing", n_lr),
        Step(
            "Refunds/disputes on last month's orders", -(old_ref + old_disp), "timing", n_or + n_od
        ),
        Step("Other Razorpay adjustments and transfers", -adj_other, "normal", n_adj),
        fstep("paid_order_no_payment"),
        fstep("amount_mismatch"),
        fstep("duplicate_payment"),
        fstep("ghost_success"),
        fstep("orphan_payment"),
        fstep("duplicate_refund"),
        fstep("refund_not_in_books"),
        fstep("refund_mismatch"),
        fstep("books_refund_not_processed"),
        fstep("chargeback"),
        fstep("unexplained_adjustment"),
        rstep(set(KINDS) - bank_checks - {"gst_mismatch"}),  # GST errors sit inside the fee lines
    ]
    explained = sum(s.amount for s in contrib)
    a = [Step("Net sales in your books (Razorpay orders, after refunds)", books, "start", n_books)]
    a += [s for s in contrib if s.amount or s.kind == "finding" and s.count]
    a += [Step("Unexplained", rp_net - books - explained, "residual"),
          Step("Net settled by Razorpay", rp_net, "end")]  # fmt: skip

    received, _ = _sum(con, "SELECT received AS x FROM settlement_bank")
    transit, n_tr = _sum(
        con, "SELECT net AS x FROM settlement_bank WHERE n_credits = 0 AND in_transit"
    )
    b = [Step("Net settled by Razorpay", rp_net, "start")]
    b += [s for s in [
        fstep("settlement_missing"),
        fstep("settlement_short"),
        Step("Settled on the last day, reaches the bank next month", -transit, "timing", n_tr),
        rstep(bank_checks),
    ] if s.amount]  # fmt: skip
    miss = sum(s.amount for s in b[1:])
    b += [Step("Unexplained", received - rp_net - miss, "residual"),
          Step("Received in your bank", received, "end")]  # fmt: skip
    return a, b


def _totals(con, r: Result) -> dict:
    """auto_reconciled: rows across the three files that no finding cites as evidence and that
    aren't unmatched Razorpay bank credits, out of all rows."""
    t = {k: sum(x.amount for x in r.findings if x.kind == k) for k in ("loss", "books", "customer")}
    t["findings"] = len(r.findings)
    t["unexplained"] = sum(
        s.amount for s in r.books_to_razorpay + r.razorpay_to_bank if s.kind == "residual"
    )
    unknown = _q(
        con,
        """SELECT row_no, txn_date, narration, credit FROM bank
        WHERE credit > 0 AND upper(narration) LIKE '%RAZORPAY%'
          AND row_no NOT IN (SELECT unnest(bank_rows) FROM settlement_bank WHERE bank_rows IS NOT NULL)""",
    )
    t["unmatched_razorpay_credits"] = unknown
    counts = con.execute("""SELECT (SELECT count(*) FROM rp), (SELECT count(*) FROM orders),
                                   (SELECT count(*) FROM bank), (SELECT count(*) FROM settlement)""").fetchone()
    t["rows"] = dict(zip(["razorpay", "orders", "bank", "settlements"], counts))
    open_rows = {(src, n) for x in r.findings for src, rows in x.rows.items() for n in rows}
    open_rows |= {("bank", u["row_no"]) for u in unknown}
    total = counts[0] + counts[1] + counts[2]
    done = total - len(open_rows)
    t["auto_reconciled"] = {"rows": done, "of": total, "pct": round(100 * done / total, 1) if total else 0.0}  # fmt: skip
    return t


def _handled(con, pr: Pricing, sb: list[dict], resolved: list[tuple[Finding, Rule]]) -> list[dict]:
    """Things that look like problems in a naive match but aren't. Shown so the user trusts it."""
    one = lambda sql: con.execute(sql).fetchone()[0]
    items = [
        ("Partial refunds that match the books",
         one("SELECT count(*) FROM refund_by_order WHERE rr = rb AND rb > 0 AND rb < (SELECT amount FROM books WHERE order_no = refund_by_order.order_no)")),
        ("Amex / Diners payments checked at the premium rate",
         one(f"SELECT count(*) FROM pay WHERE card_network IN ({', '.join(repr(n) for n in pr.premium_networks)})")),
        ("Settlements that arrived as more than one bank credit",
         sum(1 for s in sb if s["n_credits"] > 1)),
        ("Cash-on-delivery and cancelled orders left out",
         one("SELECT count(*) FROM orders WHERE payment_method <> 'razorpay' OR status = 'cancelled'")),
        ("Unpaid checkouts (pending / failed) with no payment, not sales",
         one(f"SELECT count(*) FROM orders WHERE status IN {UNPAID} AND order_no NOT IN (SELECT order_receipt FROM pay WHERE order_receipt IS NOT NULL)")),
        ("Bank lines that aren't Razorpay (rent, salaries, other channels)",
         one("SELECT count(*) FROM bank WHERE upper(narration) NOT LIKE '%RAZORPAY%'")),
    ]  # fmt: skip
    notes: dict[str, int] = {}
    for _, rule in resolved:
        notes[rule.note] = notes.get(rule.note, 0) + 1
    items += [(f"Resolved by your rule: {note}", n) for note, n in notes.items()]
    return [{"label": label, "count": int(n)} for label, n in items if n]
