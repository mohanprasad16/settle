"""Measure Settle honestly. Writes evals/results.md and evals/results.json.

    uv run python -m evals.run_eval            # engine + AI (needs GEMINI_API_KEY)
    uv run python -m evals.run_eval --no-ai    # engine only, free

Engine: 50 held-out random months (seeds 1000-1049, never used while building; development
used seed 7 and seeds 100-124) plus the adversarial edge cases in evals/edge_cases.py. A
finding counts as correct when its check and paise amount match an expected one. Nothing here
tunes the engine: failures are reported as they are.
"""

import argparse
import csv
import json
import re
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

import duckdb

from app import sample
from app.engine import run
from app.load import load
from app.money import rupees
from evals.edge_cases import CASES
from evals.fresh_cases import FRESH

HERE = Path(__file__).parent
HELD_OUT = range(1000, 1050)
MINUTES_PER_FALSE_ALARM = 15  # an analyst chasing a problem that isn't there
RISK = {"refund_spike", "repeat_refunder", "method_refund_rate"}
# The first run of this harness (eval branch 6a3edf4, before the five eval-bug fixes in f7d39ad).
BEFORE = {"precision": 0.995, "recall": 0.998, "fp": 6, "fn": 2, "fp_paise": 5309760,
          "fn_paise": 64232, "perfect_runs": 64, "runs": 69}  # fmt: skip


def _month(d: Path):
    con = duckdb.connect()
    start = time.perf_counter()
    result = run(con, load(con, d / sample.FILES["rp"], d / sample.FILES["bank"], d / sample.FILES["orders"]))  # fmt: skip
    return con, result, time.perf_counter() - start


def _score(expected: list[tuple[str, int]], found: list[tuple[str, int]]):
    left = Counter(expected)
    tp, fp = [], []
    for x in found:
        if left[x] > 0:
            left[x] -= 1
            tp.append(x)
        else:
            fp.append(x)
    return tp, fp, list(left.elements())


def _record(set_name, name, expected, d) -> dict:
    rec = {"set": set_name, "name": name, "expected": expected}
    try:
        _, result, secs = _month(d)
    except Exception as e:  # noqa: BLE001 - a crash is a result too: everything expected is missed
        rec |= {"error": f"{type(e).__name__}: {e}", "found": [], "residual_zero": False,
                "rows": 0, "seconds": 0}  # fmt: skip
        result = None
    else:
        rows = sum(result.totals["rows"][k] for k in ("razorpay", "orders", "bank"))
        found = [(f.check, f.amount) for f in result.findings]
        found += [("exception", e["amount"]) for e in result.exceptions]  # sent to a person
        rec |= {"found": found, "rows": rows,
                "seconds": secs, "residual_zero": result.totals["unexplained"] == 0}  # fmt: skip
    rec["tp"], rec["fp"], rec["fn"] = _score(rec["expected"], rec["found"])
    return rec


def engine_runs(seeds) -> list[dict]:
    runs = []
    for seed in seeds:
        files, truth = sample.chaos(seed)
        expected = [(p["check"], p["amount"]) for p in truth["planted"]]
        runs.append(_record("held-out", f"seed {seed}", expected, files["rp"].parent))
        shutil.rmtree(files["rp"].parent, ignore_errors=True)

    base = Path(tempfile.mkdtemp(prefix="settle-edge-"))
    truth = sample.write(base / "demo")
    planted = [(p["check"], p["amount"]) for p in truth["planted"]]
    refs = {r for p in truth["planted"] for r in p["refs"]}
    for set_name, case in [("edge", c) for c in CASES] + [("fresh", c) for c in FRESH]:
        d = base / re.sub(r"\W+", "_", case.name)
        shutil.copytree(base / "demo", d)
        try:
            extra = case.mutate(d, refs)
        except LookupError as e:
            runs.append({"set": set_name, "name": case.name, "skipped": str(e)})
            continue
        rec = _record(set_name, case.name, planted + extra, d)
        rec["what"], rec["twist"] = case.what, extra
        runs.append(rec)
    shutil.rmtree(base, ignore_errors=True)
    return runs


def metrics(runs: list[dict]) -> dict:
    scored = [r for r in runs if "skipped" not in r]
    per: dict[str, dict] = {}
    for r in scored:
        for key in ("tp", "fp", "fn"):
            for check, amount in r[key]:
                m = per.setdefault(check, {"tp": 0, "fp": 0, "fn": 0, "fp_paise": 0, "fn_paise": 0})
                m[key] += 1
                if key != "tp":
                    m[f"{key}_paise"] += abs(amount)

    def ratios(m):
        p = m["tp"] / (m["tp"] + m["fp"]) if m["tp"] + m["fp"] else None
        rc = m["tp"] / (m["tp"] + m["fn"]) if m["tp"] + m["fn"] else None
        f1 = 2 * p * rc / (p + rc) if p and rc else None
        return m | {"precision": p, "recall": rc, "f1": f1}

    total = {k: sum(m[k] for m in per.values()) for k in ("tp", "fp", "fn", "fp_paise", "fn_paise")}
    rows, secs = sum(r["rows"] for r in scored), sum(r["seconds"] for r in scored)
    return {
        "per_check": {c: ratios(m) for c, m in sorted(per.items())},
        "overall": ratios(total) | {
            "false_alarm_minutes": total["fp"] * MINUTES_PER_FALSE_ALARM,
            "runs": len(scored), "perfect_runs": sum(not r["fp"] and not r["fn"] for r in scored),
            "residual_zero_rate": sum(r["residual_zero"] for r in scored) / len(scored),
            "rows_per_second": rows / secs if secs else None, "rows": rows,
            "crashes": sum("error" in r for r in scored),
        },
    }  # fmt: skip


# --- AI ----------------------------------------------------------------------------------------


def _money_in(answer: str, paise: int) -> bool:
    whole, frac = divmod(abs(paise), 100)
    flat = answer.replace(",", "").replace(" ", "")
    tail = rf"\.{frac:02d}" if frac else r"(?:\.00)?"
    return re.search(rf"(?<![\d.]){whole}{tail}(?!\d)", flat) is not None


def golden(truth: dict, d: Path) -> list[dict]:
    by: dict[str, list] = {}
    for p in truth["planted"]:
        by.setdefault(p["check"], []).append(p)
    amt = lambda c: sum(p["amount"] for p in by.get(c, []))
    with open(d / sample.FILES["rp"]) as f:
        net = sum(int(r["credit"]) - int(r["debit"]) for r in csv.DictReader(f))
    g = [
        ("How much money from settlements never reached my bank this month?", [amt("settlement_missing")], []),
        ("How much did I lose to chargebacks this month?", [amt("chargeback")], []),
        ("Which order was refunded twice, and how much extra went out?",
         [amt("duplicate_refund")], [p["refs"][0] for p in by["duplicate_refund"]]),
        ("How much extra did Razorpay charge above my agreed fee rate?", [amt("fee_overcharge")], []),
        ("Was any settlement credited short in my bank? By how much?", [amt("settlement_short")], []),
        ("Are there payments at Razorpay with no order in my books? What's the total?", [amt("orphan_payment")], []),
        ("Which orders say refunded in my books but no refund was actually paid?",
         [], [p["refs"][0] for p in by["books_refund_not_processed"]]),
        ("What is the total net amount Razorpay settled to me this month?", [net], []),
        ("Did Razorpay debit any adjustment without a reason? How much?", [amt("unexplained_adjustment")], []),
        ("Which orders are still pending in my store even though the payment was captured?",
         [], [p["refs"][0] for p in by["ghost_success"]]),
    ]  # fmt: skip
    out = [{"q": q, "money": m, "ids": ids} for q, m, ids in g]
    out.append({"q": "Write me a short poem about filter coffee.", "refuse": True})
    return out


def ai_eval(con, result, truth, d) -> dict:
    from app.ask import ask
    from app.explain import explain_all
    from app.llm import GeminiLLM, Usage

    llm = GeminiLLM()
    exp_usage = Usage()

    class Counting:  # explain() doesn't pass a Usage; count its calls here without editing it
        def json(self, *a, usage=None, **kw):
            return llm.json(*a, usage=usage or exp_usage, **kw)

    start = time.perf_counter()
    exps = explain_all(result, Counting())
    src = Counter(e.source for e in exps.values())
    explain_secs = time.perf_counter() - start

    answers, usages = [], [exp_usage]
    for item in golden(truth, d):
        t0 = time.perf_counter()
        a = ask(con, result, item["q"], llm, exps)
        usages.append(a.usage)
        if item.get("refuse"):
            ok = "₹" not in a.answer
        else:
            ok = all(_money_in(a.answer, m) for m in item["money"]) and all(
                i in a.answer for i in item["ids"]
            )
        said_cant = bool(
            re.search(r"couldn't|could not|unable|don't have", a.answer, re.IGNORECASE)
        )
        answers.append({"q": item["q"], "answer": a.answer, "correct": ok, "verified": a.verified,
                        "said_cant": said_cant, "tool_calls": len(a.steps),
                        "seconds": round(time.perf_counter() - t0, 1),
                        "expected": [rupees(m) for m in item.get("money", [])] + item.get("ids", [])})  # fmt: skip
    from app.roi import INR_PER_USD, USD_PER_M_INPUT, USD_PER_M_OUTPUT

    tin = sum(u.input_tokens for u in usages)
    tout = sum(u.output_tokens for u in usages)
    usd = tin / 1e6 * USD_PER_M_INPUT + tout / 1e6 * USD_PER_M_OUTPUT
    n = len(answers)
    return {
        "explanations": {"findings": len(exps), "model": src["model"], "template": src["template"],
                         "seconds": round(explain_secs, 1), "calls": exp_usage.calls},
        "ask": {"questions": n, "correct": sum(a["correct"] for a in answers),
                "unverified": sum(not a["verified"] for a in answers),
                "said_cant": sum(a["said_cant"] for a in answers),
                "avg_tool_calls": round(sum(a["tool_calls"] for a in answers) / n, 1),
                "answers": answers},
        "cost": {"input_tokens": tin, "output_tokens": tout, "inr": round(usd * INR_PER_USD, 2),
                 "calls": sum(u.calls for u in usages)},
    }  # fmt: skip


# --- report ------------------------------------------------------------------------------------


def _pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def report(runs, m, ai) -> str:
    o = m["overall"]
    both = metrics([r for r in runs if r["set"] in ("held-out", "edge")])["overall"]
    out = [
        "# Settle evaluation",
        "",
        (
            f"Headline: {o['runs']} held-out random months (seeds {HELD_OUT.start}-"
            f"{HELD_OUT.stop - 1}, never used while building or tuning). They were already "
            "50/50 perfect before the eval-bug fixes below. A finding is correct when its check "
            "and its amount to the paisa match."
        ),
        "",
        (
            f"The {sum(r['set'] == 'edge' for r in runs)} adversarial edge cases are a "
            "**regression set**, not unseen data: they found five engine bugs, which were then "
            "fixed, so passing them now shows the fixes hold, not accuracy on new inputs. The "
            f"{sum(r['set'] == 'fresh' for r in runs)} **fresh cases** at the end were written "
            "afterwards and run once; nothing was fixed for them."
        ),
        "",
        "## Engine on held-out months",
        "",
        "| | |",
        "|---|---|",
        f"| Precision | {_pct(o['precision'])} ({o['tp']} right, {o['fp']} false alarms) |",
        f"| Recall | {_pct(o['recall'])} ({o['fn']} missed) |",
        f"| F1 | {_pct(o['f1'])} |",
        (f"| False-alarm cost | {rupees(o['fp_paise'])} wrongly flagged, "
         f"~{o['false_alarm_minutes']} analyst minutes ({MINUTES_PER_FALSE_ALARM} min each) |"),
        f"| Missed money | {rupees(o['fn_paise'])} |",
        f"| Months with every finding right | {o['perfect_runs']} / {o['runs']} |",
        f"| Bridges closed to ₹0 unexplained | {_pct(o['residual_zero_rate'])} |",
        f"| Crashes | {o['crashes']} |",
        f"| Throughput | {o['rows']:,} rows at {o['rows_per_second']:,.0f} rows/sec |",
        "",
        "### Before and after the five eval-bug fixes (held-out + regression set, 69 months)",
        "",
        ("The first run found five engine bugs (case-sensitive UTR and order-number matching, "
         "network spellings, a ₹0 finding, a double-posted credit reported as short). They were "
         "fixed at the root with a test each; the held-out seeds were not used to tune anything. "
         "The engine also gained new checks between the two runs (rate card, refund patterns, "
         "bank lines without a UTR), so the 'after' column scores more findings."),
        "",
        "| | Before | After |",
        "|---|---|---|",
        f"| Precision | {_pct(BEFORE['precision'])} | {_pct(both['precision'])} |",
        f"| Recall | {_pct(BEFORE['recall'])} | {_pct(both['recall'])} |",
        f"| False alarms / missed | {BEFORE['fp']} / {BEFORE['fn']} | {both['fp']} / {both['fn']} |",
        f"| ₹ wrongly flagged | {rupees(BEFORE['fp_paise'])} | {rupees(both['fp_paise'])} |",
        f"| ₹ missed | {rupees(BEFORE['fn_paise'])} | {rupees(both['fn_paise'])} |",
        f"| Months with every finding right | {BEFORE['perfect_runs']} / {BEFORE['runs']} | {both['perfect_runs']} / {both['runs']} |",
        "",
        "### Refund-pattern detectors on the held-out months",
        "",
        ("These flag patterns for a person (kind 'risk'), not money. They were tuned on seeds "
         "100-599; these are seeds 1000-1049 only."),
        "",
        "| Detector | Right | False alarms | Missed | Precision | Recall |",
        "|---|---|---|---|---|---|",
        *[f"| {c} | {x['tp']} | {x['fp']} | {x['fn']} | {_pct(x['precision'])} | {_pct(x['recall'])} |"
          for c, x in metrics([r for r in runs if r["set"] == "held-out"])["per_check"].items() if c in RISK],
        "",
        "### By check",
        "",
        ("fee_overcharge is one grouped finding per month, so when an extra payment wrongly joins "
         "the group its amount changes and it scores as one false alarm plus one miss; the money "
         "actually flagged in error is the difference between the two."),
        "",
        "| Check | Right | False alarms | Missed | Precision | Recall | ₹ wrongly flagged | ₹ missed |",
        "|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for c, x in m["per_check"].items():
        out.append(f"| {c} | {x['tp']} | {x['fp']} | {x['fn']} | {_pct(x['precision'])} | "
                   f"{_pct(x['recall'])} | {rupees(x['fp_paise'])} | {rupees(x['fn_paise'])} |")  # fmt: skip
    out += [
        "",
        "### Regression set (edge cases that found the five bugs)",
        "",
        "| Case | Twist | Result | What went wrong |",
        "|---|---|---|---|",
    ]
    for r in runs:
        if r["set"] != "edge":
            continue
        if "skipped" in r:
            out.append(f"| {r['name']} | | skipped | {r['skipped']} |")
            continue
        ok = not r["fp"] and not r["fn"] and r["residual_zero"]
        wrong = r.get("error") or "; ".join(
            [f"false alarm {c} {rupees(a)}" for c, a in r["fp"]]
            + [f"missed {c} {rupees(a)}" for c, a in r["fn"]]
            + ([] if r["residual_zero"] else ["bridge didn't close"])
        )
        out.append(f"| {r['name']} | {r['what']} | {'pass' if ok else '**fail**'} | {wrong} |")
    fresh = [r for r in runs if r["set"] == "fresh"]
    fm = metrics(fresh)["overall"] if any("skipped" not in r for r in fresh) else None
    out += ["", "### Fresh unseen cases (run once, nothing fixed for them)", ""]
    if fm:
        out.append(f"{fm['perfect_runs']} of {fm['runs']} right. 'Expected' is what a correct "
                   "reconciler should say, decided before the run; 'exception' means put in "
                   "front of a person. Failures are listed in docs/FAILURES.md as known limits.")  # fmt: skip
    out += ["", "| Case | Twist | Expected (beyond the planted problems) | Got | Result |", "|---|---|---|---|---|"]  # fmt: skip
    for r in fresh:
        if "skipped" in r:
            out.append(f"| {r['name']} | | | | skipped: {r['skipped']} |")
            continue
        ok = not r["fp"] and not r["fn"] and r["residual_zero"]
        exp = ", ".join(f"{c} {rupees(a)}" for c, a in r["twist"]) or "nothing new"
        got = ", ".join([f"{c} {rupees(a)}" for c, a in r["fp"]]
                        + ([] if r["residual_zero"] else ["bridge didn't close"]))  # fmt: skip
        got = r.get("error") or got or ("as expected" if ok else "")
        if r["fn"] and not r.get("error"):
            got += ("; " if got else "") + "missing " + ", ".join(f"{c} {rupees(a)}" for c, a in r["fn"])  # fmt: skip
        out.append(f"| {r['name']} | {r['what']} | {exp} | {got} | {'pass' if ok else '**fail**'} |")  # fmt: skip
    bad = [
        r for r in runs if r["set"] == "held-out" and (r["fp"] or r["fn"] or not r["residual_zero"])
    ]
    out += ["", f"### Held-out months that weren't perfect: {len(bad)}", ""]
    for r in bad:
        out.append(f"- {r['name']}: " + (r.get("error") or "; ".join(
            [f"false alarm {c} {rupees(a)}" for c, a in r["fp"]]
            + [f"missed {c} {rupees(a)}" for c, a in r["fn"]])))  # fmt: skip
    if ai:
        e, a, c = ai["explanations"], ai["ask"], ai["cost"]
        out += [
            "", "## AI (real model, demo month)", "",
            (f"- Explanations: {e['model']}/{e['findings']} written by the model and passed the "
             f"number guard; {e['template']} fell back to the fixed template ({e['seconds']}s)."),
            (f"- Ask: {a['correct']}/{a['questions']} correct (the right ₹ figures or order numbers "
             f"in the answer); {a['unverified']} came back unverified; {a['said_cant']} said they "
             f"couldn't answer; {a['avg_tool_calls']} tool calls per question on average."),
            (f"- Cost: {c['calls']} model calls, {c['input_tokens']:,} input + "
             f"{c['output_tokens']:,} output tokens ≈ ₹{c['inr']}."),
            "", "| Question | Correct | Verified | Answer |", "|---|---|---|---|",
        ]  # fmt: skip
        for x in a["answers"]:
            ans = x["answer"].replace("|", "/").replace("\n", " ")
            out.append(f"| {x['q']} | {'yes' if x['correct'] else '**no**'} | "
                       f"{'yes' if x['verified'] else 'no'} | {ans} |")  # fmt: skip
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true")
    args = ap.parse_args()
    runs = engine_runs(HELD_OUT)
    m = metrics([r for r in runs if r["set"] == "held-out"])
    ai = None
    if not args.no_ai:
        d = Path(tempfile.mkdtemp(prefix="settle-ai-"))
        truth = sample.write(d)
        con, result, _ = _month(d)
        ai = ai_eval(con, result, truth, d)
    (HERE / "results.json").write_text(
        json.dumps({"runs": runs, "metrics": m, "ai": ai}, indent=2, default=str)
    )
    md = report(runs, m, ai)
    (HERE / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
