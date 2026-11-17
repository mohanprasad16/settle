"""Builds a month of realistic data for a fictional D2C coffee brand, with problems planted on
purpose and an answer key (truth.json) listing each one and its exact rupee impact.

    uv run python -m app.sample            # writes data/sample/*.csv + truth.json
    uv run python -m app.sample 42 /tmp/x  # a random month (what "Inject chaos" serves)

The Razorpay file uses the columns of Razorpay's settlement recon API (amounts in paise). Every
"normal" thing a real month has is in here too, so the engine has to tell them apart from the
problems: partial refunds, Amex at 3%, a settlement split into two bank credits, month-end
orders that settle in November, September payments settled in October, COD orders, rent and
salary lines in the bank, last month's settlement credited on 1 Oct, abandoned checkouts.

Seed 7 is the curated demo month. Any other seed draws a random Plan: which problems, how many
(0-3 each), on which days, for how much. The engine must find exactly the planted set every time.
"""

import csv
import json
import random
import string
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
PERIOD_START, PERIOD_END = date(2026, 10, 1), date(2026, 10, 31)
FIRST_ORDER_DAY = date(2026, 9, 28)  # September orders settle in October: carry-in
LAG_DAYS = 2  # T+2 settlement
RATE_BP = {"default": 200, "premium": 300}  # 2% domestic, 3% Amex / Diners
PREMIUM = ("American Express", "Diners Club")
OVERCHARGE_BP = 240
PRICES = [34900, 49900, 64900, 89900, 119900, 149900, 249900]
DEMO_SEED = 7
FILES = {
    "rp": "razorpay_settlements.csv",
    "bank": "bank_statement.csv",
    "orders": "orders.csv",
}

RP_COLUMNS = [
    "entity_id", "type", "debit", "credit", "amount", "currency", "fee", "tax", "on_hold",
    "settled", "created_at", "settled_at", "settlement_id", "posted_at", "credit_type",
    "description", "notes", "payment_id", "settlement_utr", "order_id", "order_receipt",
    "method", "card_network", "card_issuer", "card_type", "dispute_id",
]  # fmt: skip
BANK_COLUMNS = ["txn_date", "narration", "ref_no", "debit", "credit", "balance"]
ORDER_COLUMNS = [
    "order_no", "order_date", "customer", "amount", "payment_method", "status",
    "refund_amount", "refund_date",
]  # fmt: skip

FIRST = [
    "Aarav",
    "Diya",
    "Ishaan",
    "Meera",
    "Kabir",
    "Ananya",
    "Rohan",
    "Saanvi",
    "Vihaan",
    "Nila",
    "Arjun",
    "Kavya",
    "Dev",
    "Tara",
]
LAST = [
    "Iyer",
    "Sharma",
    "Reddy",
    "Nair",
    "Menon",
    "Gupta",
    "Rao",
    "Das",
    "Pillai",
    "Bose",
    "Kulkarni",
    "Shah",
    "Verma",
    "Joshi",
]


@dataclass
class Plan:
    """What gets planted. Days are October days; amounts are paise."""

    no_payment: int = 3
    short_capture: int = 2
    short_by: int = 10000
    glitch: tuple[int, int] | None = (16, 18)  # card fees at 2.4% on these days
    dup_refund: int = 1
    refund_not_in_books: int = 3
    books_refund_np: int = 2
    chargeback: int = 2
    orphans: list[tuple[int, int]] = field(default_factory=lambda: [(11, 129900), (18, 129900)])
    adjustments: list[tuple[int, int]] = field(default_factory=lambda: [(25, 118000)])
    missing_days: list[int] = field(default_factory=lambda: [14])  # settlement days
    short_days: list[tuple[int, int]] = field(default_factory=lambda: [(21, 200000)])
    split_days: list[int] = field(default_factory=lambda: [9])
    ghost: int = 2  # paid at Razorpay, order still pending in the store
    abandoned: int = 4  # pending / failed checkouts with no payment: normal, not a problem


def plan_for(seed: int) -> Plan:
    if seed == DEMO_SEED:
        return Plan()
    rng = random.Random(seed * 7919 + 13)
    n = lambda: rng.randint(0, 3)
    k_miss, k_short, k_split = n(), n(), n()
    days = rng.sample(range(1, 31), k_miss + k_short + k_split)
    start = rng.randint(1, 27)
    return Plan(
        no_payment=n(),
        short_capture=n(),
        short_by=rng.choice([5000, 10000, 25000]),
        glitch=(start, start + rng.randint(0, 2)) if rng.random() < 0.6 else None,
        dup_refund=n(),
        refund_not_in_books=n(),
        books_refund_np=n(),
        chargeback=n(),
        orphans=[(rng.randint(1, 27), rng.choice(PRICES)) for _ in range(n())],
        adjustments=[(rng.randint(1, 27), rng.randint(5, 50) * 2360) for _ in range(n())],
        missing_days=days[:k_miss],
        short_days=[(d, rng.randint(1, 10) * 25000) for d in days[k_miss : k_miss + k_short]],
        split_days=days[k_miss + k_short :],
        ghost=n(),
        abandoned=n(),
    )


@dataclass
class Order:
    no: str
    day: date
    customer: str
    amount: int
    method: str  # razorpay | cod
    status: str = "paid"
    refund_amount: int = 0
    refund_day: date | None = None


@dataclass
class Item:  # one row of the Razorpay recon report
    row: dict
    created: date


@dataclass
class World:
    orders: list[Order] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    planted: list[dict] = field(default_factory=list)


def _id(rng: random.Random, prefix: str) -> str:
    return prefix + "".join(rng.choices(string.ascii_letters + string.digits, k=14))


def _epoch(d: date, hh: int, mm: int) -> int:
    return int(datetime.combine(d, time(hh, mm), IST).timestamp())


def _fee(amount: int, rate_bp: int) -> tuple[int, int]:
    base = (amount * rate_bp + 5000) // 10000
    tax = (base * 18 + 50) // 100
    return base + tax, tax


def _payment(rng, order: Order, created: date, *, amount=None) -> dict:
    amount = order.amount if amount is None else amount
    method = rng.choices(["upi", "card", "netbanking", "wallet"], [55, 30, 10, 5])[0]
    network = issuer = ctype = ""
    if method == "card":
        network = rng.choices(
            ["Visa", "MasterCard", "RuPay", "American Express", "Diners Club"], [45, 35, 12, 6, 2]
        )[0]
        issuer = rng.choice(["HDFC", "ICIC", "SBIN", "UTIB", "KKBK"])
        ctype = rng.choice(["credit", "debit"])
    fee, tax = _fee(amount, RATE_BP["premium" if network in PREMIUM else "default"])
    return {
        "entity_id": _id(rng, "pay_"), "type": "payment", "debit": 0, "credit": amount - fee,
        "amount": amount, "fee": fee, "tax": tax, "created_at": _epoch(created, 9 + rng.randrange(13), rng.randrange(60)),
        "order_id": _id(rng, "order_"), "order_receipt": order.no, "method": method,
        "card_network": network, "card_issuer": issuer, "card_type": ctype,
    }  # fmt: skip


def _refund(rng, pay: dict, amount: int, created: date) -> dict:
    return {
        "entity_id": _id(rng, "rfnd_"), "type": "refund", "debit": amount, "credit": 0,
        "amount": amount, "fee": 0, "tax": 0, "created_at": _epoch(created, 11, rng.randrange(60)),
        "payment_id": pay["entity_id"], "order_id": pay["order_id"],
        "order_receipt": pay["order_receipt"], "method": pay["method"],
    }  # fmt: skip


def build(seed: int = DEMO_SEED, plan: Plan | None = None) -> World:
    plan = plan or plan_for(seed)
    rng = random.Random(seed)
    w = World()
    payments: dict[str, dict] = {}  # order_no -> payment row
    n = 10231

    day = FIRST_ORDER_DAY
    while day <= PERIOD_END:
        for _ in range(rng.randint(55, 70)):
            amount = rng.choice(PRICES) * rng.choices([1, 2, 3], [70, 22, 8])[0]
            amount += 4900 if amount < 49900 else 0  # shipping on small orders
            o = Order(
                f"KK-{n}", day, f"{rng.choice(FIRST)} {rng.choice(LAST)}", amount,
                rng.choices(["razorpay", "cod"], [88, 12])[0],
            )  # fmt: skip
            n += 1
            if rng.random() < 0.02:
                o.status = "cancelled"
            w.orders.append(o)
        day += timedelta(days=1)

    live = [o for o in w.orders if o.method == "razorpay" and o.status != "cancelled"]
    in_period = [o for o in live if PERIOD_START <= o.day <= PERIOD_END - timedelta(7)]
    rng.shuffle(in_period)
    pick = iter(in_period)

    def plant(check, refs, amount, **facts):
        w.planted.append({"check": check, "refs": refs, "amount": amount, **facts})

    # Planted: order marked paid, payment never captured.
    no_payment = [next(pick) for _ in range(plan.no_payment)]
    for o in no_payment:
        plant("paid_order_no_payment", [o.no], o.amount)

    # Planted: captured ₹100 less than the order (coupon applied at checkout, not in books).
    short_capture = {next(pick).no for _ in range(plan.short_capture)}

    for o in live:
        if o in no_payment:
            continue
        amount = o.amount - plan.short_by if o.no in short_capture else o.amount
        pay = _payment(rng, o, o.day, amount=amount)
        glitch = plan.glitch and date(2026, 10, plan.glitch[0]) <= o.day <= date(2026, 10, plan.glitch[1])  # fmt: skip
        if glitch and pay["method"] == "card" and pay["card_network"] not in PREMIUM:
            fee, tax = _fee(amount, OVERCHARGE_BP)
            pay |= {"fee": fee, "tax": tax, "credit": amount - fee}
        payments[o.no] = pay
        w.items.append(Item(pay, o.day))
        if o.no in short_capture:
            plant("amount_mismatch", [o.no], o.amount - amount)

    overcharged = [
        p for p in payments.values()
        if p["method"] == "card" and p["card_network"] not in PREMIUM
        and p["fee"] != _fee(p["amount"], RATE_BP["default"])[0]
    ]  # fmt: skip
    if overcharged:
        plant(
            "fee_overcharge",
            sorted(p["entity_id"] for p in overcharged),
            sum(p["fee"] - _fee(p["amount"], RATE_BP["default"])[0] for p in overcharged),
        )

    # Normal refunds: ~4% full, ~2% partial, 1-3 days after the order.
    for o in live:
        if o.no not in payments or o.no in short_capture:
            continue
        r = rng.random()
        if r < 0.06:
            full = r < 0.04
            o.refund_amount = o.amount if full else o.amount // 2 // 100 * 100
            o.status = "refunded" if full else "partially_refunded"
            o.refund_day = o.day + timedelta(days=rng.randint(1, 3))
            w.items.append(
                Item(_refund(rng, payments[o.no], o.refund_amount, o.refund_day), o.refund_day)
            )

    clean = [
        o
        for o in in_period
        if o.status == "paid" and o.no in payments and o.no not in short_capture
    ]
    rng.shuffle(clean)
    clean = iter(clean)

    # Planted: refund paid twice at Razorpay, once in the books.
    for _ in range(plan.dup_refund):
        o = next(clean)
        o.status, o.refund_amount, o.refund_day = "refunded", o.amount, o.day + timedelta(days=1)
        first = _refund(rng, payments[o.no], o.amount, o.refund_day)
        second = _refund(rng, payments[o.no], o.amount, o.refund_day + timedelta(days=1))
        w.items += [Item(first, o.refund_day), Item(second, o.refund_day + timedelta(days=1))]
        plant("duplicate_refund", [o.no, second["entity_id"]], o.amount)

    # Planted: refunded at Razorpay, books still say paid.
    for _ in range(plan.refund_not_in_books):
        o = next(clean)
        ref = _refund(rng, payments[o.no], o.amount, o.day + timedelta(days=2))
        w.items.append(Item(ref, o.day + timedelta(days=2)))
        plant("refund_not_in_books", [o.no, ref["entity_id"]], o.amount)

    # Planted: books say refunded, nothing went out at Razorpay.
    for _ in range(plan.books_refund_np):
        o = next(clean)
        o.status, o.refund_amount, o.refund_day = "refunded", o.amount, o.day + timedelta(days=1)
        plant("books_refund_not_processed", [o.no], o.amount)

    # Planted: chargebacks debited, books still say paid.
    for _ in range(plan.chargeback):
        o = next(clean)
        pay = payments[o.no]
        d = o.day + timedelta(days=4)
        row = {
            "entity_id": _id(rng, "disp_"), "type": "dispute", "debit": pay["amount"], "credit": 0,
            "amount": pay["amount"], "fee": 0, "tax": 0, "created_at": _epoch(d, 15, 0),
            "payment_id": pay["entity_id"], "order_id": pay["order_id"],
            "order_receipt": o.no, "method": pay["method"], "dispute_id": _id(rng, "disp_"),
            "description": "Chargeback: item not received",
        }  # fmt: skip
        row["entity_id"] = row["dispute_id"]
        w.items.append(Item(row, d))
        plant("chargeback", [o.no, row["dispute_id"]], pay["amount"])

    # Planted: payments with no order in the books (a second storefront nobody exports).
    for i, (day, amount) in enumerate(plan.orphans):
        web = Order(f"KK-WEB-{900 + i}", date(2026, 10, day), "Guest", amount, "razorpay")
        pay = _payment(rng, web, web.day)
        w.items.append(Item(pay, web.day))
        plant("orphan_payment", [pay["entity_id"]], pay["amount"])

    # Planted: an adjustment debit with no reason given.
    for day, amount in plan.adjustments:
        d = date(2026, 10, day)
        adj = {
            "entity_id": _id(rng, "adj_"), "type": "adjustment", "debit": amount, "credit": 0,
            "amount": amount, "fee": 0, "tax": 0, "created_at": _epoch(d, 12, 0),
        }  # fmt: skip
        w.items.append(Item(adj, d))
        plant("unexplained_adjustment", [adj["entity_id"]], amount)

    # Last, on their own random stream so everything above is unchanged by them: orders that
    # were paid but the store never heard (ghost successes, planted), and checkouts that never
    # paid at all (abandoned, normal). Kept off the days the bank problems and fee glitch use.
    prng = random.Random(seed * 1009 + 3)
    used = {r for p in w.planted for r in p["refs"]}
    busy = {date(2026, 10, d) - timedelta(LAG_DAYS) for d in plan.missing_days + plan.split_days}
    busy |= {date(2026, 10, d) - timedelta(LAG_DAYS) for d, _ in plan.short_days}
    if plan.glitch:
        busy |= {date(2026, 10, d) for d in range(plan.glitch[0], plan.glitch[1] + 1)}
    cands = [
        o for o in in_period
        if o.status == "paid" and o.no in payments and o.no not in used
        and o.no not in short_capture and o.day not in busy
    ]  # fmt: skip
    chosen = prng.sample(cands, plan.ghost + plan.abandoned)
    for o in chosen[: plan.ghost]:
        o.status = "pending"
        plant("ghost_success", [o.no, payments[o.no]["entity_id"]], payments[o.no]["amount"])
    for i, o in enumerate(chosen[plan.ghost :]):
        o.status = "failed" if i == 0 else "pending"
        pay = payments.pop(o.no)
        w.items = [it for it in w.items if it.row is not pay]
    return w


def settle_items(rng, w: World) -> dict[date, tuple[str, str]]:
    """Group items into daily T+2 settlements. Returns settlement day -> (id, utr)."""
    batches: dict[date, tuple[str, str]] = {}
    for it in w.items:
        sday = it.created + timedelta(days=LAG_DAYS)
        if sday not in batches:
            batches[sday] = (
                _id(rng, "setl_"),
                "UTIBR5" + "".join(rng.choices(string.digits, k=10)),
            )
        sid, utr = batches[sday]
        late = sday == PERIOD_END
        it.row |= {
            "settlement_id": sid,
            "settlement_utr": utr,
            "settled_at": _epoch(sday, 23, 40) if late else _epoch(sday, 10, 30),
        }
    return batches


def write(out: Path, seed: int = DEMO_SEED) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed + 1)
    plan = plan_for(seed)
    w = build(seed, plan)
    batches = settle_items(rng, w)

    report = sorted(
        (it for it in w.items if PERIOD_START <= it.created + timedelta(LAG_DAYS) <= PERIOD_END),
        key=lambda it: (it.row["settled_at"], it.row["created_at"]),
    )
    net: dict[str, int] = {}
    for it in report:
        net[it.row["settlement_id"]] = (
            net.get(it.row["settlement_id"], 0) + it.row["credit"] - it.row["debit"]
        )

    with open(out / FILES["rp"], "w", newline="") as f:
        wr = csv.DictWriter(f, RP_COLUMNS)
        wr.writeheader()
        for it in report:
            base = dict.fromkeys(RP_COLUMNS, "")
            base |= {
                "currency": "INR",
                "on_hold": "false",
                "settled": "true",
                "credit_type": "default",
            }
            wr.writerow(base | it.row)

    # Bank: one credit per settlement on the settlement day, plus the planted bank problems.
    missing = {date(2026, 10, d) for d in plan.missing_days}
    short = {date(2026, 10, d): gap for d, gap in plan.short_days}
    split = {date(2026, 10, d) for d in plan.split_days}
    lines = []
    for sday, (sid, utr) in sorted(batches.items()):
        if sid not in net and sday != date(2026, 9, 30):
            continue
        narration = f"NEFT CR-YESB0000001-RAZORPAY SOFTWARE PVT LTD-KAAPI KART-{utr}"
        if sday == date(2026, 9, 30):  # last month's settlement credited on 1 Oct
            amount = sum(
                it.row["credit"] - it.row["debit"]
                for it in w.items
                if it.row["settlement_id"] == sid
            )
            lines.append((PERIOD_START, narration, utr, 0, amount))
        elif sday in missing:
            w.planted.append({"check": "settlement_missing", "refs": [sid], "amount": net[sid]})
        elif sday in short:
            gap = min(short[sday], net[sid] // 2)
            lines.append((sday, narration, utr, 0, net[sid] - gap))
            w.planted.append({"check": "settlement_short", "refs": [sid], "amount": gap})
        elif sday in split:
            part = net[sid] // 3
            lines.append((sday, narration, utr, 0, part))
            lines.append((sday, narration + " PART 2", utr, 0, net[sid] - part))
        elif sday == PERIOD_END:
            pass  # settled 23:40 on 31 Oct, reaches the bank on 1 Nov
        else:
            lines.append((sday, narration, utr, 0, net[sid]))
    for d, text, debit in [
        (date(2026, 10, 1), "IMPS DR-RENT-OCT-INDIRANAGAR UNIT", 8500000),
        (date(2026, 10, 7), "NEFT DR-GSTN-GST PAYMENT SEP", 21460000),
        (date(2026, 10, 30), "SALARY BATCH OCT 2026", 64200000),
    ]:
        lines.append((d, text, "", debit, 0))
    lines.append(
        (date(2026, 10, 12), "NEFT CR-AMAZON SELLER SERVICES-DISB", "AMZ88120", 0, 18733000)
    )
    lines.sort(key=lambda r: r[0])

    balance = 120000000
    with open(out / FILES["bank"], "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(BANK_COLUMNS)
        for d, text, ref, debit, credit in lines:
            balance += credit - debit
            wr.writerow([d.strftime("%d/%m/%Y"), text, ref, _r(debit), _r(credit), _r(balance)])

    with open(out / FILES["orders"], "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(ORDER_COLUMNS)
        for o in w.orders:
            if o.day < PERIOD_START:
                continue
            wr.writerow([
                o.no, o.day.isoformat(), o.customer, _r(o.amount), o.method, o.status,
                _r(o.refund_amount) if o.refund_amount else "",
                o.refund_day.isoformat() if o.refund_day else "",
            ])  # fmt: skip

    truth = {
        "period": [PERIOD_START.isoformat(), PERIOD_END.isoformat()],
        "planted": w.planted,
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=2))
    return truth


def _r(paise: int) -> str:
    return f"{paise / 100:.2f}" if paise else ""


def chaos(seed: int) -> tuple[dict[str, Path], dict]:
    """A fresh random month for the "Inject chaos" button: file paths and the answer key."""
    out = Path(tempfile.mkdtemp(prefix=f"settle-chaos-{seed}-"))
    truth = write(out, seed)
    return {k: out / v for k, v in FILES.items()}, truth


if __name__ == "__main__":
    import sys

    seed = int(sys.argv[1]) if len(sys.argv) > 1 else DEMO_SEED
    out = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path(__file__).resolve().parent.parent / "data" / "sample"
    )
    t = write(out, seed)
    print(f"planted {len(t['planted'])} problems in {out}")
