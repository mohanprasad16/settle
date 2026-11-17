"""The reconciliation engine. Deterministic SQL over the loaded tables, no model involved.

Every payment, order, refund, dispute, adjustment and settlement lands in exactly one bucket.
Each bucket is either a finding (a real problem) or a handled item (fees, timing, partial
refunds...). The two bridges add the buckets up; if anything was missed, the "unexplained"
line is not zero, and the tests fail.
"""

import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from itertools import combinations

import duckdb

from app.load import Period
from app.rules import Rule

KINDS = {
    "settlement_missing": ("loss", "Settlement never reached your bank"),
    "settlement_short": ("loss", "Bank credited less than the settlement"),
    "settlement_over": (
        "books",
        "Bank credited more than the settlement (possible double posting)",
    ),
    "paid_order_no_payment": ("loss", "Order marked paid, but no payment at Razorpay"),
    "duplicate_refund": ("loss", "Refund paid out twice"),
    "chargeback": ("loss", "Chargeback debited, order still shows paid"),
    "fee_overcharge": ("loss", "Fees above your agreed rate"),
    "rate_misclassified": ("loss", "Debit cards charged at the credit-card rate"),
    "gst_mismatch": ("loss", "GST on fees isn't 18%"),
    "unexplained_adjustment": ("loss", "Adjustment debited with no reason"),
    "duplicate_payment": ("customer", "Customer charged twice for one order"),
    "books_refund_not_processed": ("customer", "Books say refunded, no refund went out"),
    "refund_mismatch": ("books", "Refund at Razorpay differs from the books"),
    "ghost_success": ("books", "Paid at Razorpay, order still pending in your store"),
    "orphan_payment": ("books", "Payment with no order in your books"),
    "refund_not_in_books": ("books", "Refunded at Razorpay, books still say paid"),
    "amount_mismatch": ("books", "Amount captured differs from the order"),
    # Patterns, not missing money: they never enter the bridge.
    "refund_spike": ("risk", "Unusual spike in refunds"),
    "repeat_refunder": ("risk", "Same customer refunded again and again"),
    "method_refund_rate": ("risk", "One payment method refunded far more than the rest"),
}
# Findings that make Razorpay higher than the books (step goes up the waterfall, not down).
UP = {
    "duplicate_payment",
    "orphan_payment",
    "ghost_success",
    "books_refund_not_processed",
    "settlement_over",
}
UNPAID = "('pending', 'failed', 'created')"  # store statuses for checkouts that aren't sales


SPIKE_K = 6  # a refund day is a spike above median + 6 robust deviations: MAD x 1.4826, but
# never below sqrt(median), since a day's refund count is roughly Poisson and that is its noise.
SPIKE_X = 5  # ...or at least 5x a normal day (and 20 refunds): a refund wave can hide a spike
SPIKE_MIN_DAYS = 14  # too few days and there's no baseline to speak of
REPEAT_REFUNDS = 3  # one customer refunded on this many different orders in the period
METHOD_RATE_X = 3  # a method refunded 3x the other methods' median rate...
METHOD_MIN = (10, 30)  # ...with at least 10 refunds out of 30 payments


@dataclass
class RateCard:
    """The merchant's contracted MDR in basis points, per method and card type.

    In India UPI and RuPay debit carry zero MDR by regulation for most merchants; Razorpay's
    published standard plan charges a flat % on most other methods, and bigger merchants
    negotiate per method like this. The defaults are Kaapi Kart's contract.
    """

    upi: int = 0
    rupay_debit: int = 0
    debit: int = 90
    credit: int = 195
    netbanking: int = 190
    wallet: int = 190
    premium: int = 295  # Amex / Diners / international
    premium_networks: tuple[str, ...] = ("American Express", "Diners Club")
    gst_pct: int = 18
    fee_tolerance: int = 2  # paise per payment: rounding, not overcharging
    settlement_lag_days: int = 2

    def bp(self, method: str, card_type: str | None, network: str | None) -> int:
        """The contract rate for one payment. sql() below is the same rule for the engine."""
        if network in self.premium_networks:
            return self.premium
        if method == "card":
            if card_type == "debit":
                return self.rupay_debit if network == "RuPay" else self.debit
            return self.credit
        return {"upi": self.upi, "netbanking": self.netbanking, "wallet": self.wallet}.get(
            method, self.credit
        )

    def sql(self) -> str:
        premium = ", ".join("'" + n.replace("'", "''") + "'" for n in self.premium_networks)
        return f"""CASE WHEN card_network IN ({premium}) THEN {self.premium}
            WHEN method = 'card' AND card_type = 'debit' AND card_network = 'RuPay' THEN {self.rupay_debit}
            WHEN method = 'card' AND card_type = 'debit' THEN {self.debit}
            WHEN method = 'card' THEN {self.credit}
            WHEN method = 'upi' THEN {self.upi}
            WHEN method = 'netbanking' THEN {self.netbanking}
            WHEN method = 'wallet' THEN {self.wallet}
            ELSE {self.credit} END"""


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
    exceptions: list[dict] = field(default_factory=list)  # what it couldn't resolve, and why


def _q(con, sql: str, params=None) -> list[dict]:
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _views(con, p: Period, pr: RateCard) -> None:
    lag_cutoff = p.end - timedelta(days=pr.settlement_lag_days)
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW books AS
        SELECT * FROM orders
        WHERE payment_method = 'razorpay' AND status NOT IN ('cancelled', {UNPAID[1:-1]})
          AND order_date BETWEEN DATE '{p.start}' AND DATE '{p.end}';

        CREATE OR REPLACE TEMP VIEW pay AS
        SELECT *, CAST(created_at AS DATE) AS created_date,
               (amount * ({pr.sql()}) + 5000) // 10000 AS exp_base,
               (amount * {pr.credit} + 5000) // 10000 AS credit_base
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
        """)


def _norm(text: str | None) -> str:
    return re.sub(r"\s", "", (text or "").upper())


def _match_bank(con, p: Period) -> None:
    """Bank credits for each settlement, into table settlement_bank. By UTR in the ref or the
    narration first. Banks that don't print the UTR: an exact-amount Razorpay credit within 2 days
    of settling, then 2-3 credits on the day or the next that add up exactly (a split). Anything
    that fits more than one way is left unmatched with the reason, for a person to decide."""
    sets = _q(con, "SELECT * FROM settlement ORDER BY settled_at, settlement_id")
    bank = _q(con, "SELECT row_no, txn_date, narration, ref_no, credit - debit AS amt FROM bank")
    used: set[int] = set()

    def take(s, lines, how):
        s.update(bank_rows=[b["row_no"] for b in lines], received=sum(b["amt"] for b in lines),
                 matched_by=how)  # fmt: skip
        used.update(b["row_no"] for b in lines)

    for s in sets:
        s.update(bank_rows=[], received=0, matched_by=None, unmatched_reason=None,
                 in_transit=s["settled_at"].date() >= p.end)  # fmt: skip
        utr = _norm(s["utr"])
        hits = [b for b in bank if utr and (utr in _norm(b["ref_no"]) or utr in _norm(b["narration"]))]  # fmt: skip
        if hits:
            take(s, hits, "utr")

    def pool(s, days):
        d = s["settled_at"].date()
        return [b for b in bank if b["row_no"] not in used and b["amt"] and "RAZORPAY"
                in _norm(b["narration"]) and d <= b["txn_date"] <= d + timedelta(days)]  # fmt: skip

    open_ = [s for s in sets if not s["matched_by"]]
    cand = {s["settlement_id"]: [b for b in pool(s, 2) if b["amt"] == s["net"]] for s in open_}
    claims = Counter(b["row_no"] for c in cand.values() for b in c)
    for s in open_:
        c = cand[s["settlement_id"]]
        if len(c) == 1 and claims[c[0]["row_no"]] == 1:
            take(s, c, "amount_date")
        elif c:
            s["unmatched_reason"] = (
                f"{len(c)} bank credits of this exact amount within 2 days"
                if len(c) > 1 else "the only bank credit of this amount also fits another settlement"
            )  # fmt: skip
    for s in open_:
        if s["matched_by"] or s["unmatched_reason"]:
            continue
        c = pool(s, 1)
        if len(c) > 12:  # ponytail: brute force over 2-3 credits; bounded so it stays instant
            continue
        fits = [x for k in (2, 3) for x in combinations(c, k) if sum(b["amt"] for b in x) == s["net"]]  # fmt: skip
        if len(fits) == 1:
            take(s, fits[0], "split")
        elif fits:
            s["unmatched_reason"] = f"{len(fits)} different groups of bank credits add up to it"

    con.execute("""CREATE OR REPLACE TABLE settlement_bank (settlement_id VARCHAR, utr VARCHAR,
        settled_at TIMESTAMP, net BIGINT, n_items BIGINT, rp_rows BIGINT[], received BIGINT,
        n_credits BIGINT, bank_rows BIGINT[], in_transit BOOLEAN, matched_by VARCHAR,
        unmatched_reason VARCHAR)""")
    rows = [(s["settlement_id"], s["utr"], s["settled_at"], s["net"], s["n_items"], s["rp_rows"],
             s["received"], len(s["bank_rows"]), s["bank_rows"], s["in_transit"], s["matched_by"],
             s["unmatched_reason"]) for s in sets]  # fmt: skip
    if rows:
        con.executemany("INSERT INTO settlement_bank VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)  # fmt: skip


def run(
    con: duckdb.DuckDBPyConnection,
    period: Period,
    rates: RateCard | None = None,
    rules: list[Rule] | None = None,
) -> Result:
    pr = rates or RateCard()
    _views(con, period, pr)
    _match_bank(con, period)
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
    over, misc = [], []  # wrong rate for the card type / card charged as the wrong type
    for p in fee:
        if p["fee"] - p["exp_fee"] > pr.fee_tolerance:
            debit_as_credit = p["card_type"] == "debit" and abs(p["fee"] - p["tax"] - p["credit_base"]) <= pr.fee_tolerance  # fmt: skip
            (misc if debit_as_credit else over).append(p)

    def rate(ps, key):
        return round(100 * sum(p[key] for p in ps) / sum(p["amount"] for p in ps), 2)

    for check, ps in (("fee_overcharge", over), ("rate_misclassified", misc)):
        if not ps:
            continue
        for p in ps:
            p["charged"] = p["fee"] - p["tax"]
        facts = {"payments": len(ps), "charged_rate_pct": rate(ps, "charged"),
                 "first_day": min(p["created_at"] for p in ps).date(),
                 "last_day": max(p["created_at"] for p in ps).date()}  # fmt: skip
        if check == "fee_overcharge":
            facts |= {"agreed_rate_pct": rate(ps, "exp_base"), "methods": sorted({p["method"] for p in ps})}  # fmt: skip
        else:
            facts |= {"card_type": "debit", "contract_rate_pct": rate(ps, "exp_base"),
                      "networks": sorted({p["card_network"] for p in ps if p["card_network"]})}  # fmt: skip
        f.append(Finding(check, sum(p["fee"] - p["exp_fee"] for p in ps),
            [p["entity_id"] for p in ps], {"rp": [p["row_no"] for p in ps]}, facts))  # fmt: skip
    gst = [p for p in fee if abs(p["tax"] - p["exp_tax"]) > pr.fee_tolerance]
    if gst:
        f.append(Finding("gst_mismatch", sum(p["tax"] - p["exp_tax"] for p in gst),
            [p["entity_id"] for p in gst], {"rp": [p["row_no"] for p in gst]},
            {"payments": len(gst), "gst_pct": pr.gst_pct}))  # fmt: skip

    # --- Settlements vs bank ------------------------------------------------------------------
    sb = _q(con, "SELECT * FROM settlement_bank ORDER BY settled_at")
    exceptions = [{"settlement_id": s["settlement_id"], "utr": s["utr"], "amount": s["net"],
                   "settled_on": s["settled_at"], "why": s["unmatched_reason"], "rows": {"rp": s["rp_rows"]}}
                  for s in sb if s["unmatched_reason"]]  # fmt: skip
    for s in sb:
        rows = {"rp": s["rp_rows"], "bank": s["bank_rows"] or []}
        facts = {"settlement_id": s["settlement_id"], "utr": s["utr"], "settled_on": s["settled_at"],
                 "net_settled": s["net"], "bank_received": s["received"], "items": s["n_items"]}  # fmt: skip
        if s["n_credits"] == 0 and not s["in_transit"] and not s["unmatched_reason"]:
            f.append(Finding("settlement_missing", s["net"], [s["settlement_id"], s["utr"]], rows, facts))  # fmt: skip
        elif s["n_credits"] and s["received"] < s["net"]:
            f.append(Finding("settlement_short", s["net"] - s["received"], [s["settlement_id"], s["utr"]], rows, facts))  # fmt: skip
        elif s["n_credits"] and s["received"] > s["net"]:
            f.append(Finding("settlement_over", s["received"] - s["net"], [s["settlement_id"], s["utr"]], rows, facts))  # fmt: skip

    f += _risk(con, period)
    f = [x for x in f if x.amount]  # a ₹0 difference is not a problem (mandates, authorisations)

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

    result = Result(period, f, *_bridges(con, f, pr, [x for x, _ in resolved]), resolved=resolved,
                    exceptions=exceptions)  # fmt: skip
    result.totals = _totals(con, result)
    result.handled = _handled(con, pr, sb, resolved)
    _persist(con, f)
    return result


def _risk(con, p: Period) -> list[Finding]:
    """Refund patterns worth a look. The refunds themselves match the books, so these flags
    never move the bridge; they say "this looks wrong", not "this money is missing"."""
    out = []
    refunds = f"""SELECT r.*, CAST(r.created_at AS DATE) AS day, o.customer, o.order_no, o.row_no AS order_row
        FROM other r LEFT JOIN books o ON o.order_no = r.receipt
        WHERE r.type = 'refund' AND CAST(r.created_at AS DATE) BETWEEN DATE '{p.start}' AND DATE '{p.end}'"""
    rs = _q(con, refunds)

    # Methods refunded far more than the rest first: their wave is its own finding, and the daily
    # spike check then looks at the other methods' refunds, so the wave can't hide a spike.
    waves = set()
    rates = _q(
        con,
        f"""SELECT p.method, count(*) AS payments,
            count(*) FILTER (WHERE p.entity_id IN (SELECT payment_id FROM rp WHERE type = 'refund')) AS refunded
        FROM pay p WHERE created_date BETWEEN DATE '{p.start}' AND DATE '{p.end}' GROUP BY p.method""",
    )
    for m in rates:
        others = [x["refunded"] / x["payments"] for x in rates if x is not m and x["payments"]]
        if not others or m["refunded"] < METHOD_MIN[0] or m["payments"] < METHOD_MIN[1]:
            continue
        rate, base = m["refunded"] / m["payments"], statistics.median(others)
        if rate > METHOD_RATE_X * base:
            waves.add(m["method"])
            mine = [r for r in rs if r["method"] == m["method"]]
            out.append(Finding("method_refund_rate", sum(r["amount"] for r in mine), [m["method"]],
                {"rp": [r["row_no"] for r in mine]}, {"method": m["method"], "refunds": m["refunded"],
                 "payments": m["payments"], "refund_rate_pct": round(100 * rate, 1),
                 "others_rate_pct": round(100 * base, 1), "total_refunded": sum(r["amount"] for r in mine)}))  # fmt: skip

    days = (p.end - p.start).days + 1
    if days >= SPIKE_MIN_DAYS:
        by_day = Counter(r["day"] for r in rs if r["method"] not in waves)
        counts = [by_day.get(p.start + timedelta(i), 0) for i in range(days)]
        med = statistics.median(counts)
        mad = statistics.median(abs(c - med) for c in counts)
        limit = med + SPIKE_K * max(1.4826 * mad, med**0.5, 1)
        for d, n in sorted(by_day.items()):
            if n > limit or n >= max(SPIKE_X * med, 20):
                mine = [r for r in rs if r["day"] == d]
                out.append(Finding("refund_spike", sum(r["amount"] for r in mine), [d.isoformat(), *[r["entity_id"] for r in mine]],
                    {"rp": [r["row_no"] for r in mine]}, {"date": d, "refunds": len(mine), "usual_per_day": med if med % 1 else int(med),
                     "total_refunded": sum(r["amount"] for r in mine)}))  # fmt: skip

    by_cust: dict[str, list[dict]] = {}
    for r in rs:
        if r["customer"]:
            by_cust.setdefault(r["customer"], []).append(r)
    for cust, mine in sorted(by_cust.items()):
        orders = sorted({r["order_no"] for r in mine})
        if len(orders) >= REPEAT_REFUNDS:
            out.append(Finding("repeat_refunder", sum(r["amount"] for r in mine), [cust, *orders],
                {"rp": [r["row_no"] for r in mine], "orders": sorted({r["order_row"] for r in mine})},
                {"customer": cust, "refunds": len(orders), "orders": orders,
                 "total_refunded": sum(r["amount"] for r in mine)}))  # fmt: skip

    return out


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
    con, f: list[Finding], pr: RateCard, resolved: list[Finding]
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
    over = by.get("fee_overcharge", [0, 0])[0] + by.get("rate_misclassified", [0, 0])[0]
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

    bank_checks = {"settlement_missing", "settlement_short", "settlement_over"}

    contrib = [
        Step("Razorpay fees + GST at your agreed rate", -exp_fee, "normal", n_pay),
        Step("Fee rounding (≤ 2 paise each)", -(all_fee - exp_fee - over), "normal"),
        fstep("fee_overcharge"),
        fstep("rate_misclassified"),
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
        rstep(
            {k for k, v in KINDS.items() if v[0] != "risk"} - bank_checks - {"gst_mismatch"}
        ),  # GST errors sit inside the fee lines
    ]
    explained = sum(s.amount for s in contrib)
    a = [Step("Net sales in your books (Razorpay orders, after refunds)", books, "start", n_books)]
    a += [s for s in contrib if s.amount or s.kind == "finding" and s.count]
    a += [Step("Unexplained", rp_net - books - explained, "residual"),
          Step("Net settled by Razorpay", rp_net, "end")]  # fmt: skip

    received, _ = _sum(con, "SELECT received AS x FROM settlement_bank")
    transit, n_tr = _sum(
        con,
        """SELECT net AS x FROM settlement_bank
        WHERE n_credits = 0 AND in_transit AND unmatched_reason IS NULL""",
    )
    unsure, n_un = _sum(
        con, "SELECT net AS x FROM settlement_bank WHERE unmatched_reason IS NOT NULL"
    )
    b = [Step("Net settled by Razorpay", rp_net, "start")]
    b += [s for s in [
        fstep("settlement_missing"),
        fstep("settlement_short"),
        fstep("settlement_over"),
        Step("Settled on the last day, reaches the bank next month", -transit, "timing", n_tr),
        Step("Couldn't match automatically (see exceptions)", -unsure, "exception", n_un),
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
    t["risk"] = sum(1 for x in r.findings if x.kind == "risk")
    t["exceptions"] = len(r.exceptions)
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
    flagged = [x.rows for x in r.findings if x.kind != "risk"] + [e["rows"] for e in r.exceptions]
    open_rows = {(src, n) for rows_by in flagged for src, rows in rows_by.items() for n in rows}
    open_rows |= {("bank", u["row_no"]) for u in unknown}
    total = counts[0] + counts[1] + counts[2]
    done = total - len(open_rows)
    t["auto_reconciled"] = {"rows": done, "of": total, "pct": round(100 * done / total, 1) if total else 0.0}  # fmt: skip
    return t


def _handled(con, pr: RateCard, sb: list[dict], resolved: list[tuple[Finding, Rule]]) -> list[dict]:
    """Things that look like problems in a naive match but aren't. Shown so the user trusts it."""
    one = lambda sql: con.execute(sql).fetchone()[0]
    items = [
        ("Partial refunds that match the books",
         one("SELECT count(*) FROM refund_by_order WHERE rr = rb AND rb > 0 AND rb < (SELECT amount FROM books WHERE order_no = refund_by_order.order_no)")),
        ("Amex / Diners payments checked at the premium rate",
         one(f"SELECT count(*) FROM pay WHERE card_network IN ({', '.join(repr(n) for n in pr.premium_networks)})")),
        ("Zero-amount payments (card mandates / authorisations)",
         one("SELECT count(*) FROM pay WHERE amount = 0")),
        ("UPI and RuPay debit payments at zero fee, as contracted",
         one("SELECT count(*) FROM pay WHERE exp_base = 0 AND fee = 0")),
        ("Settlements that arrived as more than one bank credit",
         sum(1 for s in sb if s["n_credits"] > 1 and s["matched_by"] == "utr")),
        ("Settlements matched by amount and date (no UTR on the bank line)",
         sum(1 for s in sb if s["matched_by"] == "amount_date")),
        ("Settlements matched as a split of credits with no UTR",
         sum(1 for s in sb if s["matched_by"] == "split")),
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
