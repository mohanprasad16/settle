"""Decision log and the audit report.

Every decision is logged with who made it (engine, ai, human, rule), what it concerned and a
hash of its inputs, so a reviewer can see why each line in the report is there. The report is a
single self-contained HTML page that prints cleanly to PDF, plus a CSV pack for Excel.
"""

import csv
import hashlib
import html
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from app.engine import Result
from app.gate import Action
from app.journal import Entry, to_csv
from app.money import rupees


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class Decision:
    actor: str  # engine | ai | human | rule
    action: str
    detail: str = ""
    finding_id: str | None = None
    inputs: str = ""  # short hash of what the decision was based on
    ts: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class DecisionLog:
    entries: list[Decision] = field(default_factory=list)

    def log(
        self, actor: str, action: str, detail: str = "", finding_id=None, inputs=None
    ) -> Decision:
        h = hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:12] if inputs is not None else ""  # fmt: skip
        d = Decision(actor, action, detail, finding_id, h)
        self.entries.append(d)
        return d

    def record_run(self, result: Result, files: dict[str, Path]) -> None:
        """What the engine decided on its own: inputs, every finding, every rule and handled item."""
        self.log(
            "engine",
            "loaded files",
            ", ".join(f"{k}={file_hash(p)[:12]}" for k, p in files.items()),
        )
        for f in result.findings:
            self.log(
                "engine",
                "flagged",
                f"{f.title}: {rupees(f.amount)}",
                f.id,
                [f.check, f.amount, f.facts],
            )
        for f, rule in result.resolved:
            self.log("rule", "resolved", f"{f.title}: {rupees(f.amount)} by rule '{rule.note}'", f.id, [f.check, f.amount])  # fmt: skip
        for h in result.handled:
            self.log("engine", "handled", f"{h['label']} ({h['count']})")

    def export(self, path: Path) -> None:
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, ["ts", "actor", "action", "finding_id", "detail", "inputs"])
            w.writeheader()
            w.writerows(asdict(d) for d in self.entries)


def pack(result: Result, journals: list[Entry], out: Path) -> list[Path]:
    """CSV pack for Excel: findings, both bridges, journals."""
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / "findings.csv", out / "bridges.csv", out / "journals.csv"]
    with open(paths[0], "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "kind", "problem", "amount_inr", "references", "evidence_rows"])
        for f in result.findings:
            rows = "; ".join(f"{src} {', '.join(map(str, r))}" for src, r in f.rows.items() if r)
            w.writerow([f.id, f.kind, f.title, f"{f.amount / 100:.2f}", " ".join(r for r in f.refs[:20] if r), rows])  # fmt: skip
    with open(paths[1], "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bridge", "line", "kind", "count", "amount_inr"])
        for name, steps in (("Books to Razorpay", result.books_to_razorpay), ("Razorpay to Bank", result.razorpay_to_bank)):  # fmt: skip
            for s in steps:
                w.writerow([name, s.label, s.kind, s.count, f"{s.amount / 100:.2f}"])
    to_csv(journals, paths[2], result.period.end)
    return paths


# --- the report -----------------------------------------------------------------------------

CSS = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body { font: 10.5pt/1.45 "Helvetica Neue", Arial, sans-serif; color: #1c2430; margin: 0; }
h1 { font-size: 20pt; margin: 0 0 4pt; } h2 { font-size: 13pt; margin: 18pt 0 6pt;
  border-bottom: 1.5pt solid #1c2430; padding-bottom: 3pt; break-after: avoid; }
h3 { font-size: 11pt; margin: 10pt 0 2pt; }
.muted { color: #5b6573; } .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0 8pt; break-inside: auto; }
th, td { padding: 3.5pt 5pt; border-bottom: 0.5pt solid #d5dae1; vertical-align: top; text-align: left; }
th { font-size: 9pt; text-transform: uppercase; letter-spacing: .03em; color: #5b6573; }
tr { break-inside: avoid; }
.cards { display: flex; gap: 8pt; margin: 8pt 0; } .card { flex: 1; border: 0.75pt solid #d5dae1; padding: 7pt 9pt; border-radius: 4pt; }
.card b { display: block; font-size: 14pt; } .loss b { color: #b42318; } .ok b { color: #067647; }
.start td, .end td { font-weight: 700; } .residual td { font-weight: 700; color: #067647; }
.residual.bad td { color: #b42318; } .finding td:first-child { color: #b42318; }
.f { border: 0.75pt solid #d5dae1; border-radius: 4pt; padding: 7pt 9pt; margin: 6pt 0; break-inside: avoid; }
.tag { font-size: 8pt; padding: 1pt 5pt; border-radius: 8pt; background: #eef1f4; margin-left: 4pt; }
.cover { border-bottom: 2pt solid #1c2430; padding-bottom: 8pt; }
.sign td { height: 28pt; } code { font-size: 9pt; word-break: break-all; }
"""


def _e(x) -> str:
    return html.escape(str(x))


def _bridge(steps) -> str:
    rows = []
    for s in steps:
        cls = s.kind + (" bad" if s.kind == "residual" and s.amount else "")
        rows.append(f'<tr class="{cls}"><td>{_e(s.label)}</td><td class="num">{s.count or ""}</td>'
                    f'<td class="num">{rupees(s.amount)}</td></tr>')  # fmt: skip
    return '<table><tr><th>Line</th><th class="num">Items</th><th class="num">Amount</th></tr>' + "".join(rows) + "</table>"  # fmt: skip


def render(
    result: Result,
    files: dict[str, Path],
    explanations: dict | None = None,
    actions: list[Action] | None = None,
    journals: list[Entry] | None = None,
    log: DecisionLog | None = None,
    ai_usage: dict | None = None,
    merchant: str = "Kaapi Kart Foods Pvt Ltd",
) -> str:
    t, p = result.totals, result.period
    explanations, actions, journals = explanations or {}, actions or [], journals or []
    unexplained = t.get("unexplained", 0)
    auto = t.get("auto_reconciled", {})
    rows = t.get("rows", {})
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>Settle audit report, {p.start:%b %Y}</title><style>{CSS}</style></head><body>"]  # fmt: skip

    out.append(f"""<div class="cover"><div class="muted">Settle · reconciliation audit report</div>
<h1>{_e(merchant)}</h1><div>Period {p.start:%d %b %Y} to {p.end:%d %b %Y} · generated {datetime.now():%d %b %Y %H:%M}</div></div>
<h2>Inputs</h2><table><tr><th>File</th><th class="num">Rows</th><th>SHA-256</th></tr>""")
    count_of = {
        "razorpay": rows.get("razorpay"),
        "bank": rows.get("bank"),
        "orders": rows.get("orders"),
    }
    for name, path in files.items():
        out.append(f"<tr><td>{_e(name)} <span class='muted'>({_e(Path(path).name)})</span></td>"
                   f"<td class='num'>{count_of.get(name, '') or ''}</td><td><code>{file_hash(path)}</code></td></tr>")  # fmt: skip
    out.append("</table>")

    out.append(f"""<h2>Summary</h2><div class="cards">
<div class="card loss">Money lost<b>{rupees(t.get("loss", 0))}</b></div>
<div class="card loss">Owed to customers<b>{rupees(t.get("customer", 0))}</b></div>
<div class="card">Book corrections<b>{rupees(t.get("books", 0))}</b></div>
<div class="card ok">Auto-reconciled<b>{auto.get("pct", 0)}%</b><span class="muted">{auto.get("rows", 0)} of {auto.get("of", 0)} rows</span></div>
<div class="card {"ok" if unexplained == 0 else "loss"}">Unexplained<b>{rupees(unexplained)}</b></div></div>""")

    out.append("<h2>Books to Razorpay</h2>" + _bridge(result.books_to_razorpay))
    out.append("<h2>Razorpay to bank</h2>" + _bridge(result.razorpay_to_bank))

    out.append(f"<h2>Findings ({len(result.findings)})</h2>")
    for f in result.findings:
        x = explanations.get(f.id)
        ev = "; ".join(f"{src} rows {', '.join(map(str, r[:12]))}{' …' if len(r) > 12 else ''}" for src, r in f.rows.items() if r)  # fmt: skip
        body = ""
        if x is not None:
            steps = "".join(f"<li>{_e(s)}</li>" for s in x.what_to_do)
            body = (f"<div><b>{_e(x.headline)}</b></div><div>{_e(x.what_happened)}</div>"
                    f"<div class='muted'>{_e(x.likely_cause)}</div><ul>{steps}</ul>"
                    f"<div class='muted'>Contact: {_e(x.who_to_contact)} · written by {_e(x.source)}, numbers verified: {'yes' if x.verified else 'no'}</div>")  # fmt: skip
        out.append(f"""<div class="f"><h3>{f.id} · {_e(f.title)} <span class="tag">{f.kind}</span>
<span style="float:right">{rupees(f.amount)}</span></h3>{body}
<div class="muted">Evidence: {_e(ev)} · refs: {_e(" ".join(r for r in f.refs[:8] if r))}{" …" if len(f.refs) > 8 else ""}</div></div>""")  # fmt: skip

    out.append(
        "<h2>Handled automatically</h2><table><tr><th>What</th><th class='num'>Count</th></tr>"
    )
    out += [
        f"<tr><td>{_e(h['label'])}</td><td class='num'>{h['count']}</td></tr>"
        for h in result.handled
    ]
    out.append("</table>")

    out.append("<h2>Rules applied</h2>")
    if result.resolved:
        out.append(
            "<table><tr><th>Finding</th><th>Rule note</th><th>Added</th><th class='num'>Amount</th></tr>"
        )
        out += [f"<tr><td>{_e(f.title)}</td><td>{_e(r.note)}</td><td>{_e(r.created_at)}</td><td class='num'>{rupees(f.amount)}</td></tr>" for f, r in result.resolved]  # fmt: skip
        out.append("</table>")
    else:
        out.append("<p class='muted'>No rules were applied.</p>")

    out.append("<h2>Could not resolve automatically</h2><ul>")
    unknown = t.get("unmatched_razorpay_credits", [])
    for u in unknown:
        out.append(f"<li>Bank credit of {rupees(u['credit'])} on {u['txn_date']:%d %b %Y} (bank row {u['row_no']}) says Razorpay but matches no settlement in this report: likely from another period.</li>")  # fmt: skip
    if unexplained:
        out.append(f"<li>{rupees(unexplained)} is unexplained by the bridges.</li>")
    out.append(
        f"<li>{len(result.findings)} findings need a person's decision (listed above).</li></ul>"
    )

    out.append("<h2>Journal entries suggested</h2>")
    for i, e in enumerate(journals, 1):
        lines = "".join(f"<tr><td>{'Dr' if dr else '&nbsp;&nbsp;&nbsp;Cr'} {_e(ledger)}</td><td class='num'>{rupees(dr) if dr else ''}</td><td class='num'>{rupees(cr) if cr else ''}</td></tr>" for ledger, dr, cr in e.lines)  # fmt: skip
        out.append(f"<table><tr><th>JV-{i} · {_e(e.narration)}</th><th class='num'>Dr</th><th class='num'>Cr</th></tr>{lines}</table>")  # fmt: skip

    out.append("<h2>Actions</h2>")
    if actions:
        out.append(
            "<table><tr><th>Id</th><th>Type</th><th>Finding</th><th>Status</th><th>Reason / by</th><th class='num'>Amount</th></tr>"
        )
        out += [f"<tr><td>{a.id}</td><td>{_e(a.type)}</td><td>{_e(a.finding_id or '')}</td><td>{_e(a.status)}</td><td>{_e(a.approved_by or a.reason)}</td><td class='num'>{rupees(a.amount)}</td></tr>" for a in actions]  # fmt: skip
        out.append("</table>")
    else:
        out.append("<p class='muted'>No actions were proposed.</p>")

    u = ai_usage or {}
    verified = sum(1 for x in explanations.values() if x.verified)
    out.append(f"""<h2>AI usage</h2><table>
<tr><td>Model calls</td><td class="num">{u.get("calls", 0)}</td></tr>
<tr><td>Tokens in / out</td><td class="num">{u.get("input_tokens", 0)} / {u.get("output_tokens", 0)}</td></tr>
<tr><td>Explanations with every number verified</td><td class="num">{verified} of {len(explanations)}</td></tr>
<tr><td>Figures computed by the model</td><td class="num">0 (all by the engine)</td></tr></table>""")

    if log and log.entries:
        out.append(
            "<h2>Decision log</h2><table><tr><th>Time</th><th>Actor</th><th>Decision</th><th>Detail</th><th>Inputs</th></tr>"
        )
        out += [f"<tr><td>{_e(d.ts)}</td><td>{_e(d.actor)}</td><td>{_e(d.action)} {_e(d.finding_id or '')}</td><td>{_e(d.detail)}</td><td><code>{_e(d.inputs)}</code></td></tr>" for d in log.entries]  # fmt: skip
        out.append("</table>")

    out.append("""<h2>Sign-off</h2><table class="sign"><tr><th style="width:30%"></th><th>Name</th><th>Signature</th><th>Date</th></tr>
<tr><td>Prepared by</td><td></td><td></td><td></td></tr><tr><td>Reviewed by</td><td></td><td></td><td></td></tr></table></body></html>""")
    return "\n".join(out)
