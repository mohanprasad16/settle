"""Desk on its golden set: the sample inbox's answer keys, with the real model.

    uv run python -m evals.desk_eval     # needs GEMINI_API_KEY; writes evals/desk_results.md

Scores match accuracy (code: the right order or payout, or "ambiguous" / "none"), category
accuracy (model) and "every figure verified" (the reply the model wrote passed the number guard,
with no template fallback). Matching is also run, code only, on the 50 held-out random months.
No cache: every message goes to the model fresh.
"""

import json
import tempfile
import time
from pathlib import Path

import duckdb

from app import desk, sample
from app.drafts import bad_numbers
from app.engine import run
from app.llm import GeminiLLM
from app.load import load

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"
OUT = Path(__file__).resolve().parent / "desk_results.md"
STORE = "Kaapi Kart Foods Pvt Ltd"


class Counting:
    def __init__(self, llm):
        self.llm, self.calls = llm, 0

    def json(self, *a, **kw):
        self.calls += 1
        return self.llm.json(*a, **kw)


def _month(d: Path):
    con = duckdb.connect()
    result = run(con, load(con, d / "razorpay_settlements.csv", d / "bank_statement.csv", d / "orders.csv"))  # fmt: skip
    return con, result, desk.index(con, result), json.loads((d / "inbox.json").read_text())


def _matched(t, m) -> bool:
    return (t["match"]["status"], t["about"] or None) == (m["expected"]["match"], m["expected"]["ref"])


def main() -> None:
    _, result, ix, inbox = _month(SAMPLE)
    llm = Counting(GeminiLLM())
    rows, t0 = [], time.perf_counter()
    for m in inbox:
        t = desk.ticket(ix, result, m, STORE, {}, llm)
        sheet = desk.facts(ix, result, m, t["match"], STORE, t["category"])[0]
        rows.append({"id": m["id"], "channel": m["channel"], "text": m["text"], "expected": m["expected"],
                     "match_ok": _matched(t, m), "got_match": [t["match"]["status"], t["about"]],
                     "category": t["category"], "category_ok": t["category"] == m["expected"]["category"],
                     "source": t["reply"]["source"], "guard_clean": not bad_numbers(t["reply"]["body"], sheet),
                     "reply": t["reply"]["body"]})  # fmt: skip
    secs = time.perf_counter() - t0

    held, held_ok = 0, 0
    for seed in range(1000, 1050):
        d = Path(tempfile.mkdtemp(prefix=f"desk-{seed}-"))
        sample.write(d, seed)
        _, r, x, msgs = _month(d)
        for m in msgs:
            held += 1
            held_ok += _matched(desk.ticket(x, r, m, STORE, {}), m)

    n = len(rows)
    match = sum(r["match_ok"] for r in rows)
    cat = sum(r["category_ok"] for r in rows)
    model = sum(r["source"] == "model" for r in rows)
    clean = sum(r["guard_clean"] for r in rows)
    lines = [
        "# Desk evaluation",
        "",
        (f"Run {time.strftime('%Y-%m-%d %H:%M')} with the real model, once, no cache. Golden set: the {n} messages "
        "of the sample inbox, each with an answer key written by the generator (category, and the order or "
        "payout it is about, or ambiguous / none). It includes one prompt injection, two off-topic messages, "
        "one that fits several payments and one with an order number that doesn't exist."),
        "",
        "| | |",
        "|---|---|",
        f"| Match accuracy (code) | {match} / {n} |",
        f"| Category accuracy (model) | {cat} / {n} |",
        f"| Every figure verified: reply written by the model and passed the number guard first time or on the retry | {model} / {n} |",
        f"| Final replies (model or template) with any figure not in the facts | {n - clean} |",
        f"| Model calls | {llm.calls} ({llm.calls / n:.1f} per message; {secs:.0f} s in all) |",
        f"| Matching on the 50 held-out random months (seeds 1000-1049, code only) | {held_ok} / {held} messages |",
        "",
        "## Misses",
        "",
    ]  # fmt: skip
    misses = [r for r in rows if not (r["match_ok"] and r["category_ok"] and r["source"] == "model")]
    lines += [f"- **{r['id']}** ({r['channel']}) “{r['text'][:90]}”: expected {r['expected']['category']} / "
              f"{r['expected']['match']} {r['expected']['ref'] or ''}; got {r['category']} / {' '.join(x or '' for x in r['got_match'])}, "
              f"reply from the {r['source']}." for r in misses] or ["None on this run."]  # fmt: skip
    lines += ["", "## Every message", "", "| Id | Match | Category | Reply | Reply text |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {'✓' if r['match_ok'] else '✗'} {r['got_match'][0]} | {'✓' if r['category_ok'] else '✗'} {r['category']} "
              f"| {r['source']} | {r['reply'].replace('|', '/').replace(chr(10), ' ')} |" for r in rows]  # fmt: skip
    OUT.write_text("\n".join(lines) + "\n")
    (OUT.with_suffix(".json")).write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print("\n".join(lines[6:14]))


if __name__ == "__main__":
    main()
