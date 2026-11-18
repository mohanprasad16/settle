"""Smoke test a running Settle (a container, a deploy) through its API and in real Chrome.

    uv run python -m e2e.smoke http://localhost:8790

The reviewer's path: health, the sample month to ₹0 unexplained, an issue, a random month, an
upload, a plain error, every download, and the same in a browser with no console errors.
Prints PASS/FAIL per check; exit 1 if any failed.
"""

import io
import sys
import zipfile
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"
fails = 0


def check(name: str, ok: bool, detail="") -> None:
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} {name}{'' if ok else f': {detail}'}")


def api(base: str) -> None:
    c = httpx.Client(base_url=base, timeout=60)
    h = c.get("/api/health").json()
    check("health answers", h.get("ok") is True, h)
    v = c.post("/api/sample").json()
    check("sample month: ₹0.00 unexplained", v["totals"]["unexplained"] == 0, v["totals"])
    f = v["findings"][0]["id"]
    d = c.get(f"/api/finding/{f}").json()
    check("an issue opens with an explanation", bool(d.get("explanation")), d.keys())
    ch = c.post("/api/chaos").json()
    check("random month: ₹0.00 unexplained", ch["totals"]["unexplained"] == 0, ch["totals"])
    files = {k: (f"{k}.csv", (SAMPLE / n).read_bytes(), "text/csv") for k, n in
             {"razorpay": "razorpay_settlements.csv", "bank": "bank_statement.csv", "orders": "orders.csv"}.items()}  # fmt: skip
    up = c.post("/api/upload", files=files)
    check("upload three CSVs reconciles", up.status_code == 200 and up.json()["totals"]["unexplained"] == 0, up.text[:200])  # fmt: skip
    bad = c.post("/api/upload", files={"bank": files["orders"]})
    check("a wrong upload is a plain error, no stack trace", bad.status_code >= 400 and "Traceback" not in bad.text, bad.text[:200])  # fmt: skip
    z = zipfile.ZipFile(io.BytesIO(c.get("/api/export").content))
    check("CSV pack downloads", "findings.csv" in z.namelist(), z.namelist())
    check("audit report downloads", "Sign-off" in c.get("/api/report").text)
    check("Tally journal downloads", c.get("/api/journal").text.startswith("Date,"))
    check("BRS downloads", c.get("/api/brs").status_code == 200)


def browser(base: str) -> None:
    from e2e.test_system import wait_results

    errors = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome")
        page = b.new_page(viewport={"width": 1366, "height": 768})
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base)
        page.locator("#try-sample").wait_for(timeout=15000)  # a remote host draws later than localhost
        check("landing page loads", page.locator("#try-sample").is_visible())
        page.click("#try-sample")
        wait_results(page)
        check("browser: results show ₹0.00 unexplained", "₹0.00" in page.locator("#results").inner_text())  # fmt: skip
        page.click("#nav-issues")
        page.locator("#page-issues button.entry:visible").first.click()
        page.wait_for_selector("#drawer-title")
        check("browser: an issue drawer opens", bool(page.locator("#drawer-title").inner_text()))
        check("browser: no console or page errors", not errors, errors[:3])
        b.close()


if __name__ == "__main__":
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
    api(base)
    browser(base)
    print(f"{'OK' if not fails else f'{fails} FAILED'} against {base}")
    sys.exit(1 if fails else 0)
