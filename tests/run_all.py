"""Run every test and write one report a reviewer can open instead of clicking through the app.

    uv run python -m tests.run_all             # unit tests + browser system test (tape replay)
    uv run python -m tests.run_all --live      # browser test against the real model (.env key)
    uv run python -m tests.run_all --publish   # also copy the report to docs/test-report.html

Writes reports/test-report.html (self-contained) and reports/test-summary.md. Every case in
docs/TEST_PLAN.md is marked by the tests it names. Exit code 1 if anything failed.
"""

import argparse
import base64
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports"
PLAN = ROOT / "docs" / "TEST_PLAN.md"
SHOTS = ROOT / "e2e" / "screenshots"
KEY_SHOTS = ["landing_hero_video", "results_overview_desktop", "finding_drawer_desktop", "bridges_desktop",
             "settlements_desktop", "change_card_before_confirm", "ask_with_trace", "chaos_answer_key",
             "pdf_review_broken_row", "settings_policy", "results_phone", "audit_report"]  # fmt: skip


def run_unit() -> tuple[dict[str, dict], float]:
    xml = OUT / "junit.xml"
    t0 = time.perf_counter()
    subprocess.run([sys.executable, "-m", "pytest", "-q", f"--junitxml={xml}", "-p", "no:cacheprovider"], cwd=ROOT, check=False)  # fmt: skip
    took = time.perf_counter() - t0
    results = {}
    for tc in ET.parse(xml).iter("testcase"):
        key = f"{tc.get('classname').replace('.', '/')}.py::{tc.get('name')}"
        bad = tc.find("failure") is not None or tc.find("error") is not None
        skip = tc.find("skipped") is not None
        results[key] = {"ok": not bad, "skipped": skip, "secs": float(tc.get("time") or 0)}
    return results, took


def run_e2e(mode: str) -> tuple[dict, float]:
    t0 = time.perf_counter()
    subprocess.run([sys.executable, "-m", "e2e.test_system"], cwd=ROOT, env=os.environ | {"E2E_LLM": mode}, check=False)  # fmt: skip
    data = json.loads((OUT / "e2e.json").read_text())
    return data, time.perf_counter() - t0


def plan() -> list[dict]:
    cases, area = [], ""
    for line in PLAN.read_text().splitlines():
        if line.startswith("## "):
            area = line[3:].strip()
        m = re.match(r"\|\s*([A-Z0-9]+-\d+)\s*\|(.+?)\|(.+?)\|(.+)\|\s*$", line)
        if m:
            refs = re.findall(r"`([^`]+)`", m.group(4))
            cases.append({"id": m.group(1), "area": area, "case": m.group(2).strip(),
                          "expected": m.group(3).strip(), "refs": refs})  # fmt: skip
    return cases


def mark(cases: list[dict], unit: dict, checks: list[dict]) -> None:
    for c in cases:
        found, status = [], "pass"
        for ref in c["refs"]:
            if ref.startswith("eval:"):
                found.append((ref, "measured"))
                continue
            if ref.startswith("e2e:"):
                hits = [x for x in checks if ref[4:].strip() in x["name"]]
                ok = bool(hits) and all(x["ok"] for x in hits)
                found.append((ref, "pass" if ok else ("missing" if not hits else "fail")))
            else:
                hits = [v for k, v in unit.items() if k == ref or k.startswith(ref + "[")]
                ok = bool(hits) and all(v["ok"] for v in hits)
                found.append((ref, "pass" if ok else ("missing" if not hits else "fail")))
        states = {s for _, s in found}
        if "fail" in states or "missing" in states:
            status = "fail"
        elif states == {"measured"}:
            status = "measured"
        c["refs_status"], c["status"] = found, status


def thumb(name: str) -> str | None:
    path = next(iter(sorted(SHOTS.glob(f"*_{name}.png"))), None)
    if path is None:
        return None
    try:
        from PIL import Image  # dev dependency; the report still works without thumbnails

        im = Image.open(path).convert("RGB")
        im.thumbnail((520, 520))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return None


def render(cases, unit, e2e, unit_secs, e2e_secs) -> tuple[str, str]:
    e = html.escape
    passed = sum(c["status"] == "pass" for c in cases)
    failed = [c for c in cases if c["status"] == "fail"]
    measured = sum(c["status"] == "measured" for c in cases)
    u_ok = sum(v["ok"] and not v["skipped"] for v in unit.values())
    u_skip = sum(v["skipped"] for v in unit.values())
    chk = e2e["checks"]
    e_ok = sum(x["ok"] for x in chk)
    mode = {"replay": "recorded model replies (tape), no key", "live": "live model", "record": "live model, recording"}[e2e["llm"]]  # fmt: skip
    verdict = (
        "All passing"
        if not failed and u_ok + u_skip == len(unit) and e_ok == len(chk)
        else "Failures below"
    )
    areas: dict[str, list] = {}
    for c in cases:
        areas.setdefault(c["area"], []).append(c)
    badge = {"pass": "✓ pass", "fail": "✗ fail", "measured": "measured", "missing": "missing"}
    rows = []
    for area, cs in areas.items():
        ok = sum(c["status"] == "pass" for c in cs)
        rows.append(f"<h2>{e(area)} <span class='muted'>{ok}/{len(cs)}</span></h2><table><tr><th>ID</th><th>Case</th><th>Expected</th><th>Proof</th><th>Result</th></tr>")  # fmt: skip
        for c in cs:
            proof = "<br>".join(f"<code>{e(r)}</code> <span class='{s}'>{badge[s]}</span>" for r, s in c["refs_status"])  # fmt: skip
            rows.append(f"<tr class='{c['status']}'><td><b>{c['id']}</b></td><td>{e(c['case'])}</td><td>{e(c['expected'])}</td><td class='proof'>{proof}</td><td><span class='{c['status']}'>{badge[c['status']]}</span></td></tr>")  # fmt: skip
        rows.append("</table>")
    shots = [(n, thumb(n)) for n in KEY_SHOTS]
    gallery = "".join(f"<figure><img src='data:image/jpeg;base64,{b}' alt='{e(n)}'><figcaption>{e(n.replace('_', ' '))}</figcaption></figure>" for n, b in shots if b)  # fmt: skip
    timing = "".join(f"<tr><td>{e(k)}</td><td class='num'>{v:.2f}</td></tr>" for k, v in e2e["timings"].items())  # fmt: skip
    fails = "".join(f"<li><b>{c['id']}</b> {e(c['case'])}</li>" for c in failed) or "<li>None</li>"
    bad_checks = "".join(f"<li>[{e(x['area'])}] {e(x['name'])}: <code>{e(x['detail'])}</code></li>" for x in chk if not x["ok"]) or "<li>None</li>"  # fmt: skip
    bad_unit = "".join(f"<li><code>{e(k)}</code></li>" for k, v in unit.items() if not v["ok"]) or "<li>None</li>"  # fmt: skip
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Settle test report</title><style>
:root{{--ink:#0f1b2d;--muted:#586579;--line:#e2e8f0;--ok:#067647;--okb:#ecfdf3;--bad:#b42318;--badb:#fef3f2}}
body{{font:14px/1.5 -apple-system,Segoe UI,Inter,Arial,sans-serif;color:var(--ink);margin:0;background:#f4f6fa}}
main{{max-width:1180px;margin:0 auto;padding:28px 20px 60px}} h1{{margin:0 0 4px;font-size:26px}} h2{{margin:28px 0 8px;font-size:17px}}
.muted{{color:var(--muted);font-weight:400}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:16px 0}}
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}} .card b{{display:block;font-size:22px}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px}} th{{background:#f8fafc;color:var(--muted);font-weight:600}}
td.proof{{font-size:12px;max-width:430px;word-break:break-word}} code{{font-size:11.5px;background:#f1f5f9;padding:1px 4px;border-radius:4px}}
.pass{{color:var(--ok);font-weight:600}} .fail,.missing{{color:var(--bad);font-weight:600}} .measured{{color:#3538cd;font-weight:600}}
tr.fail{{background:var(--badb)}} .verdict{{display:inline-block;padding:4px 10px;border-radius:999px;font-weight:600;background:{"var(--okb)" if verdict == "All passing" else "var(--badb)"};color:{"var(--ok)" if verdict == "All passing" else "var(--bad)"}}}
.gallery{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}} figure{{margin:0;background:#fff;border:1px solid var(--line);border-radius:10px;padding:8px}}
figure img{{width:100%;border-radius:6px;display:block}} figcaption{{font-size:12px;color:var(--muted);margin-top:4px}} .num{{text-align:right}}
</style></head><body><main>
<h1>Settle test report</h1>
<p class="muted">Run {time.strftime("%d %b %Y %H:%M")} · unit and API tests (pytest) and the browser system test in real Chrome (Playwright), desktop and phone · model replies: {e(mode)}.</p>
<p><span class="verdict">{verdict}</span></p>
<div class="cards">
<div class="card">Test-plan cases<b>{passed}/{len(cases) - measured}</b><span class="muted">pass{f" · {measured} measured in evals" if measured else ""}</span></div>
<div class="card">Unit and API tests<b>{u_ok}/{len(unit) - u_skip}</b><span class="muted">{unit_secs:.0f} s{f" · {u_skip} skipped" if u_skip else ""}</span></div>
<div class="card">Browser checks<b>{e_ok}/{len(chk)}</b><span class="muted">{e2e_secs:.0f} s, 3 servers</span></div>
<div class="card">Failing cases<b>{len(failed)}</b><span class="muted">see below</span></div>
</div>
<h2>Failures</h2><ul>{fails}</ul><details><summary>Failing browser checks and unit tests</summary><ul>{bad_checks}</ul><ul>{bad_unit}</ul></details>
<h2>Key screens from this run</h2><div class="gallery">{gallery}</div>
{"".join(rows)}
<h2>Timings (browser)</h2><table><tr><th>Step</th><th class="num">Seconds</th></tr>{timing}</table>
<p class="muted">Test plan: docs/TEST_PLAN.md · Every check: e2e/REPORT.md · Real-model evaluation: evals/results.md</p>
</main></body></html>"""
    md = [f"# Settle test summary ({time.strftime('%Y-%m-%d %H:%M')})", "",
          f"- Test-plan cases: **{passed}/{len(cases) - measured} pass**" + (f" ({measured} measured in evals)" if measured else ""),
          f"- Unit and API tests: **{u_ok}/{len(unit) - u_skip}** ({unit_secs:.0f} s)",
          f"- Browser checks: **{e_ok}/{len(chk)}** ({e2e_secs:.0f} s, model replies: {mode})", "",
          "| Area | Cases passing |", "|---|---|"]  # fmt: skip
    md += [f"| {a} | {sum(c['status'] == 'pass' for c in cs)}/{sum(c['status'] != 'measured' for c in cs)} |" for a, cs in areas.items()]  # fmt: skip
    md += ["", "Failing cases: " + (", ".join(c["id"] for c in failed) or "none")]
    return page, "\n".join(md) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="browser test against the real model")
    ap.add_argument("--publish", action="store_true", help="copy the report to docs/")
    ap.add_argument("--no-browser", action="store_true", help="unit tests only (reuse the last browser run)")  # fmt: skip
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    unit, unit_secs = run_unit()
    if args.no_browser and (OUT / "e2e.json").exists():
        e2e, e2e_secs = json.loads((OUT / "e2e.json").read_text()), 0.0
    else:
        e2e, e2e_secs = run_e2e("live" if args.live else "replay")
    cases = plan()
    mark(cases, unit, e2e["checks"])
    page, md = render(cases, unit, e2e, unit_secs, e2e_secs)
    (OUT / "test-report.html").write_text(page)
    (OUT / "test-summary.md").write_text(md)
    if args.publish:
        shutil.copy(OUT / "test-report.html", ROOT / "docs" / "test-report.html")
    print(md)
    print(f"Report: {OUT / 'test-report.html'}")
    bad = any(c["status"] == "fail" for c in cases) or not all(v["ok"] for v in unit.values()) or not all(x["ok"] for x in e2e["checks"])  # fmt: skip
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
