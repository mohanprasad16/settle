"""Plain-English explanation for each finding. The model writes words; the engine owns numbers.

Each finding becomes a fact sheet with money and dates already formatted. The model may only
repeat figures from it: a guard checks every number it writes, retries once naming the
offenders, then falls back to a fixed template. The sample's explanations are cached so the
demo loads instantly with no model calls.

    uv run python -m app.explain     # regenerate data/sample/explanations.json
"""

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.engine import Finding, Result
from app.llm import LLM, LLMError
from app.money import rupees

log = logging.getLogger(__name__)

MONEY = {"order_amount", "captured", "difference", "books_refund", "razorpay_refunds", "amount",
         "net_settled", "bank_received"}  # fmt: skip
PCT = {"agreed_rate_pct", "charged_rate_pct", "gst_pct"}
SAMPLE_CACHE = Path(__file__).resolve().parent.parent / "data" / "sample" / "explanations.json"

SYSTEM = """You explain one reconciliation finding to a small-business owner in India.
Use only figures that appear in the fact sheet.
Copy rupee amounts exactly as written in the fact sheet.
Never introduce new numbers, percentages, dates or totals, and never do arithmetic.
Use Indian finance terms where they fit: settlement, UTR, chargeback, GST on fees.
Write plainly, no jargon beyond those terms, no greetings.
Refer to facts in plain words; never write the fact sheet's field names.
likely_cause is a hedged guess that starts with "Most likely".
what_to_do gives 1 to 3 concrete steps the owner can take this week."""


class Explanation(BaseModel):
    headline: str
    what_happened: str
    likely_cause: str
    what_to_do: list[str]
    who_to_contact: Literal[
        "Razorpay support", "your bank", "your accountant", "the customer", "your store/ops team"
    ]


class Explained(Explanation):
    source: Literal["model", "template"]
    verified: bool = True


def _fmt(key: str, v) -> str:
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return f"{v.day} {v:%b %Y}"
    if isinstance(v, list):
        return ", ".join(_fmt(key, x) for x in v)
    if key in MONEY and isinstance(v, int):
        return rupees(v)
    if key in PCT:
        return f"{v:g}%"
    return str(v)


def fact_sheet(f: Finding) -> dict[str, str]:
    sheet = {"finding": f.title, "kind": f.kind, "impact": rupees(f.amount)}
    sheet |= {k: _fmt(k, v) for k, v in f.facts.items() if v not in (None, "", [])}
    sheet["reference_ids"] = ", ".join(r for r in f.refs[:10] if r) + (
        f" (+{len(f.refs) - 10} more)" if len(f.refs) > 10 else ""
    )
    return sheet


_TOKEN = re.compile(r"₹\s?[\d,]+(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?%|[A-Za-z_-]*\d[\w,.-]*")


def _norm(tok: str) -> str:
    tok = tok.replace("₹", "").replace(",", "").replace(" ", "").rstrip(".-")
    if tok.endswith("%"):
        return f"{float(tok[:-1]):g}%"
    return tok.removesuffix(".00")


def unsupported(exp: Explanation, sheet: dict[str, str]) -> list[str]:
    """Numbers in the explanation that the fact sheet doesn't contain."""
    allowed = {_norm(t) for v in sheet.values() for t in _TOKEN.findall(v)}
    text = " ".join([exp.headline, exp.what_happened, exp.likely_cause, *exp.what_to_do])
    bad = []
    for t in _TOKEN.findall(text):
        n = _norm(t)
        digits = re.sub(r"\D", "", n)
        if (t.startswith("₹") or n.endswith("%") or len(digits) >= 2) and n not in allowed:
            bad.append(t)
    return bad


def template(f: Finding) -> Explained:
    s = fact_sheet(f)
    g = s.get
    t = {
        "settlement_missing": (
            f"Settlement {g('settlement_id')} of {g('net_settled')} never reached your bank.",
            f"Razorpay settled {g('net_settled')} on {g('settled_on')} under UTR {g('utr')}, but no bank credit with that UTR is in your statement.",
            "Most likely the transfer failed or was returned by the bank.",
            ["Search your bank statement for the UTR.", "Raise a ticket with Razorpay support quoting the settlement ID and UTR."],
            "Razorpay support"),
        "settlement_short": (
            f"Bank received {g('impact')} less than settlement {g('settlement_id')}.",
            f"Razorpay settled {g('net_settled')} under UTR {g('utr')}; your bank shows {g('bank_received')}.",
            "Most likely part of the transfer was held or reversed by the bank.",
            ["Ask your bank for the full credit details of the UTR.", "Share the gap with Razorpay support."],
            "your bank"),
        "paid_order_no_payment": (
            f"Order {g('order_no')} is marked paid but no payment came in.",
            f"Your books show {g('order_no')} as paid for {g('order_amount')} on {g('order_date')}, but Razorpay has no payment for it.",
            "Most likely the payment failed and the order was marked paid anyway.",
            ["Check whether the goods were shipped.", "Ask the customer to pay or cancel the order."],
            "your store/ops team"),
        "duplicate_refund": (
            f"Order {g('order_no')} was refunded twice.",
            f"Your books refunded {g('books_refund')}, but Razorpay paid out {g('razorpay_refunds')} across {g('refund_count')} refunds ({g('refund_ids')}).",
            "Most likely the refund was triggered twice.",
            ["Ask the customer to return the extra refund.", "Check who can trigger refunds in your Razorpay dashboard."],
            "the customer"),
        "chargeback": (
            f"Chargeback of {g('amount')} on order {g('order_no')}.",
            f"Razorpay debited {g('amount')} for dispute {g('dispute_id')}, but your books still show order {g('order_no')} as paid.",
            "Most likely the customer disputed the charge with their bank.",
            ["Submit delivery proof to contest the dispute in your Razorpay dashboard.", "Mark the order as charged back in your books."],
            "Razorpay support"),
        "fee_overcharge": (
            f"Fees above your agreed rate on {g('payments')} payments.",
            f"You agreed to {g('agreed_rate_pct')}, but these payments were charged {g('charged_rate_pct')}, costing you {g('impact')} extra including GST.",
            "Most likely a pricing change was applied to your account by mistake.",
            ["Ask Razorpay support to correct the rate and refund the difference."],
            "Razorpay support"),
        "gst_mismatch": (
            f"GST on fees isn't {g('gst_pct')} on {g('payments')} payments.",
            f"The GST charged on fees for these payments doesn't match {g('gst_pct')}; the difference is {g('impact')}.",
            "Most likely a rounding or tax setup error.",
            ["Ask Razorpay support for a corrected tax invoice."],
            "Razorpay support"),
        "unexplained_adjustment": (
            f"Razorpay debited {g('amount')} with no reason given.",
            f"Adjustment {g('adjustment_id')} took {g('amount')} out of settlement {g('settlement_id')} with no description.",
            "Most likely a fee or reversal that wasn't labelled.",
            ["Ask Razorpay support what the adjustment is for."],
            "Razorpay support"),
        "duplicate_payment": (
            f"Customer paid twice for order {g('order_no')}.",
            f"Order {g('order_no')} is {g('order_amount')}, but Razorpay captured {g('captured')}.",
            "Most likely the customer retried a payment that had already gone through.",
            ["Refund the extra payment to the customer."],
            "the customer"),
        "books_refund_not_processed": (
            f"Refund for order {g('order_no')} never went out.",
            f"Your books show a refund of {g('books_refund')}, but Razorpay has no refund for {g('order_no')}.",
            "Most likely the refund was recorded but never started in Razorpay.",
            ["Issue the refund in Razorpay.", "Tell the customer when it will reach them."],
            "the customer"),
        "refund_mismatch": (
            f"Refund amount differs for order {g('order_no')}.",
            f"Your books refunded {g('books_refund')}, Razorpay refunded {g('razorpay_refunds')}.",
            "Most likely a partial refund was recorded with the wrong amount.",
            ["Correct the refund amount in your books."],
            "your accountant"),
        "orphan_payment": (
            f"Payment of {g('amount')} has no order in your books.",
            f"Razorpay received {g('amount')} as {g('payment_id')} (receipt {g('receipt')}), but no order with that number is in your books.",
            "Most likely the sale came from a channel that isn't in your export.",
            ["Find the order and add it to your books so the sale is recorded."],
            "your accountant"),
        "refund_not_in_books": (
            f"Order {g('order_no')} was refunded but books say paid.",
            f"Razorpay refunded {g('razorpay_refunds')} for {g('order_no')}, but your books still show it as {g('books_status')}.",
            "Most likely the refund was made in Razorpay without updating the books.",
            ["Mark the order as refunded in your books."],
            "your accountant"),
        "amount_mismatch": (
            f"Order {g('order_no')} captured {g('impact')} less than billed.",
            f"The order is {g('order_amount')} but Razorpay captured {g('captured')}.",
            "Most likely a discount or coupon was applied at checkout and not in the books.",
            ["Check the checkout discount and correct the order value in your books."],
            "your accountant"),
    }[f.check]  # fmt: skip
    return Explained(
        headline=t[0], what_happened=t[1], likely_cause=t[2], what_to_do=t[3],
        who_to_contact=t[4], source="template",
    )  # fmt: skip


def explain(f: Finding, llm: LLM | None) -> Explained:
    if llm is None:
        return template(f)
    sheet = fact_sheet(f)
    prompt = "Fact sheet:\n" + "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in sheet.items())
    for attempt in range(2):
        try:
            exp = llm.json(SYSTEM, prompt, Explanation)
        except LLMError as e:
            log.warning("explain %s failed: %s", f.id, e)
            break
        bad = unsupported(exp, sheet)
        if not bad:
            return Explained(**exp.model_dump(), source="model")
        log.info("explain %s attempt %d unsupported numbers %s", f.id, attempt, bad)
        prompt += f"\n\nYour last answer used figures not in the fact sheet: {', '.join(bad)}. Remove them."  # fmt: skip
    return template(f)


def cache_key(f: Finding) -> str:
    raw = json.dumps([f.check, f.amount, f.facts], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def load_cache(path: Path) -> dict[str, Explained]:
    if not path.exists():
        return {}
    return {k: Explained(**v) for k, v in json.loads(path.read_text()).items()}


def save_cache(path: Path, cache: dict[str, Explained]) -> None:
    path.write_text(json.dumps({k: v.model_dump() for k, v in cache.items()}, indent=2))


def explain_all(result: Result, llm: LLM | None, cache: dict | None = None) -> dict[str, Explained]:
    """Finding id -> explanation. Cache hits cost nothing; misses go to the model in parallel."""
    cache = {} if cache is None else cache
    todo = [f for f in result.findings if cache_key(f) not in cache]
    with ThreadPoolExecutor(6) as pool:
        for f, exp in zip(todo, pool.map(lambda f: explain(f, llm), todo)):
            cache[cache_key(f)] = exp
    return {f.id: cache[cache_key(f)] for f in result.findings}


if __name__ == "__main__":
    import duckdb

    from app.engine import run
    from app.llm import GeminiLLM
    from app.load import load

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = SAMPLE_CACHE.parent
    con = duckdb.connect()
    result = run(con, load(con, d / "razorpay_settlements.csv", d / "bank_statement.csv", d / "orders.csv"))  # fmt: skip
    cache: dict[str, Explained] = {}
    out = explain_all(result, GeminiLLM(), cache)
    save_cache(SAMPLE_CACHE, cache)
    sources = [e.source for e in out.values()]
    print(f"{sources.count('model')} model, {sources.count('template')} template")
