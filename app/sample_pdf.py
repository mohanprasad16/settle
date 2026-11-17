"""Renders data/sample/bank_statement.csv as the PDFs a merchant actually gets from a bank.

    uv run python -m app.sample_pdf

Writes three files next to the CSV:
  bank_statement.pdf          digital, multi-page, header repeated on every page
  bank_statement_scanned.pdf  the same pages printed and scanned: image only, tilted, noisy
  bank_statement_locked.pdf   digital, AES-256 locked like Indian bank e-statements;
                              password KAAPI1234

Needs the dev dependencies (Playwright + Chrome, pypdfium2, Pillow). The app itself only reads.
"""

import csv
import html
import io
import random
from pathlib import Path

from app.money import rupees, to_paise

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"
PASSWORD = "KAAPI1234"
OPENING = 120000000  # paise; the balance before the first line of the CSV


def _amt(text: str) -> str:
    return rupees(to_paise(text)).replace("₹", "") if text else ""


def statement_html(rows: list[dict]) -> str:
    debit = sum(to_paise(r["debit"]) for r in rows)
    credit = sum(to_paise(r["credit"]) for r in rows)
    closing = to_paise(rows[-1]["balance"])
    body = "\n".join(
        f"<tr><td>{r['txn_date']}</td><td>{html.escape(r['narration'])}</td>"
        f"<td>{r['ref_no']}</td><td class=n>{_amt(r['debit'])}</td>"
        f"<td class=n>{_amt(r['credit'])}</td><td class=n>{_amt(r['balance'])}</td></tr>"
        for r in rows
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 14mm 12mm 16mm; }}
body {{ font: 9pt Helvetica, Arial, sans-serif; color: #111; }}
.top {{ display: flex; justify-content: space-between; border-bottom: 2px solid #1b4b72; }}
.bank {{ font-size: 17pt; font-weight: bold; color: #1b4b72; }}
.meta td {{ padding: 1px 10px 1px 0; }}
table.t {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
.t th {{ background: #1b4b72; color: #fff; padding: 4px; text-align: left; font-size: 8.5pt; }}
.t td {{ border-bottom: 1px solid #ccc; padding: 4px; vertical-align: top; }}
.t td:nth-child(2) {{ width: 38%; word-break: break-all; }}
.n {{ text-align: right; white-space: nowrap; }}
.sum {{ margin-top: 12px; width: 60%; margin-left: auto; }}
.sum td {{ padding: 3px 6px; border-bottom: 1px solid #ddd; }}
.foot {{ margin-top: 18px; font-size: 7.5pt; color: #555; }}
</style></head><body>
<div class=top><div><div class=bank>KAVERI BANK</div><div>Indiranagar Branch, Bengaluru 560038 ·
IFSC KAVB0001042</div></div><div style="text-align:right">STATEMENT OF ACCOUNT<br>Current Account</div></div>
<table class=meta style="margin-top:8px">
<tr><td>Account holder</td><td><b>KAAPI KART FOODS PVT LTD</b></td><td>Account no.</td><td>XXXXXXXX4417</td></tr>
<tr><td>Period</td><td>01/10/2026 to 31/10/2026</td><td>Currency</td><td>INR</td></tr>
<tr><td>Opening balance</td><td>{_amt(f"{OPENING / 100:.2f}")}</td><td>Customer ID</td><td>KAAPI1234</td></tr>
</table>
<table class=t><thead><tr><th>Date</th><th>Narration</th><th>Chq/Ref No</th>
<th class=n>Withdrawal</th><th class=n>Deposit</th><th class=n>Balance</th></tr></thead>
<tbody>{body}</tbody></table>
<table class=sum>
<tr><td>Opening balance</td><td class=n>{_amt(f"{OPENING / 100:.2f}")}</td></tr>
<tr><td>Total withdrawals ({sum(1 for r in rows if r["debit"])})</td><td class=n>{_amt(f"{debit / 100:.2f}")}</td></tr>
<tr><td>Total deposits ({sum(1 for r in rows if r["credit"])})</td><td class=n>{_amt(f"{credit / 100:.2f}")}</td></tr>
<tr><td><b>Closing balance</b></td><td class=n><b>{_amt(f"{closing / 100:.2f}")}</b></td></tr>
</table>
<div class=foot>This is a computer generated statement. Kaveri Bank is a fictional bank used for
a demo; all figures are synthetic.</div></body></html>"""


def render_pdf(page_html: str) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        page = browser.new_page()
        page.set_content(page_html)
        # A narrow page forces a few pages so the repeated header shows up.
        data = page.pdf(format="A5", landscape=False, print_background=True, scale=1.25)
        browser.close()
    return data


def scan(pdf: bytes, seed: int = 3) -> bytes:
    """Print-and-scan look: greyscale, a slight tilt, speckle, washed-out contrast."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageEnhance, ImageFilter

    rng = random.Random(seed)
    pages = []
    for page in pdfium.PdfDocument(pdf):
        img = page.render(scale=2.2).to_pil().convert("L")
        img = img.rotate(rng.uniform(0.5, 0.9) * rng.choice([-1, 1]), expand=True, fillcolor=245)
        img = ImageEnhance.Contrast(img).enhance(0.72)
        noise = Image.effect_noise(img.size, 22).point(lambda v: 255 if v > 150 else v)
        img = Image.blend(img, noise.convert("L"), 0.08).filter(ImageFilter.GaussianBlur(0.5))
        pages.append(img.convert("RGB"))
    out = io.BytesIO()
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    return out.getvalue()


def lock(pdf: bytes, password: str = PASSWORD) -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt(password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def write(out: Path = SAMPLE) -> None:
    rows = list(csv.DictReader((out / "bank_statement.csv").read_text().splitlines()))
    digital = render_pdf(statement_html(rows))
    (out / "bank_statement.pdf").write_bytes(digital)
    (out / "bank_statement_scanned.pdf").write_bytes(scan(digital))
    (out / "bank_statement_locked.pdf").write_bytes(lock(digital))


if __name__ == "__main__":
    write()
    print("wrote bank_statement.pdf, _scanned.pdf, _locked.pdf (password KAAPI1234)")
