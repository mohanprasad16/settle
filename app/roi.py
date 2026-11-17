"""What a run is worth: money found, hours saved, what the AI cost. Every figure comes with the
formula behind it, so the UI can show the working instead of a bare claim."""

from app.engine import Result
from app.llm import Usage
from app.money import rupees

# Assumptions, shown next to the result. Change them here, not in the UI.
MINUTES_PER_ROW_BY_HAND = 0.5  # ticking one line across three files in Excel
MINUTES_PER_FINDING = 10  # a person still reads each flagged problem and acts on it
# gemini-3.5-flash list price, checked 17 Nov 2026 (USD per 1M tokens). Every call is priced
# at the main model's rate, so lite-model fallbacks make the real cost lower, not higher.
USD_PER_M_INPUT, USD_PER_M_OUTPUT = 1.50, 9.00
INR_PER_USD = 88.0  # 17 Nov 2026


def roi(result: Result, usages: list[Usage]) -> dict:
    t = result.totals
    found = t["loss"] + t["customer"]
    rows = sum(t["rows"][k] for k in ("razorpay", "orders", "bank"))
    manual = rows * MINUTES_PER_ROW_BY_HAND
    review = len(result.findings) * MINUTES_PER_FINDING
    hours = max(manual - review, 0) / 60
    tokens_in = sum(u.input_tokens for u in usages)
    tokens_out = sum(u.output_tokens for u in usages)
    usd = tokens_in / 1e6 * USD_PER_M_INPUT + tokens_out / 1e6 * USD_PER_M_OUTPUT
    cost = round(usd * INR_PER_USD * 100)  # paise
    return {
        "leakage_found": found,
        "hours_saved": round(hours, 1),
        "ai_cost": cost,
        "found_per_rupee_of_ai": round(found / cost) if cost else None,
        "formulas": {
            "leakage_found": f"money lost {rupees(t['loss'])} + owed to customers "
            f"{rupees(t['customer'])}",
            "hours_saved": f"({rows:,} rows × {MINUTES_PER_ROW_BY_HAND:g} min by hand − "
            f"{len(result.findings)} findings × {MINUTES_PER_FINDING} min to review) ÷ 60",
            "ai_cost": f"{tokens_in:,} input tokens × ${USD_PER_M_INPUT}/M + {tokens_out:,} "
            f"output tokens × ${USD_PER_M_OUTPUT}/M, at ₹{INR_PER_USD:g}/$",
        },
    }
