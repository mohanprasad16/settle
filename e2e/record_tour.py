"""Records the landing page's product tour by driving the real app in Chrome.

    uv run python -m e2e.record_tour     # writes app/static/tour.{mp4,jpg} and docs/tour.gif

Real server, real sample files, the real model for the scanned PDF and every Settle AI answer, so
every figure on screen is what the app actually produced. Captions read their numbers off the
page. Six chapters (bring your data, see every rupee, ask Settle AI, fix with approval, answer
customers, hand it to the auditor); a cursor with a click ripple, and the page eases in on what a caption is about.
Re-run after UI changes.
Needs ffmpeg (the raw WebM is ~25 MB, too heavy to serve). Only the waits for the model are
sped up, and each says so on screen with its real length: "AI working: N s real time".
"""

import csv
import json
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
FRAMES = Path("/tmp/tour_v3")  # stills for review; not part of the app
TRIM = 0.6  # encode() skips the blank first moment before the page paints
SPED = 1.6  # each AI wait plays in about this many seconds

OVERLAY = """
(() => {
  const css = `
    #tour-cap { position: fixed; left: 50%; bottom: 30px; transform: translate(-50%, 8px); z-index: 99999;
      display: flex; align-items: center; gap: 12px; max-width: 1080px;
      background: rgba(15, 23, 42, .74); -webkit-backdrop-filter: blur(14px) saturate(1.4); backdrop-filter: blur(14px) saturate(1.4);
      color: #fff; font: 600 22px/1.35 Inter, system-ui, sans-serif; padding: 12px 22px 12px 14px; border-radius: 999px;
      border: 1px solid rgba(255,255,255,.14); box-shadow: 0 10px 30px rgba(0,0,0,.25);
      transition: opacity .3s, transform .3s; opacity: 0; pointer-events: none; }
    #tour-cap.on { opacity: 1; transform: translate(-50%, 0); }
    #tour-cap i { flex: none; width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center;
      background: rgba(255,255,255,.14); font-style: normal; font-size: 16px; }
    #tour-cap.ai i { background: linear-gradient(135deg, #3b5bff, #8b5cf6); }
    #tour-cur { position: fixed; left: 0; top: 0; width: 26px; height: 26px; z-index: 100001; pointer-events: none;
      transition: transform .75s cubic-bezier(.4,0,.2,1); filter: drop-shadow(0 2px 3px rgba(0,0,0,.35)); }
    .tour-ripple { position: fixed; z-index: 100000; width: 44px; height: 44px; margin: -22px 0 0 -22px; border-radius: 50%;
      border: 3px solid rgba(91, 76, 255, .85); pointer-events: none; animation: tour-rip .55s ease-out forwards; }
    @keyframes tour-rip { from { transform: scale(.3); opacity: 1; } to { transform: scale(1.5); opacity: 0; } }
    #tour-chap, #tour-end { position: fixed; inset: 0; z-index: 100002; display: grid; place-content: center; text-align: center;
      background: radial-gradient(1200px 600px at 70% 20%, rgba(139,92,246,.35), transparent 60%),
                  radial-gradient(900px 500px at 15% 90%, rgba(59,91,255,.35), transparent 60%), #0b1220;
      color: #fff; font-family: Inter, system-ui, sans-serif; opacity: 0; transition: opacity .3s; }
    #tour-chap.on, #tour-end.on { opacity: 1; }
    #tour-chap small { display: block; font: 600 20px Inter, system-ui, sans-serif; color: #b9c2ff; margin-bottom: 10px; letter-spacing: .02em; }
    #tour-chap b { display: block; font: 700 64px/1.05 Inter, system-ui, sans-serif; letter-spacing: -.03em; }
    #tour-end b { display: block; font: 700 72px Inter, system-ui, sans-serif; letter-spacing: -.03em; margin-bottom: 14px; }
    #tour-end span { font: 500 26px Inter, system-ui, sans-serif; color: #c7cde0; }
    #main { transition: transform .8s cubic-bezier(.4,0,.2,1); }
    section.tour { display: none !important; }  /* don't film the video slot inside the video */`;
  const CUR = `<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2l15 11-6.6.9 3.8 7.2-2.6 1.3-3.8-7.3L4 20z" fill="#fff" stroke="#0f172a" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
  const add = () => {
    if (document.getElementById("tour-cap")) return;
    const s = document.createElement("style"); s.textContent = css; document.head.append(s);
    const c = document.createElement("div"); c.id = "tour-cap"; c.innerHTML = "<i></i><span></span>"; document.body.append(c);
    const k = document.createElement("div"); k.id = "tour-cur"; k.innerHTML = CUR; document.body.append(k);
    k.style.transform = "translate(683px, 384px)";
  };
  const full = (id, html) => { add(); const e = document.createElement("div"); e.id = id; e.innerHTML = html;
    document.body.append(e); requestAnimationFrame(() => e.classList.add("on")); return e; };
  window.__tour = {
    cap(t, icon = "•", ai = false) { add(); const c = document.getElementById("tour-cap");
      c.querySelector("span").textContent = t; c.querySelector("i").textContent = icon; c.classList.toggle("ai", ai); c.classList.toggle("on", !!t); },
    cur(x, y) { add(); document.getElementById("tour-cur").style.transform = `translate(${x - 4}px, ${y - 2}px)`; },
    ripple(x, y) { const r = document.createElement("div"); r.className = "tour-ripple"; r.style.left = x + "px"; r.style.top = y + "px";
      document.body.append(r); setTimeout(() => r.remove(), 600); },
    zoom(c, s) { const m = document.getElementById("main"); const b = m.getBoundingClientRect();
      if (!c) { m.style.transform = ""; return; }
      m.style.transformOrigin = `${c.x - b.left}px ${c.y - b.top}px`;
      m.style.transform = `translate(${innerWidth / 2 - c.x}px, ${innerHeight / 2 - c.y}px) scale(${s})`; },
    chap(n, title) { const e = full("tour-chap", `<div><small></small><b></b></div>`);
      e.querySelector("small").textContent = `${n} of 6`; e.querySelector("b").textContent = title; },
    unchap() { const e = document.getElementById("tour-chap"); if (!e) return; e.classList.remove("on"); setTimeout(() => e.remove(), 320); },
    end(title, line) { const e = full("tour-end", `<div><b></b><span></span></div>`);
      e.querySelector("b").textContent = title; e.querySelector("span").textContent = line; },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", add); else add();
})();
"""

SPARK = "✦"


class Tour:
    def __init__(self, page):
        self.page = page
        self.t0 = time.perf_counter()  # the recording starts with the page
        self.fast: list[tuple[float, float]] = []  # AI waits, seconds into the recording

    def now(self) -> float:
        return time.perf_counter() - self.t0

    def ai_wait(self, done) -> None:
        """Wait for the model under a live counter of real seconds; encode() speeds it up."""
        self.page.evaluate("""() => { const s = Date.now(); const tick = () => window.__tour.cap(
            `AI working: ${Math.round((Date.now() - s) / 1000)} s real time (long waits sped up)`, "⏩", true); tick();
            window.__aiTimer = setInterval(tick, 250); }""")
        a = self.now()
        done()
        self.wait(0.3)  # let the counter show its final value
        self.page.evaluate("clearInterval(window.__aiTimer)")
        # Leave ~0.7 s at normal speed at the start so the label can be read.
        self.fast.append((a + 0.7, self.now()))

    def cap(self, text: str, hold: float = 0, icon: str = "•", ai: bool = False) -> None:
        self.page.evaluate("([t, i, a]) => window.__tour.cap(t, i, a)", [text, icon, ai])
        self.wait(hold)

    def chapter(self, n: int, title: str) -> None:
        self.cap("")
        self.page.evaluate("([n, t]) => window.__tour.chap(n, t)", [n, title])
        self.wait(1.3)
        self.page.evaluate("window.__tour.unchap()")
        self.wait(0.35)

    def wait(self, s: float) -> None:
        self.page.wait_for_timeout(int(s * 1000))

    def centre(self, loc):
        b = loc.bounding_box()
        return {"x": b["x"] + b["width"] / 2, "y": b["y"] + b["height"] / 2} if b else None

    def show(self, loc, hold: float = 0.8, scroll: bool = True) -> None:
        """Scroll the element into view and glide the cursor to it."""
        if scroll:
            loc.evaluate("e => e.scrollIntoView({behavior: 'smooth', block: 'center'})")
            self.wait(0.8)
        c = self.centre(loc)
        if c:
            self.page.evaluate("c => window.__tour.cur(c.x, c.y)", c)
        self.wait(hold)

    def zoom(self, loc, scale: float = 1.35, hold: float = 1.6) -> None:
        """Ease the page in on an element, hold, ease back out."""
        c = self.centre(loc)
        if c:
            self.page.evaluate("([c, s]) => window.__tour.zoom(c, s)", [c, scale])
            self.wait(0.85 + hold)
            MARKS.setdefault("zoom", self.now() - 0.3)
            self.page.evaluate("window.__tour.zoom(null)")
            self.wait(0.85)

    def click(self, loc, hold: float = 0.6, scroll: bool = True) -> None:
        self.show(loc, 0.5, scroll)
        c = self.centre(loc)
        if c:
            self.page.evaluate("c => window.__tour.ripple(c.x, c.y)", c)
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
            MARKS.clear()
            url = f"http://127.0.0.1:{PORT}"
            page.goto(url)
            page.wait_for_selector("#try-sample")
            page.wait_for_timeout(500)
            CUT[0] = t.now()  # the film starts here; the blank first paint is cut

            # 1. Bring your data: connect, or upload three files (a scanned PDF is read and checked).
            t.chapter(1, "Bring your data")
            MARKS["chapter"] = t.now() - 1.2
            t.click(page.locator("#open-connect"), 0.6)
            page.wait_for_selector("#connections:not([hidden])")
            t.cap("Connect Razorpay, your bank and your books: read-only, with your consent", 2.8)
            t.show(page.locator("#demo-sync"), 1.4)
            t.cap("")
            t.click(page.locator("#conn-back"), 0.5)
            t.click(page.locator("#go-upload"), 0.6)
            t.cap("Or upload three files you already have. This bank statement is a scanned PDF.")
            page.set_input_files("#f-razorpay", str(SAMPLE / "razorpay_settlements.csv"))
            page.set_input_files("#f-bank", str(SAMPLE / "bank_statement_scanned.pdf"))
            page.set_input_files("#f-orders", str(SAMPLE / "orders.csv"))
            t.show(page.locator(".slots"), 1.8)
            MARKS["data_in"] = t.now() - 0.6
            t.cap("")
            t.click(page.locator("#upload-form button[type=submit]"), 0)
            t.ai_wait(lambda: page.wait_for_selector("#review-summary .banner", timeout=120_000))
            t.cap("The AI reads it; code checks every running balance before you use it")
            t.zoom(page.locator("#review-summary"), 1.25, 1.6)
            # The model sometimes misreads a digit on the scan. Then the row waits for a person, and
            # the film shows that: the outlined box is corrected from the statement, on camera.
            broken = page.locator("#review-table tr.broken")
            if broken.count():
                truth = list(
                    csv.DictReader((SAMPLE / "bank_statement.csv").read_text().splitlines())
                )
                t.cap("A row didn't add up, so it waits for a person, who fixes it from the PDF")
                for i in range(broken.count()):
                    row = broken.nth(i)
                    n = int(row.locator("td").first.inner_text())
                    t.show(row, 1.0)
                    for k in ("debit", "credit", "balance"):
                        box = row.locator(f"input[data-k={k}]")
                        if box.input_value().replace("₹", "").replace(",", "") != truth[n - 1][k]:
                            t.click(box, 0.2, scroll=False)
                            box.fill("")
                            box.type(truth[n - 1][k], delay=50)
                    t.wait(0.8)
            t.cap("")
            start_t = time.perf_counter()
            t.click(page.locator("#review-go"), 0)
            page.wait_for_selector("#results:not([hidden]) .statement", timeout=60_000)
            secs = time.perf_counter() - start_t  # the app's real work, confirm to results

            # 2. See every rupee
            t.chapter(2, "See every rupee")
            n = page.evaluate("async () => (await (await fetch('/api/result')).json()).totals.rows")
            rows = f"{n['razorpay'] + n['orders'] + n['bank']:,}"
            took = "in under a second" if secs < 1 else f"in {secs:.1f} seconds"
            lost = page.locator(".statement .loss .fig").inner_text()
            unexpl = page.locator(".statement .tied .fig").inner_text()
            t.cap(f"{rows} rows reconciled {took}. {lost} lost, and Unexplained {unexpl}")
            t.show(page.locator(".statement"), 3.2)
            t.cap("Books → payment gateway → bank, every rupee on a named line")
            page.locator("section[aria-labelledby=wf-h]").evaluate("e => e.scrollIntoView({behavior: 'smooth', block: 'center'})")  # fmt: skip
            t.wait(0.9)
            t.zoom(page.locator("section[aria-labelledby=wf-h] .wf"), 1.3, 2.4)

            # 3. Ask Settle AI, from what you're looking at
            t.chapter(3, "Ask Settle AI")
            page.locator(".statement").evaluate("e => e.scrollIntoView({block: 'center'})")
            t.cap("Every figure has a ✦ Ask. It opens Settle AI with that figure as context", icon=SPARK, ai=True)  # fmt: skip
            page.locator("[data-kpi=loss]").hover()
            t.click(page.locator("[data-kpi=loss] .ask-ai"), 1.6, scroll=False)
            t.cap("")
            page.locator("#cp-q").press("Enter")
            t.ai_wait(lambda: page.locator("#cp-log .cp-turn").first.wait_for(timeout=120_000))
            page.wait_for_function("() => !cp.busy", timeout=30_000)
            turn = page.locator("#cp-log .cp-turn").first
            ok = turn.locator(".badge.ok").count() > 0  # the caption says what the badge says
            t.cap("Answers cite the rows they used, and every figure was checked" if ok else "Answers cite their rows; a figure it can't verify is flagged, never hidden", icon=SPARK, ai=True)  # fmt: skip
            t.show(turn.locator(".cp-meta"), 2.4, scroll=False)
            MARKS["poster"] = t.now()
            MARKS["overview_ai"] = t.now()
            t.click(turn.locator("button[aria-label='Good answer']"), 1.0, scroll=False)
            t.cap("Rate it 👍 or 👎. That's how the test set grows", icon=SPARK, ai=True)
            t.show(turn.locator(".cp-next"), 2.4, scroll=False)
            page.locator("#copilot header button[aria-label='Close Settle AI']").click()
            t.wait(0.4)
            # ⌘K: jump anywhere, or ask from anywhere.
            t.cap("⌘K: jump anywhere, or ask Settle AI from anywhere", icon="⌘")
            page.keyboard.press("Control+k")
            page.wait_for_selector("#palette[open]")
            t.wait(0.5)
            page.keyboard.type("21 oct", delay=90)
            t.wait(1.3)
            page.fill("#palette-q", "")
            page.keyboard.type("Why is the 21 Oct payout short?", delay=45)
            t.show(page.locator("#palette-list li.po-ai"), 1.0, scroll=False)
            t.cap("")
            page.keyboard.press("Enter")
            t.ai_wait(lambda: page.wait_for_function("() => document.querySelectorAll('#cp-log .cp-turn').length >= 2 && !cp.busy", timeout=120_000))  # fmt: skip
            t.cap("The same answer checks, from any page", icon=SPARK, ai=True)
            t.show(page.locator("#cp-log .cp-turn").last.locator(".cp-text"), 2.2, scroll=False)
            page.locator("#copilot header button[aria-label='Close Settle AI']").click()
            t.wait(0.3)

            # 4. Fix with approval: tick the line nothing matches, Settle AI prepares, a person allows.
            t.chapter(4, "Fix with approval")
            t.click(page.locator("#nav-patterns"), 0.4)
            pick = page.locator("#exc-list input.pick-row").first
            t.cap("A bank credit nothing matches. Tick it and ask Settle AI to reconcile it")
            t.show(pick, 0.6)
            pick.check(force=True)
            t.wait(0.5)
            t.click(page.locator("#cp-launch"), 0.4, scroll=False)
            t.click(page.locator("#cp-new"), 0.6, scroll=False)  # back to the home view
            t.cap("")
            t.click(page.locator("#cp-reconcile"), 0, scroll=False)
            t.ai_wait(lambda: page.wait_for_function("() => document.querySelector('#cp-log .change') && !cp.busy", timeout=120_000))  # fmt: skip
            card = page.locator("#cp-log .change").last
            t.cap("It prepares the change: before → after, and the checks it passed", icon=SPARK, ai=True)  # fmt: skip
            t.show(card, 3.0)
            MARKS["change_card"] = t.now() - 0.8
            card.locator(".cc-name").fill("")
            card.locator(".cc-name").type("Priya, Finance", delay=55)
            t.cap("Nothing happens until a named person allows it")
            t.click(card.locator("button", has_text="Allow once"), 0)
            page.locator("#cp-log .change .banner.okb").last.wait_for(timeout=60_000)
            unexpl = page.evaluate("rupees(state.view.totals.unexplained)")
            t.cap(f"Allowed. Code applies it, the month reconciles again: Unexplained {unexpl}")
            t.show(page.locator("#cp-log .change").last, 3.0, scroll=False)
            page.locator("#copilot header button[aria-label='Close Settle AI']").click()
            t.wait(0.3)
            t.cap("")
            t.click(page.locator("#nav-settings"), 0.4)
            page.wait_for_selector("#pol-kinds")
            t.cap(
                "Your approval policy decides what may run alone. Code enforces it, not the model"
            )
            t.zoom(page.locator("#pol-kinds"), 1.2, 1.8)

            # 5. Answer customers: a "where's my refund?" message, matched by code, answered from facts.
            t.chapter(5, "Answer customers")
            t.click(page.locator("#nav-desk"), 0.3)
            stuck = next(m for m in json.loads((SAMPLE / "inbox.json").read_text()) if "Was it really sent" in m["text"])  # fmt: skip
            t.cap("A customer asks where their refund is. Paste it into Desk (Gmail and WhatsApp connect next)")
            t.click(page.locator("#paste-text"), 0.2)
            page.locator("#paste-text").type(stuck["text"], delay=12)
            page.locator("#paste-name").type(stuck["from"]["name"], delay=30)
            t.wait(0.4)
            t.cap("")
            t.click(page.locator("#paste-go"), 0, scroll=False)
            t.ai_wait(lambda: page.wait_for_function("() => location.hash === '#/desk/P1' && document.querySelector('#reply-body')", timeout=120_000))  # fmt: skip
            t.cap("Code finds the order: the refund was recorded but never went out. The issue and its fix are linked")
            t.show(page.locator("#ticket .linked"), 2.6)
            MARKS["desk"] = t.now() - 0.6
            ok = "every figure checked" in page.inner_text("#ticket .reply-meta")
            t.cap("The AI writes the reply from those facts, every figure checked" if ok else "The reply is written by code from those facts", icon=SPARK if ok else "•", ai=ok)  # fmt: skip
            t.show(page.locator("#reply-body"), 2.4)
            t.cap("")
            t.click(page.locator("#ticket-send"), 0, scroll=False)
            page.locator("#ticket-card .change").wait_for(timeout=30_000)
            t.cap("Sending is a change card: nothing reaches the customer until a named person allows it")
            t.show(page.locator("#ticket-card .change"), 2.6)

            # 6. Hand it to the auditor
            t.chapter(6, "Hand it to the auditor")
            page.goto(f"{url}/api/report")
            page.wait_for_load_state()
            t.cap("An audit report with every decision and who made it, plus the BRS, Tally journal and Excel pack")  # fmt: skip
            t.wait(1.4)
            page.mouse.wheel(0, 600)
            t.wait(2.4)
            t.cap("")
            page.evaluate(
                "window.__tour.end('Settle', 'Account for every rupee · github.com/mohanprasad16/settle')"
            )
            t.wait(0.4)
            MARKS["end"] = t.now()
            t.wait(2.4)

            FAST.extend(t.fast)
            video = page.video.path()
            ctx.close()
            browser.close()
            return Path(video)
    finally:
        server.terminate()


FAST: list[tuple[float, float]] = []
CUT = [TRIM]  # where the film starts, seconds into the recording
MARKS: dict[str, float] = {}  # named moments (poster, report frames), seconds into the recording


def film_time(raw: float) -> float:
    """Where a moment of the raw recording lands in the encoded film, after the AI speed-ups."""
    out = raw - CUT[0]
    for a, b in sorted(FAST):
        if b - a > SPED + 0.5 and a < raw:
            gone = min(raw, b) - a
            out -= gone - gone * SPED / (b - a)
    return out


def speedup_filter(fast: list[tuple[float, float]], trim: float) -> str:
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
    subprocess.run([*ff, "-ss", f"{CUT[0]:.2f}", "-i", str(webm), "-filter_complex", speedup_filter(FAST, CUT[0]), "-map", "[out]",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(STATIC / "tour.mp4")], check=True)  # fmt: skip
    # The poster: the Overview with Settle AI open on a checked answer.
    subprocess.run([*ff, "-ss", f"{film_time(MARKS['poster']):.2f}", "-i", str(STATIC / "tour.mp4"), "-frames:v", "1", "-q:v", "3", str(STATIC / "tour.jpg")], check=True)  # fmt: skip
    FRAMES.mkdir(parents=True, exist_ok=True)
    for name in ["chapter", "data_in", "zoom", "overview_ai", "change_card", "desk", "end"]:
        if name in MARKS:
            subprocess.run([*ff, "-ss", f"{film_time(MARKS[name]):.2f}", "-i", str(STATIC / "tour.mp4"), "-frames:v", "1", "-q:v", "3", str(FRAMES / f"tour_{name}.jpg")], check=True)  # fmt: skip
    gif = ROOT / "docs" / "tour.gif"
    vf = "fps=8,scale=760:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer"
    subprocess.run([*ff, "-ss", "0", "-t", "15", "-i", str(STATIC / "tour.mp4"), "-vf", vf, str(gif)], check=True)  # fmt: skip


if __name__ == "__main__":
    webm = record()
    encode(webm)
    print("AI waits sped up:", ", ".join(f"{b - a:.0f} s" for a, b in FAST))
    length = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(STATIC / "tour.mp4")], capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    print(f"film length: {float(length):.0f} s")
    for f in [STATIC / "tour.mp4", STATIC / "tour.jpg", ROOT / "docs" / "tour.gif"]:
        if f.exists():
            print(f"{f.relative_to(ROOT)}: {f.stat().st_size / 1e6:.1f} MB")
