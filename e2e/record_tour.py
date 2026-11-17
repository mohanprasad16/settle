"""Records the landing page's product tour by driving the real app in Chrome.

    uv run python -m e2e.record_tour     # writes app/static/tour.{mp4,jpg} and docs/tour.gif

Real server, real sample, real model for the Ask answer and the scanned PDF, so every figure on
screen is what the app actually produced. Captions read their numbers off the page. A soft dot
stands in for the mouse and a ring marks what each caption is about. Re-run after UI changes.
Needs ffmpeg (the raw WebM is ~25 MB, too heavy to serve). Only the waits for the model are
sped up, and each says so on screen with its real length: "⏩ AI working (N s, sped up)".
"""

import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from e2e.test_system import ROOT, SAMPLE, start

PORT = 8790
SIZE = {"width": 1366, "height": 768}
STATIC = ROOT / "app" / "static"
OUT = ROOT / "e2e" / "tour_raw"
TRIM = 0.6  # encode() skips the blank first moment before the page paints
SPED = 1.6  # each AI wait plays in about this many seconds

OVERLAY = """
(() => {
  const css = `
    #tour-cap { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%); z-index: 99999;
      background: rgba(20,32,56,.94); color: #fff; font: 600 24px/1.35 system-ui, sans-serif;
      padding: 14px 26px; border-radius: 12px; max-width: 1100px; text-align: center;
      box-shadow: 0 8px 30px rgba(0,0,0,.25); transition: opacity .35s; opacity: 0; pointer-events: none; }
    #tour-dot { position: fixed; left: 0; top: 0; width: 22px; height: 22px; margin: -11px 0 0 -11px;
      border-radius: 50%; background: rgba(231,76,60,.85); border: 3px solid #fff; z-index: 99999;
      box-shadow: 0 2px 8px rgba(0,0,0,.35); transition: transform .7s cubic-bezier(.4,0,.2,1); pointer-events: none; }
    #tour-ring { position: fixed; z-index: 99998; border: 3px solid rgba(231,76,60,.75); border-radius: 10px;
      box-shadow: 0 0 0 6px rgba(231,76,60,.15); transition: all .5s ease; opacity: 0; pointer-events: none; }
    #tour-end { position: fixed; inset: 0; z-index: 100000; background: #142038; color: #fff;
      display: grid; place-content: center; text-align: center; font: 600 28px system-ui, sans-serif; }
    #tour-end b { display: block; font: 700 64px Georgia, serif; margin-bottom: 14px; }
    section.tour { display: none !important; }  /* don't film the video slot inside the video */`;
  const add = () => {
    if (document.getElementById("tour-cap")) return;
    const s = document.createElement("style"); s.textContent = css; document.head.append(s);
    for (const id of ["tour-cap", "tour-dot", "tour-ring"]) {
      const d = document.createElement("div"); d.id = id; document.body.append(d);
    }
    document.getElementById("tour-dot").style.transform = "translate(683px, 384px)";
  };
  window.__tour = {
    cap(t) { add(); const c = document.getElementById("tour-cap"); c.textContent = t; c.style.opacity = t ? 1 : 0; },
    dot(x, y) { add(); document.getElementById("tour-dot").style.transform = `translate(${x}px, ${y}px)`; },
    ring(r) { add(); const g = document.getElementById("tour-ring");
      if (!r) { g.style.opacity = 0; return; }
      Object.assign(g.style, { left: r.x - 8 + "px", top: r.y - 8 + "px", width: r.w + 16 + "px", height: r.h + 16 + "px", opacity: 1 }); },
    end(title, line) { add(); const e = document.createElement("div"); e.id = "tour-end";
      e.innerHTML = `<div><b></b><span></span></div>`; e.querySelector("b").textContent = title;
      e.querySelector("span").textContent = line; document.body.append(e); },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", add); else add();
})();
"""


class Tour:
    def __init__(self, page):
        self.page = page
        self.t0 = time.perf_counter()  # the recording starts with the page
        self.fast: list[tuple[float, float]] = []  # AI waits, seconds into the recording

    def ai_wait(self, done) -> None:
        """Wait for the model under a live counter of real seconds; encode() speeds it up."""
        self.page.evaluate("""() => { const s = Date.now(); const tick = () => window.__tour.cap(
            `⏩ AI working (${Math.round((Date.now() - s) / 1000)} s, sped up)`); tick();
            window.__aiTimer = setInterval(tick, 250); }""")
        a = time.perf_counter() - self.t0
        done()
        self.wait(0.3)  # let the counter show its final value
        self.page.evaluate("clearInterval(window.__aiTimer)")
        # Leave ~0.7 s at normal speed at the start so the label can be read.
        self.fast.append((a + 0.7, time.perf_counter() - self.t0))

    def cap(self, text: str, hold: float = 0) -> None:
        self.page.evaluate("t => window.__tour.cap(t)", text)
        self.wait(hold)

    def wait(self, s: float) -> None:
        self.page.wait_for_timeout(int(s * 1000))

    def show(self, loc, hold: float = 0.8, ring: bool = True) -> None:
        """Scroll the element into view, glide the dot to it and ring it."""
        loc.evaluate("e => e.scrollIntoView({behavior: 'smooth', block: 'center'})")
        self.wait(0.9)
        b = loc.bounding_box()
        if b:
            self.page.evaluate("p => window.__tour.dot(p.x, p.y)", {"x": b["x"] + b["width"] / 2, "y": b["y"] + b["height"] / 2})  # fmt: skip
            r = {"x": b["x"], "y": b["y"], "w": b["width"], "h": b["height"]}
            self.page.evaluate("r => window.__tour.ring(r)", r if ring else None)
        self.wait(hold)

    def click(self, loc, hold: float = 0.6) -> None:
        self.show(loc, 0.5)
        self.page.evaluate("window.__tour.ring(null)")
        # ponytail: force skips Playwright's stability wait, which a smooth scroll can starve.
        loc.click(force=True)
        self.wait(hold)


def record() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.webm"):
        old.unlink()
    server = start(PORT)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            ctx = browser.new_context(
                viewport=SIZE, record_video_dir=str(OUT), record_video_size=SIZE
            )
            ctx.add_init_script(OVERLAY)
            page = ctx.new_page()
            t = Tour(page)
            FAST.clear()
            url = f"http://127.0.0.1:{PORT}"
            page.goto(url)
            page.wait_for_selector("#try-sample")

            # 1. Landing
            t.cap(
                "Your books, your payment gateway and your bank never match. Settle finds out why."
            )
            t.show(page.locator(".hero h1"), 3.2)

            # 2. Sample
            t.cap("")
            # Time only the app's work: from the real click to the results, not the dot's glide.
            t.show(page.locator("#try-sample"), 0.5)
            page.evaluate("window.__tour.ring(null)")
            start_t = time.perf_counter()
            page.locator("#try-sample").click(force=True)
            page.wait_for_selector("#results:not([hidden]) .statement")
            secs = time.perf_counter() - start_t
            n = page.evaluate("async () => (await (await fetch('/api/result')).json()).totals.rows")
            rows = f"{n['razorpay'] + n['orders'] + n['bank']:,}"
            took = "in under a second" if secs < 1 else f"in {secs:.1f} seconds"
            t.cap(f"{rows} rows reconciled {took}")
            t.show(page.locator(".statement"), 2.4)

            # 3. Summary
            lost = page.locator(".statement .loss .fig").inner_text()
            unexpl = page.locator(".statement .tied .fig").inner_text()
            t.cap(f"{lost} lost · Unexplained {unexpl}: every rupee lands on a named line")
            t.show(page.locator("[data-kpi=unexplained]"), 2.6)

            # 4. The bridge
            t.cap("Books → payment gateway → bank, closed to the paisa")
            t.show(page.locator("section[aria-labelledby=wf-h]"), 3.2)

            # 5. Guidance
            t.cap("A first-time user knows what to do: three steps, ticked off as you go")
            t.show(page.locator("#start-here"), 2.8)

            # 5b. Settlements, payout by payout
            value = page.locator("[data-kpi=value] .fig").inner_text()
            t.cap("")
            t.click(page.locator("#nav-settlements"), 0)
            page.wait_for_selector("#settlement-rows tr[data-status]")
            missing = page.locator("#set-filters .chip[data-status=missing] .n")
            gone = f" · {missing.inner_text()} never reached the bank" if missing.count() else ""
            t.cap(f"Every payout against your bank: {value} reconciled by value{gone}")
            t.show(page.locator("#settlement-list"), 1.4, ring=False)
            row = page.locator("#settlement-rows tr[data-status=missing]")
            if row.count():
                t.show(row.first, 2.0)
            t.click(page.locator("#nav-overview"), 0.4)

            # 6. The biggest issue
            t.cap("")
            t.click(page.locator("#page-overview .page-head .btn.primary"), 1.0)
            page.wait_for_selector("#tab-summary h3")
            t.cap("Plain-English reason, proof rows, and where the money stopped")
            t.show(page.locator("#tab-summary h3").first, 1.8)
            t.click(page.locator("#t-trace"), 0.3)
            t.show(page.locator("#tab-trace ol.chain"), 1.8)
            t.click(page.locator("#t-evidence"), 0.3)
            t.show(page.locator("#tab-evidence table.ev").first, 1.6)

            # 7. Approve a fix with a name
            t.click(page.locator("#t-fix"), 0.3)
            card = page.locator("#drawer-sheet .action").first
            t.cap("Nothing moves money without a named approver")
            t.show(card, 1.0)
            page.locator("#approver").fill("")
            t.click(page.locator("#approver"), 0.2)
            page.locator("#approver").type("Priya, Finance", delay=70)
            t.click(card.locator("button.btn.ok"), 2.6)
            page.keyboard.press("Escape")
            t.wait(0.6)

            # Each caption is set only after its screen is showing, and cleared between beats.
            # 8. Ask (real model; the wait is real too)
            t.cap("")
            t.click(page.locator("#nav-ask"), 0.4)
            t.click(page.locator("#page-ask .suggest button", has_text="Why did a settlement never reach my bank?"), 0)  # fmt: skip
            t.ai_wait(lambda: page.locator("#chat > .answer:not(.pending)").first.wait_for(timeout=90_000))  # fmt: skip
            t.cap("Ask anything. Every figure is checked against your files.")
            t.show(page.locator("#chat > .answer:not(.pending)").first, 3.4)

            # 8b. Copilot: select the line nothing matches, reconcile it with AI, a person confirms
            t.cap("")
            t.click(page.locator("#nav-patterns"), 0.4)
            pick = page.locator("#exc-list input.pick-row").first
            if pick.count():
                t.show(pick, 0.6)
                pick.check(force=True)
                t.cap("Something doesn't match? Select it and hand it to Copilot")
                t.click(page.locator("#sel-ai"), 0)
                t.ai_wait(lambda: page.locator("#cp-log .cp-a .badge, #cp-log .banner.err").last.wait_for(timeout=120_000))  # fmt: skip
                card = page.locator("#cp-log .change").last
                if card.count():
                    t.cap(
                        "Copilot prepares the change; nothing happens until a named person allows it"
                    )
                    t.show(card, 2.6)
                    card.locator(".cc-name").fill("")
                    card.locator(".cc-name").type("Priya, Finance", delay=60)
                    t.click(card.locator("button", has_text="Allow once"), 0)
                    page.locator("#cp-log .change .banner.okb").last.wait_for(timeout=60_000)
                    t.cap("Allowed: code applies it, the month re-reconciles, and it can be undone")
                    t.show(page.locator("#cp-log .change").last, 2.6)
                page.locator("#copilot header button[aria-label='Close Copilot']").click()
                t.wait(0.3)

            # 9. Scanned PDF
            t.cap("")
            t.click(page.locator("#nav-new"), 0.4)
            t.click(page.locator("#go-upload"), 0.6)
            page.set_input_files("#f-razorpay", str(SAMPLE / "razorpay_settlements.csv"))
            page.set_input_files("#f-bank", str(SAMPLE / "bank_statement_scanned.pdf"))
            page.set_input_files("#f-orders", str(SAMPLE / "orders.csv"))
            t.show(page.locator(".slot[data-slot=bank]"), 1.0)
            t.click(page.locator("#upload-form button[type=submit]"), 0)
            t.ai_wait(lambda: page.wait_for_selector("#review-summary .banner", timeout=120_000))
            t.cap("Scanned or locked bank PDFs: read by AI, verified by the balance chain")
            t.show(page.locator("#review-summary"), 2.6)
            t.cap("")
            t.click(page.locator("#review-go"), 0)
            page.wait_for_selector("#results:not([hidden]) .statement", timeout=60_000)
            t.wait(0.6)

            # 10. Chaos
            t.click(page.locator("#nav-chaos"), 0)
            page.wait_for_selector("#answer-key")
            key = page.locator("#answer-key")
            t.cap(f"A brand-new random month. {key.locator('p b').first.inner_text()}.")
            t.show(key.locator("h2"), 0.8)
            page.mouse.wheel(0, 300)
            t.wait(2.4)

            # 11. Audit report + end card
            t.cap("")
            page.goto(f"{url}/api/report")
            page.wait_for_load_state()
            t.cap("Audit-ready report, Tally journal, Excel pack")
            t.wait(1.2)
            page.mouse.wheel(0, 500)
            t.wait(2.2)
            t.cap("")
            page.evaluate(
                "window.__tour.end('Settle', 'Account for every rupee · github.com/mohanprasad16/settle')"
            )
            t.wait(3.0)

            FAST.extend(t.fast)
            video = page.video.path()
            ctx.close()
            browser.close()
            return Path(video)
    finally:
        server.terminate()


FAST: list[tuple[float, float]] = []


def speedup_filter(fast: list[tuple[float, float]], trim: float = TRIM) -> str:
    """ffmpeg filter that plays the recording at normal speed except the AI waits, each squeezed
    to about SPED seconds. Times are seconds into the raw recording."""
    parts, at = [], trim
    for a, b in sorted(fast):
        if b - a > SPED + 0.5:
            parts += [(at, a, 1.0), (a, b, (b - a) / SPED)]
            at = b
    parts.append((at, None, 1.0))
    chains = [f"[0:v]trim=start={s - trim:.2f}" + (f":end={e - trim:.2f}" if e else "") +
              f",setpts=(PTS-STARTPTS)/{f:.3f}[v{i}]" for i, (s, e, f) in enumerate(parts)]  # fmt: skip
    return ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0,fps=25[out]"  # fmt: skip


def encode(webm: Path) -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit(f"ffmpeg not found; the raw recording is at {webm}")
    ff = ["ffmpeg", "-y", "-loglevel", "error"]
    subprocess.run([*ff, "-ss", str(TRIM), "-i", str(webm), "-filter_complex", speedup_filter(FAST), "-map", "[out]",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(STATIC / "tour.mp4")], check=True)  # fmt: skip
    subprocess.run([*ff, "-ss", "8", "-i", str(STATIC / "tour.mp4"), "-frames:v", "1", "-q:v", "3", str(STATIC / "tour.jpg")], check=True)  # fmt: skip
    gif = ROOT / "docs" / "tour.gif"
    vf = "fps=8,scale=760:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer"
    subprocess.run([*ff, "-ss", "3", "-t", "15", "-i", str(STATIC / "tour.mp4"), "-vf", vf, str(gif)], check=True)  # fmt: skip


if __name__ == "__main__":
    webm = record()
    encode(webm)
    print("AI waits sped up:", ", ".join(f"{b - a:.0f} s" for a, b in FAST))
    for f in [STATIC / "tour.mp4", STATIC / "tour.jpg", ROOT / "docs" / "tour.gif"]:
        if f.exists():
            print(f"{f.relative_to(ROOT)}: {f.stat().st_size / 1e6:.1f} MB")
