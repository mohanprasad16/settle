"""The web app: a JSON API over the engine and one static page.

Each browser gets a session (cookie) holding its own DuckDB connection, result, rules, action
gate and decision log, kept in memory for SESSION_TTL. Loading data never calls the model:
explanations and drafts come from the sample cache or the per-check templates, and the model is
only called when a person asks (Ask, "Explain with AI", reading a PDF), behind a per-IP limit.

Every error leaves as {"error": plain English, "hint": what to do next}; never a stack trace.
"""

import io
import json
import logging
import os
import random
import shutil
import tempfile
import time
import uuid
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import copilot as copilot_mod
from app import drafts as drafts_mod
from app import explain as explain_mod
from app import journal, sample, transform
from app.actions import propose_all
from app.ask import ask
from app.audit import DecisionLog, pack, render
from app.config import settings
from app.engine import KINDS, SETTLEMENT_STATUS, Result, brs, check_manual, run, settlements
from app.gate import HARD_CAP, LOCKED, MODES, Gate, Policy
from app.gate import KINDS as POLICY_KINDS
from app.llm import GeminiLLM, TapeLLM, Usage
from app.load import REQUIRED, LoadError, load
from app.money import rupees, to_paise
from app.pdf_bank import PdfLocked, check, extract, pages, write_csv
from app.roi import roi
from app.rules import ManualMatch, Rule, suggest_rules
from app.sanitize import CsvWriter
from app.trace import trace

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample"
STATIC = Path(__file__).resolve().parent / "static"

SESSION_TTL = 60 * 60  # seconds idle before a session is dropped
MAX_SESSIONS = 200
MAX_UPLOAD = 15 * 1024 * 1024  # bytes per file
AI_CALLS_PER_WINDOW = int(os.environ.get("SETTLE_AI_CALLS", "20"))  # per IP, model-backed requests
AI_WINDOW = 10 * 60  # ... per 10 minutes
# Browser system test: replay recorded model replies (no key, no network) or record them.
TAPE = os.environ.get("SETTLE_TAPE"), os.environ.get("SETTLE_TAPE_MODE", "replay")
_tape: TapeLLM | None = None


def _ai_on() -> bool:
    return bool(settings.gemini_api_key) or bool(TAPE[0] and TAPE[1] == "replay")


SLOTS = {"razorpay": "rp", "bank": "bank", "orders": "orders"}
SLOT_LABEL = {
    "rp": "Razorpay settlement report",
    "bank": "bank statement",
    "orders": "orders export",
}
MERCHANT = {
    "sample": "Kaapi Kart Foods Pvt Ltd",
    "chaos": "Kaapi Kart Foods Pvt Ltd (random month)",
    "upload": "Your business",
}


class Oops(Exception):
    def __init__(self, status: int, error: str, hint: str = "", **extra):
        self.status, self.error, self.hint, self.extra = status, error, hint, extra


@dataclass
class Session:
    dir: Path
    source: str = ""
    files: dict[str, Path] = field(default_factory=dict)  # razorpay / bank / orders
    con: duckdb.DuckDBPyConnection | None = None
    result: Result | None = None
    rules: list[Rule] = field(default_factory=list)
    explanations: dict = field(default_factory=dict)
    ai_explained: dict = field(default_factory=dict)  # cache key -> explanation a person asked for
    drafts: dict = field(default_factory=dict)
    gate: Gate | None = None
    log: DecisionLog = field(default_factory=DecisionLog)
    journals: list = field(default_factory=list)
    usages: list[Usage] = field(default_factory=list)
    truth: dict | None = None
    pending_pdf: dict | None = None  # extraction waiting for the person to confirm
    manual: list[ManualMatch] = field(default_factory=list)  # a person's confirmed matches
    proposals: dict = field(default_factory=dict)  # Copilot / form change cards, id -> Proposal
    sheets: dict = field(default_factory=dict)  # files attached in the Copilot, id -> sheet
    policy: Policy = field(default_factory=Policy)  # the approval policy; only a person edits it
    answers: dict = field(default_factory=dict)  # Settle AI answer id -> {q, answer, page}
    feedback: list = field(default_factory=list)  # 👍 / 👎 on answers: the start of an eval set
    ai_times: deque = field(
        default_factory=deque
    )  # this session's model calls, for its hourly limit
    seen: float = field(default_factory=time.time)


SESSIONS: dict[str, Session] = {}
AI_HITS: dict[str, deque] = defaultdict(deque)

app = FastAPI(title="Settle", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --- errors ----------------------------------------------------------------------------------


@app.exception_handler(Oops)
async def _oops(_, e: Oops):
    return JSONResponse({"error": e.error, "hint": e.hint, **e.extra}, status_code=e.status)


@app.exception_handler(RequestValidationError)
async def _bad_request(_, e):
    return JSONResponse(
        {
            "error": "Some of what was sent is missing or in the wrong shape.",
            "hint": "Reload the page and try again.",
        },
        status_code=400,
    )


@app.exception_handler(Exception)
async def _crash(_, e):
    log.exception("unhandled error")
    return JSONResponse(
        {
            "error": "Something went wrong on our side.",
            "hint": "Try again. If it keeps happening, reload the page.",
        },
        status_code=500,
    )


# --- sessions and limits ---------------------------------------------------------------------


def _session(request: Request, create: bool = False) -> tuple[str, Session]:
    now = time.time()
    for sid in [k for k, s in SESSIONS.items() if now - s.seen > SESSION_TTL]:
        _drop(sid)
    sid = request.cookies.get("settle_sid", "")
    s = SESSIONS.get(sid)
    if s is None:
        if not create:
            raise Oops(
                409, "Nothing is loaded yet.", "Start with the sample data or upload your files."
            )
        while len(SESSIONS) >= MAX_SESSIONS:
            _drop(min(SESSIONS, key=lambda k: SESSIONS[k].seen))
        sid = uuid.uuid4().hex
        s = SESSIONS[sid] = Session(Path(tempfile.mkdtemp(prefix="settle-")))
    s.seen = now
    return sid, s


def _drop(sid: str) -> None:
    s = SESSIONS.pop(sid, None)
    if s:
        shutil.rmtree(s.dir, ignore_errors=True)


def _loaded(request: Request) -> Session:
    _, s = _session(request)
    if s.result is None:
        raise Oops(
            409, "Nothing is loaded yet.", "Start with the sample data or upload your files."
        )
    return s


def _respond(sid: str, body) -> JSONResponse:
    r = JSONResponse(jsonable_encoder(body))
    r.set_cookie("settle_sid", sid, max_age=SESSION_TTL, httponly=True, samesite="lax")
    return r


def _ai_allowed(request: Request) -> None:
    ip = request.client.host if request.client else "?"
    hits, now = AI_HITS[ip], time.time()
    while hits and now - hits[0] > AI_WINDOW:
        hits.popleft()
    if len(hits) >= AI_CALLS_PER_WINDOW:
        wait = (int(AI_WINDOW - (now - hits[0])) if hits else AI_WINDOW) // 60 + 1
        raise Oops(
            429,
            "You've used the AI a lot in the last few minutes.",
            f"Try again in about {wait} min. The reconciliation itself keeps working.",
        )
    hits.append(now)


def _llm(s: "Session | None" = None) -> GeminiLLM:
    if s is not None and not s.policy.ai_enabled:
        raise Oops(
            503,
            "The AI is switched off in Settings.",
            "Turn it on in Settings → Approval policy; everything else still works.",
        )
    if s is not None:
        now = time.time()
        while s.ai_times and now - s.ai_times[0] > 3600:
            s.ai_times.popleft()
        if len(s.ai_times) >= s.policy.ai_calls_per_hour:
            raise Oops(429, "This session reached its AI limit for the hour (set in Settings).", "Raise it in Settings, or try again later.")  # fmt: skip
        s.ai_times.append(now)
    if not _ai_on():
        raise Oops(
            503,
            "The AI isn't available right now.",
            "Everything else still works; explanations are written from templates.",
        )
    if TAPE[0]:
        global _tape
        _tape = _tape or TapeLLM(TAPE[0], TAPE[1], GeminiLLM() if TAPE[1] == "record" else None)
        return _tape
    return GeminiLLM()


# --- the pipeline ----------------------------------------------------------------------------


def _akey(a) -> str:
    """An action's identity across re-runs (finding ids renumber when an issue is resolved)."""
    return json.dumps([a.type, a.amount, a.payload], sort_keys=True, default=str)


def _reconcile(s: Session, keep: bool = False, why: str = "") -> None:
    """Load the session's three files, run the engine, and rebuild everything downstream.
    keep: a person changed something on this same month (a rule, a match, a sheet); their
    decision log and the actions they already decided carry over instead of starting again."""
    old_log = s.log if keep else None
    decided = {_akey(a): a for a in s.gate.actions.values()} if keep and s.gate else {}
    con = duckdb.connect()
    try:
        period = load(con, s.files["razorpay"], s.files["bank"], s.files["orders"])
    except LoadError as e:
        raise Oops(422, str(e), _load_hint(str(e))) from e
    except duckdb.Error as e:
        raise Oops(
            422,
            "One of the files isn't a readable CSV.",
            "Export it again as CSV (comma separated) and upload it.",
        ) from e
    if not con.execute("SELECT count(*) FROM rp").fetchone()[0]:
        raise Oops(
            422,
            "The Razorpay report has no transactions.",
            "Download the settlement report for the same month as the bank statement.",
        )
    result = run(con, period, rules=s.rules, manual=s.manual)
    exp_cache = explain_mod.load_cache(explain_mod.SAMPLE_CACHE)
    draft_cache = drafts_mod.load_cache(drafts_mod.SAMPLE_CACHE)
    exp_cache |= (
        s.ai_explained
    )  # keep what a person asked the model for, if the finding is unchanged
    s.con, s.result = con, result
    s.explanations = explain_mod.explain_all(result, None, exp_cache)
    s.drafts = drafts_mod.draft_all(result, None, draft_cache)
    if old_log is None:
        s.log = DecisionLog()
        s.log.record_run(result, s.files)
        s.log.record_ai(s.explanations, s.drafts)
        for r in s.rules:
            s.log.log("human", "added rule", f"{r.check}: {r.note}")
    else:
        s.log = old_log
        s.log.log("engine", "reconciled again", f"{why}: {len(result.findings)} issues, unexplained {result.totals['unexplained']} paise")  # fmt: skip
    s.gate = Gate({f.id: f for f in result.findings}, s.policy)
    s.journals = journal.entries(con, result)
    propose_all(s.gate, result, s.journals, s.drafts)
    for a in s.gate.actions.values():  # carry a person's earlier decisions over by identity
        b = decided.get(_akey(a))
        if b and b.status in ("approved", "executed", "rejected"):
            a.status, a.approved_by, a.sent, a.reason = b.status, b.approved_by, b.sent, b.reason
    s.gate.log = s.log
    if old_log is None:
        for a in s.gate.actions.values():
            s.gate._record(a.proposed_by, "proposed", a)


def _load_hint(msg: str) -> str:
    if "missing columns" in msg:
        return "Check each file is in the right box, and that it's the export named on the box with its original column names."
    if "date" in msg:
        return "Dates should look like 14/10/2026 or 2026-10-14."
    return "Export the file again as CSV and try once more."


def _compare(result: Result, truth: dict) -> list[dict]:
    """Answer key vs what the engine found, for the chaos panel."""
    out, used = [], set()
    for p in truth["planted"]:
        hit = next(
            (
                f
                for f in result.findings
                if f.id not in used
                and f.check == p["check"]
                and f.amount == p["amount"]
                and p["refs"][0] in f.refs
            ),
            None,
        )
        if hit:
            used.add(hit.id)
        title = KINDS[p["check"]][1] if p["check"] in KINDS else p["check"]
        out.append(
            {
                "title": title,
                "amount": p["amount"],
                "ref": p["refs"][0],
                "found": hit is not None,
                "finding_id": hit.id if hit else None,
            }
        )
    for f in result.findings:
        if f.id not in used:
            out.append(
                {
                    "title": f.title,
                    "amount": f.amount,
                    "ref": f.refs[0] if f.refs else "",
                    "found": None,
                    "finding_id": f.id,
                }
            )
    return out


def _view(s: Session) -> dict:
    r = s.result
    t = dict(r.totals)
    exps = s.explanations
    findings = [
        {
            "id": f.id,
            "check": f.check,
            "kind": f.kind,
            "title": f.title,
            "amount": f.amount,
            "ref": f.refs[0] if f.refs else "",
            "count": len(f.refs)
            if f.check in ("fee_overcharge", "rate_misclassified", "gst_mismatch")
            else None,
            "headline": exps[f.id].headline if f.id in exps else "",
            "contact": exps[f.id].who_to_contact if f.id in exps else "",
            "ai": exps[f.id].source == "model" if f.id in exps else False,
        }
        for f in r.findings
    ]
    return {
        "source": s.source,
        "merchant": MERCHANT.get(s.source, ""),
        "period": {"start": r.period.start, "end": r.period.end},
        "totals": {
            k: t.get(k)
            for k in (
                "loss",
                "customer",
                "books",
                "findings",
                "risk",
                "exceptions",
                "unexplained",
                "auto_reconciled",
                "value_reconciled",
                "rows",
            )
        },
        "roi": roi(r, s.usages),
        "findings": findings,
        "bridges": {
            "books_to_razorpay": [vars(x) for x in r.books_to_razorpay],
            "razorpay_to_bank": [vars(x) for x in r.razorpay_to_bank],
        },
        "handled": r.handled,
        "resolved": [
            {"id": f.id, "title": f.title, "amount": f.amount, "note": rule.note}
            for f, rule in r.resolved
        ],
        "exceptions": [{k: v for k, v in e.items() if k != "rows"} for e in r.exceptions],
        "unmatched_credits": t.get("unmatched_razorpay_credits", []),
        "suspicious": t.get("suspicious_text", []),
        "ai": {
            "available": _ai_on(),
            "templates": sum(1 for x in exps.values() if x.source != "model"),
        },
        "chaos": _compare(r, s.truth) if s.truth else None,
        "seed": s.truth.get("seed") if s.truth else None,
        "rules": [
            {"check": x.check, "note": x.note, "ref": x.ref, "max_amount": x.max_amount}
            for x in s.rules
        ],
        "suggested_rules": [
            x
            for x in suggest_rules(r.findings)
            if not any(y.check == x["check"] and y.ref is None for y in s.rules)
        ],
        "manual": [
            {"note": m.note, "by": m.by, "bank_rows": m.bank_rows, "settlement_id": m.settlement_id}
            for m in s.manual
        ],
        "demo_mode": settings.demo_mode,
        # [day, refunds, paise] for the refunds-per-day chart on Patterns.
        "refunds_by_day": s.con.execute(
            """SELECT CAST(created_at AS DATE), count(*), sum(amount) FROM rp WHERE type = 'refund'
               AND CAST(created_at AS DATE) BETWEEN ? AND ? GROUP BY 1 ORDER BY 1""",
            [r.period.start, r.period.end],
        ).fetchall(),
    }


# --- endpoints: loading data -----------------------------------------------------------------


# HEAD too: uptime monitors check with it, and a 405 reads as "down".
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.api_route("/api/health", methods=["GET", "HEAD"])
def health():
    return {"ok": True, "ai": _ai_on(), "demo_mode": settings.demo_mode}


@app.get("/llms.txt")
def llms_txt():
    """Plain-text summary for AI tools that read a site before a person does."""
    return FileResponse(ROOT / "llms.txt", media_type="text/plain; charset=utf-8")


@app.post("/api/sample")
def load_sample(request: Request):
    sid, s = _session(request, create=True)
    s.files = {
        k: SAMPLE / v
        for k, v in (
            ("razorpay", "razorpay_settlements.csv"),
            ("bank", "bank_statement.csv"),
            ("orders", "orders.csv"),
        )
    }
    s.source, s.truth, s.rules, s.ai_explained = "sample", None, [], {}
    s.manual, s.proposals = [], {}
    _reconcile(s)
    return _respond(sid, _view(s))


class ChaosIn(BaseModel):
    seed: int | None = None


@app.post("/api/chaos")
def load_chaos(request: Request, body: ChaosIn | None = None):
    sid, s = _session(request, create=True)
    seed = body.seed if body and body.seed is not None else random.randint(100_000, 999_999)
    if seed == sample.DEMO_SEED:
        seed += 1
    shutil.rmtree(s.dir / "chaos", ignore_errors=True)  # one random month per session at a time
    paths, truth = sample.chaos(seed, s.dir / "chaos")
    s.files = {"razorpay": paths["rp"], "bank": paths["bank"], "orders": paths["orders"]}
    s.source, s.truth, s.rules, s.ai_explained = "chaos", truth | {"seed": seed}, [], {}
    s.manual, s.proposals = [], {}
    _reconcile(s)
    return _respond(sid, _view(s))


def _which(header: str) -> str | None:
    """Which slot a CSV header row belongs to, by its required columns."""
    cols = {c.strip().strip('"').lower() for c in header.split(",")}
    return next((slot for slot, need in REQUIRED.items() if set(need) <= cols), None)


async def _save(upload: UploadFile, dest: Path) -> bytes:
    data = await upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise Oops(
            413,
            f"{upload.filename} is larger than {MAX_UPLOAD // (1024 * 1024)} MB.",
            "Upload one month at a time.",
        )
    if not data.strip():
        raise Oops(422, f"{upload.filename} is empty.", "Export the file again and upload it.")
    dest.write_bytes(data)
    return data


@app.post("/api/upload")
async def upload(
    request: Request,
    razorpay: Annotated[UploadFile, File()],
    bank: Annotated[UploadFile, File()],
    orders: Annotated[UploadFile, File()],
    password: Annotated[str, Form()] = "",
):
    sid, s = _session(request, create=True)
    files = {"razorpay": razorpay, "bank": bank, "orders": orders}
    saved, raw = {}, {}
    for slot, up in files.items():
        is_pdf = (up.filename or "").lower().endswith(".pdf")
        dest = s.dir / f"{slot}{'.pdf' if is_pdf else '.csv'}"
        raw[slot] = await _save(up, dest)
        saved[slot] = dest
        if raw[slot][:5] == b"%PDF-" and slot != "bank":
            raise Oops(
                422,
                f"The {SLOT_LABEL[SLOTS[slot]]} box needs a CSV, not a PDF.",
                "Only the bank statement can be a PDF.",
            )
        if raw[slot][:5] != b"%PDF-":
            head = raw[slot].decode("utf-8-sig", "replace").splitlines()[0] if raw[slot] else ""
            got = _which(head)
            if got and got != SLOTS[slot]:
                raise Oops(
                    422,
                    f"The file in the {SLOT_LABEL[SLOTS[slot]]} box looks like your {SLOT_LABEL[got]}.",
                    "Swap the files into the right boxes and try again.",
                    slot=slot,
                )

    s.source, s.truth, s.rules, s.ai_explained, s.pending_pdf = "upload", None, [], {}, None
    s.manual, s.proposals = [], {}
    if raw["bank"][:5] == b"%PDF-":
        try:
            pages(raw["bank"], password or None)  # cheap: unlock first, before any model call
        except PdfLocked as e:
            need = "password" in str(e)
            raise Oops(
                422,
                str(e),
                "Banks often use your customer ID or date of birth (DDMMYYYY)."
                if need
                else "Upload the statement as a CSV instead, if your bank offers one.",
                needs_password=need,
            ) from e
        _ai_allowed(request)
        try:
            rep = extract(raw["bank"], _llm(), password or None)
        except PdfLocked as e:
            raise Oops(422, str(e), "Try again, or upload the statement as a CSV.") from e
        s.usages.append(Usage(calls=rep.model_calls))
        s.files = {
            "razorpay": saved["razorpay"],
            "bank": s.dir / "bank_from_pdf.csv",
            "orders": saved["orders"],
        }
        s.pending_pdf = {"opening": rep.opening_balance}
        return _respond(
            sid,
            {
                "needs_review": True,
                "verified": rep.verified,
                "pages": rep.pages,
                "totals_ok": rep.totals_ok,
                "reread_pages": rep.reread_pages,
                "opening_balance": rep.opening_balance,
                "rows": [_pdf_row(r) for r in rep.rows],
                "breaks": [vars(b) for b in rep.breaks],
            },
        )
    s.files = saved
    _reconcile(s)
    return _respond(sid, _view(s))


def _pdf_row(r: dict) -> dict:
    rup = lambda p: f"{p / 100:.2f}" if p else ""
    return {
        "txn_date": r["txn_date"],
        "narration": r["narration"],
        "ref_no": r["ref_no"],
        "debit": rup(r["debit"]),
        "credit": rup(r["credit"]),
        "balance": rup(r["balance"]),
        "page": r.get("page"),
    }


class BankRow(BaseModel):
    txn_date: str
    narration: str = ""
    ref_no: str = ""
    debit: str = ""
    credit: str = ""
    balance: str = ""


class BankConfirm(BaseModel):
    rows: list[BankRow]


@app.post("/api/bank-preview/confirm")
def confirm_bank(request: Request, body: BankConfirm):
    sid, s = _session(request)
    if not s.pending_pdf:
        raise Oops(409, "There's no statement waiting to be checked.", "Upload your files again.")
    try:
        rows = [
            {
                "txn_date": r.txn_date,
                "narration": r.narration,
                "ref_no": r.ref_no,
                "debit": to_paise(r.debit),
                "credit": to_paise(r.credit),
                "balance": to_paise(r.balance),
            }
            for r in body.rows
        ]
    except ValueError as e:
        raise Oops(
            422, f"One of the amounts isn't a number: {e}.", "Use digits only, like 12,345.67."
        ) from e
    breaks = check(rows, s.pending_pdf["opening"])
    if breaks:
        raise Oops(
            422,
            f"{len(breaks)} row{'s still don' if len(breaks) > 1 else ' still doesn'}'t add up to the printed balance.",
            "Fix the highlighted rows, then continue.",
            breaks=[vars(b) for b in breaks],
        )
    write_csv(rows, s.files["bank"])
    s.pending_pdf = None
    _reconcile(s)
    return _respond(sid, _view(s))


# --- endpoints: reading the result -----------------------------------------------------------

EVIDENCE = {
    "rp": (
        "rp",
        "entity_id, type, amount, fee, tax, order_receipt, method, card_type, card_network, settlement_id, utr, created_at",
    ),
    "bank": ("bank", "txn_date, narration, ref_no, debit, credit"),
    "orders": ("orders", "order_no, order_date, customer, amount, status, refund_amount"),
}
SOURCE_LABEL = {"rp": "Razorpay report", "bank": "Bank statement", "orders": "Your orders"}
MONEY_COLUMNS = ["amount", "fee", "tax", "debit", "credit", "refund_amount"]


def _evidence(s: Session, rows: dict[str, list[int]], limit: int = 25) -> list[dict]:
    out = []
    for src, nums in rows.items():
        if src not in EVIDENCE or not nums:
            continue
        table, cols = EVIDENCE[src]
        nums = sorted(set(nums))
        q = f"SELECT row_no, {cols} FROM {table} WHERE row_no IN ({', '.join('?' * len(nums[:limit]))}) ORDER BY row_no"
        cur = s.con.execute(q, nums[:limit])
        names = [d[0] for d in cur.description]
        out.append(
            {
                "source": src,
                "label": SOURCE_LABEL[src],
                "columns": names,
                "money": MONEY_COLUMNS,
                "rows": [dict(zip(names, r)) for r in cur.fetchall()],
                "total": len(nums),
            }
        )
    return out


def _finding(s: Session, fid: str):
    f = next((x for x in s.result.findings if x.id == fid), None)
    if f is None:
        raise Oops(
            404,
            f"There's no problem {fid} in this month.",
            "It may have been resolved by a rule; reload the results.",
        )
    return f


def _action(a) -> dict:
    return {
        "id": a.id,
        "type": a.type,
        "amount": a.amount,
        "status": a.status,
        "reason": a.reason,
        "approved_by": a.approved_by,
        "proposed_by": a.proposed_by,
        "payload": a.payload,
        "sent": a.sent,
    }


@app.get("/api/result")
def result(request: Request):
    """The current session's result; {"loaded": false} (not an error) on a first visit."""
    s = SESSIONS.get(request.cookies.get("settle_sid", ""))
    if s is None or s.result is None:
        return {"loaded": False}
    sid, s = _session(request)
    return _respond(sid, _view(s) | {"loaded": True})


@app.get("/api/finding/{fid}")
def finding(request: Request, fid: str):
    s = _loaded(request)
    f = _finding(s, fid)
    try:
        chain = trace(s.con, s.result, f.refs[0]) if f.refs and f.kind != "risk" else None
    except Exception:  # a trace is a nice-to-have; never fail the drawer over it
        log.exception("trace failed for %s", fid)
        chain = None
    exp = s.explanations.get(fid)
    return JSONResponse(
        jsonable_encoder(
            {
                "finding": {
                    "id": f.id,
                    "check": f.check,
                    "kind": f.kind,
                    "title": f.title,
                    "amount": f.amount,
                    "refs": f.refs[:30],
                    "ref_count": len(f.refs),
                    "facts": f.facts,
                },
                "explanation": exp.model_dump() if exp else None,
                "evidence": _evidence(s, f.rows),
                "trace": chain,
                "drafts": [d.model_dump() for d in s.drafts.get(fid, [])],
                "api_call": drafts_mod.api_call(f),
                "actions": [_action(a) for a in s.gate.actions.values() if a.finding_id == fid],
            }
        )
    )


@app.post("/api/finding/{fid}/explain")
def explain_with_ai(request: Request, fid: str):
    s = _loaded(request)
    f = _finding(s, fid)
    llm = _llm()
    _ai_allowed(request)
    exp = explain_mod.explain(f, llm)
    if exp.source != "model":
        raise Oops(
            503,
            "The AI couldn't write a verified explanation just now.",
            "The template explanation is still correct. Try again in a minute.",
        )
    s.explanations[fid] = s.ai_explained[explain_mod.cache_key(f)] = exp
    s.log.log("ai", "explained", "model, numbers verified (on request)", fid)
    return JSONResponse(jsonable_encoder({"explanation": exp.model_dump()}))


@app.get("/api/trace/{ref}")
def trace_ref(request: Request, ref: str):
    s = _loaded(request)
    out = trace(s.con, s.result, ref)
    if not out["found"]:
        raise Oops(
            404,
            f"Couldn't find “{ref}” in these files.",
            "Try an order number like KK-10450, or a pay_, rfnd_ or setl_ id, or a UTR.",
        )
    return JSONResponse(jsonable_encoder(out))


class AskIn(BaseModel):
    question: str


@app.post("/api/ask")
def ask_question(request: Request, body: AskIn):
    s = _loaded(request)
    q = body.question.strip()
    if not q:
        raise Oops(
            400, "Type a question first.", "For example: why was the 14 Oct settlement short?"
        )
    if len(q) > 500:
        raise Oops(400, "That question is too long.", "Keep it under 500 characters.")
    llm = _llm()
    _ai_allowed(request)
    out = ask(s.con, s.result, q, llm, s.explanations)
    s.usages.append(out.usage)
    s.log.log(
        "ai",
        "answered a question",
        ("verified" if out.verified else "not verified") + f": {q[:80]}",
    )
    return JSONResponse(
        jsonable_encoder(
            {
                "answer": out.answer,
                "verified": out.verified,
                "citations": [c.model_dump() for c in out.citations],
                "trace": out.trace,
            }
        )
    )


# --- endpoints: decisions --------------------------------------------------------------------


class RuleIn(BaseModel):
    finding_id: str
    note: str
    scope: str = "this"  # this | similar


@app.post("/api/rules")
def add_rule(request: Request, body: RuleIn):
    s = _loaded(request)
    sid, _ = _session(request)
    f = _finding(s, body.finding_id)
    note = body.note.strip()
    if not note:
        raise Oops(
            400, "Add a short note saying why this is expected.", "For example: coupon at checkout."
        )
    if f.kind == "loss" and body.scope == "similar":
        raise Oops(
            400,
            "Money-loss problems can only be marked one at a time.",
            "Mark this one as expected; new ones will still be flagged.",
        )
    rule = Rule(
        f.check,
        note[:120],
        ref=f.refs[0] if body.scope == "this" and f.refs else None,
        max_amount=abs(f.amount) if body.scope == "similar" else None,
    )
    s.rules.append(rule)
    _reconcile(s, keep=True, why=f"rule added: {rule.note}")
    return _respond(sid, _view(s))


class ApproveIn(BaseModel):
    approver: str


@app.post("/api/actions/{aid}/approve")
def approve(request: Request, aid: str, body: ApproveIn):
    s = _loaded(request)
    a = s.gate.actions.get(aid)
    if a is None:
        raise Oops(404, "That action no longer exists.", "Reload the results.")
    if not body.approver.strip():
        raise Oops(
            400, "Type your name to approve.", "Approvals are recorded with the approver's name."
        )
    try:
        if a.status == "needs_approval":
            s.gate.approve(aid, body.approver)
        if a.status == "needs_approval":  # two-person rule: first approval recorded, still waiting
            return JSONResponse(jsonable_encoder({"action": _action(a), "needs_second": True, "demo_mode": settings.demo_mode}))  # fmt: skip
        s.gate.execute(aid)
    except PermissionError as e:
        raise Oops(403, f"This action can't run: {e}.", "Nothing was sent.") from e
    return JSONResponse(jsonable_encoder({"action": _action(a), "demo_mode": settings.demo_mode}))


@app.post("/api/actions/{aid}/reject")
def reject(request: Request, aid: str, body: ApproveIn):
    s = _loaded(request)
    a = s.gate.actions.get(aid)
    if a is None:
        raise Oops(404, "That action no longer exists.", "Reload the results.")
    if a.status not in ("needs_approval", "auto"):
        raise Oops(403, f"This action is already {a.status}.", "Nothing changed.")
    a.status, a.reason = "rejected", f"rejected by {body.approver.strip() or 'a reviewer'}"
    s.log.log("human", "rejected", f"{a.id} {a.type}", a.finding_id)
    return JSONResponse(jsonable_encoder({"action": _action(a)}))


@app.get("/api/connect")
def connect_info():
    return {
        "demo_mode": settings.demo_mode,
        "message": "In live mode you'd enter your Razorpay API key (read-only). Settle pulls the settlement report every day and alerts you only when something new is wrong. This demo runs on sample data.",
    }


# --- endpoints: downloads --------------------------------------------------------------------


def _usage(s: Session) -> dict:
    return {
        "calls": sum(u.calls for u in s.usages),
        "input_tokens": sum(u.input_tokens for u in s.usages),
        "output_tokens": sum(u.output_tokens for u in s.usages),
    }


def _report_html(s: Session) -> str:
    files = {k: v for k, v in s.files.items() if v.exists()}
    return render(
        s.result,
        files,
        s.explanations,
        list(s.gate.actions.values()),
        s.journals,
        s.log,
        _usage(s),
        merchant=MERCHANT.get(s.source, "Your business"),
    )


@app.get("/api/audit")
def audit_trail(request: Request):
    """Every proposed action and the decision log, for the Approvals & audit trail page."""
    s = _loaded(request)
    acts = [_action(a) | {"finding_id": a.finding_id} for a in s.gate.actions.values()]
    return JSONResponse(
        jsonable_encoder({"actions": acts, "log": [vars(e) for e in s.log.entries[-300:]]})
    )


@app.get("/api/report", response_class=HTMLResponse)
def report(request: Request):
    s = _loaded(request)
    return HTMLResponse(_report_html(s))


@app.get("/api/settlements")
def settlement_list(request: Request):
    """Payout by payout: the accountant's first view. Same table the Razorpay → bank bridge uses."""
    s = _loaded(request)
    rows = settlements(s.con, s.result)
    by_type: dict[str, dict[str, int]] = defaultdict(dict)
    for sid, typ, amt, fee in s.con.execute(
        """SELECT settlement_id, type,
                  sum(CASE WHEN type = 'payment' THEN amount ELSE credit - debit END),
                  sum(CASE WHEN type = 'payment' THEN fee ELSE 0 END)
           FROM rp GROUP BY ALL"""
    ).fetchall():
        by_type[sid][typ] = int(amt)
        if fee:
            by_type[sid]["fees"] = by_type[sid].get("fees", 0) - int(fee)
    for r in rows:
        r["by_type"] = by_type.get(r["settlement_id"], {})
    totals = {k: sum(r[k] for r in rows) for k in ("items", "net", "received", "difference")}
    return JSONResponse(
        jsonable_encoder({"settlements": rows, "totals": totals, "labels": SETTLEMENT_STATUS})
    )


@app.get("/api/brs")
def brs_csv(request: Request):
    """The Bank Reconciliation Statement in the layout an accountant files, as CSV."""
    s = _loaded(request)
    buf = io.StringIO()
    w = CsvWriter(buf)
    p = s.result.period
    w.writerow([f"Bank Reconciliation Statement, {p.start:%d %b %Y} to {p.end:%d %b %Y}", ""])
    w.writerow(["Particulars", "Amount (INR)"])
    for label, amount in brs(s.result):
        w.writerow([label, f"{amount / 100:.2f}"])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="settle_brs_{p.start:%Y_%m}.csv"'},
    )


@app.get("/api/journal")
def journal_csv(request: Request):
    s = _loaded(request)
    path = s.dir / "tally_journal.csv"
    journal.to_csv(s.journals, path, s.result.period.end)
    return FileResponse(
        path, media_type="text/csv", filename=f"settle_journal_{s.result.period.start:%Y_%m}.csv"
    )


@app.get("/api/export")
def export(request: Request):
    s = _loaded(request)
    out = s.dir / "pack"
    paths = pack(s.result, s.journals, out, s.log, s.feedback)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, p.name)
        z.writestr("audit_report.html", _report_html(s))
    name = f"settle_{s.result.period.start:%Y_%m}.zip"
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --- Copilot and change cards ----------------------------------------------------------------


def _ctx(s: Session) -> copilot_mod.Ctx:
    return copilot_mod.Ctx(s.con, s.result, s.gate, s.sheets, s.proposals, s.log)


def _card(p, policy: Policy | None = None) -> dict:
    return {"id": p.id, "type": p.type, "title": p.title, "changes": p.changes, "checks": p.checks,
            "status": p.status, "decided_by": p.decided_by, "proposed_by": p.proposed_by,
            "kind": p.kind, "amount": p.amount, "approvals": p.approvals,
            "always_allow": copilot_mod.can_always_allow(policy, p) is None if policy else False,
            "preview": p.payload.get("preview"), "result": p.result}  # fmt: skip


class Selection(BaseModel):
    settlements: list[str] = []
    bank_rows: list[int] = []
    findings: list[str] = []
    actions: list[str] = []


class CopilotIn(BaseModel):
    message: str
    page: str = "overview"
    focus: str = ""
    selection: Selection = Selection()


@app.post("/api/copilot")
def copilot_chat(request: Request, body: CopilotIn):
    s = _loaded(request)
    q = body.message.strip()
    if not q:
        raise Oops(400, "Type what you need first.", "For example: what should I do first?")
    if len(q) > 800:
        raise Oops(400, "That message is too long.", "Keep it under 800 characters.")
    llm = _llm(s)
    _ai_allowed(request)
    out, made = copilot_mod.copilot(_ctx(s), q, llm, s.explanations, body.page[:30], body.focus[:40],
                                    body.selection.model_dump())  # fmt: skip
    s.usages.append(out.usage)
    s.log.log("ai", "copilot", ("verified" if out.verified else "not verified") + f": {q[:80]}")
    for p in made:  # a granted kind, or auto-accept mode within the policy: code applies it
        if copilot_mod.auto_applies(s.policy, p):
            _apply(s, p, "auto-accept (your approval policy)")
    aid = uuid.uuid4().hex[:10]
    s.answers[aid] = {"q": q[:800], "answer": out.answer[:4000], "page": body.page[:30], "verified": out.verified}  # fmt: skip
    while len(s.answers) > 200:  # ponytail: oldest answers can't be rated after 200 more
        s.answers.pop(next(iter(s.answers)))
    sid, _ = _session(request)
    return _respond(sid, {
        "answer_id": aid, "answer": out.answer, "verified": out.verified,
        "citations": [c.model_dump() for c in out.citations], "trace": out.trace,
        "plan": out.plan, "proposals": [_card(p, s.policy) for p in made],
        "follow_ups": copilot_mod.follow_ups(s.result, body.page[:30], out.citations, made, q),
        "view": _view(s) | {"loaded": True}})  # fmt: skip


FEEDBACK_REASONS = {"wrong_number": "Wrong number", "not_asked": "Not what I asked", "unclear": "Unclear", "other": "Other"}  # fmt: skip


class FeedbackIn(BaseModel):
    answer_id: str
    rating: str  # up | down
    reason: str = ""
    note: str = ""


@app.post("/api/feedback")
def feedback(request: Request, body: FeedbackIn):
    """👍 / 👎 on a Settle AI answer. The question and answer come from the session, not the
    browser, so a rating can't smuggle in text that was never said."""
    s = _loaded(request)
    a = s.answers.get(body.answer_id)
    if a is None:
        raise Oops(
            404, "That answer can't be rated any more.", "Ask again and rate the new answer."
        )
    if body.rating not in ("up", "down"):
        raise Oops(422, "A rating is up or down.", "Use 👍 or 👎.")
    if body.reason and body.reason not in FEEDBACK_REASONS:
        raise Oops(422, "That reason isn't one of the options.", "Pick wrong number, not what I asked, unclear or other.")  # fmt: skip
    note = " ".join(body.note.split())[:300]
    s.feedback = [
        f for f in s.feedback if f["answer_id"] != body.answer_id
    ]  # changing your mind replaces it
    s.feedback.append({"ts": datetime.now().isoformat(timespec="seconds"), "answer_id": body.answer_id,
                       "rating": body.rating, "reason": FEEDBACK_REASONS.get(body.reason, ""), "note": note,
                       "page": a["page"], "question": a["q"], "answer": a["answer"], "verified": a["verified"]})  # fmt: skip
    s.log.log("human", "rated an answer", f"{'👍' if body.rating == 'up' else '👎'} {FEEDBACK_REASONS.get(body.reason, '')} {note}".strip()[:200])  # fmt: skip
    sid, _ = _session(request)
    return _respond(sid, {"ok": True, "count": len(s.feedback)})


@app.post("/api/copilot/file")
async def copilot_file(request: Request, file: Annotated[UploadFile, File()]):
    """Attach a spreadsheet to the Copilot. Code reads it (values only) and suggests a plan
    straight away, so this works with the AI off too; the model can refine it in chat."""
    s = _loaded(request)
    name = (file.filename or "sheet").rsplit("/", 1)[-1][:80]
    if not name.lower().endswith((".csv", ".xlsx", ".xlsm")):
        raise Oops(422, "Attach a .csv or .xlsx file.", "Save the sheet as Excel (.xlsx) or CSV and try again.")  # fmt: skip
    cap = min(s.policy.max_file_mb, 15) * 1024 * 1024
    data = await file.read(cap + 1)
    if len(data) > cap:
        raise Oops(413, f"{name} is larger than {cap // (1024 * 1024)} MB.", "Attach one month at a time, or raise the limit in Settings.")  # fmt: skip
    try:
        headers, rows = transform.read_sheet(data, name)
    except Exception as e:  # a corrupt workbook, a non-sheet file: say so, never crash
        raise Oops(422, f"Couldn't read {name}: {str(e)[:120]}.", "Open it in Excel, save as .xlsx or CSV, and attach it again.") from e  # fmt: skip
    fid = f"S{len(s.sheets) + 1}"
    s.sheets[fid] = {"name": name, "headers": headers, "rows": rows}
    s.log.log("human", "attached a file", f"{fid} {name}: {len(rows)} rows")
    plan = transform.suggest(headers)
    card = None
    try:
        card = _card(
            copilot_mod.propose_transform(_ctx(s), fid, plan, plan.target, by="engine"), s.policy
        )
    except copilot_mod.Refused as e:
        reason = str(e)
    else:
        reason = ""
    return JSONResponse(jsonable_encoder({"file_id": fid, "name": name, "headers": headers,
                                          "rows": len(rows), "proposal": card, "why_not": reason}))  # fmt: skip


class ManualIn(BaseModel):
    bank_rows: list[int]
    note: str
    settlement_id: str | None = None
    by_name: str = ""


@app.post("/api/proposals/manual")
def propose_manual(request: Request, body: ManualIn):
    s = _loaded(request)
    try:
        p = copilot_mod.propose_manual_match(_ctx(s), body.bank_rows, body.note, body.settlement_id or None, by="human")  # fmt: skip
        p.proposer = body.by_name.strip()[:60]
    except copilot_mod.Refused as e:
        raise Oops(422, f"Can't match that: {e}.", "Pick bank lines that add up exactly to the settlement, or leave the settlement empty to explain them.") from e  # fmt: skip
    return JSONResponse(jsonable_encoder({"proposal": _card(p, s.policy)}))


class RuleProposalIn(BaseModel):
    check: str
    note: str
    max_amount: int | None = None


@app.post("/api/proposals/rule")
def propose_rule(request: Request, body: RuleProposalIn):
    s = _loaded(request)
    try:
        p = copilot_mod.propose_rule(_ctx(s), body.check, body.note, body.max_amount, by="human")
    except copilot_mod.Refused as e:
        raise Oops(422, f"Can't make that rule: {e}.", "Pick a suggested rule, or mark single issues as expected from their Fix tab.") from e  # fmt: skip
    return JSONResponse(jsonable_encoder({"proposal": _card(p, s.policy)}))


def _proposal(s: Session, pid: str):
    p = s.proposals.get(pid)
    if p is None:
        raise Oops(404, "That change card no longer exists.", "Ask Settle AI again.")
    if p.status != "pending":
        raise Oops(409, f"That change was already {p.status}.", "Nothing changed.")
    return p


def _apply(s: Session, p, who: str) -> None:
    """Apply a confirmed change card. Code does it; every path re-checks before changing."""
    d = p.payload
    if p.type == "manual_match":
        why = check_manual(s.con, s.result, d["settlement_id"], d["bank_rows"])
        if why:
            raise Oops(409, f"This match no longer fits: {why}.", "Ask Settle AI to look again.")
        m = ManualMatch(d["note"], who, d["bank_rows"], d["settlement_id"])
        s.manual.append(m)
        _reconcile(s, keep=True, why=f"manual match confirmed by {who}")
        p.result = {"done": p.title}
    elif p.type == "rule":
        s.rules.append(Rule(d["check"], d["note"], ref=d["ref"], max_amount=d["max_amount"]))
        _reconcile(s, keep=True, why=f"rule confirmed by {who}: {d['note']}")
        p.result = {"done": p.title}
    elif p.type == "actions":
        done, blocked, before = [], [], {}
        by_key = {_akey(a): a for a in s.gate.actions.values()}
        for key in d["keys"]:
            a = by_key.get(key)  # by identity: ids renumber when the month is reconciled again
            if a is None or a.status not in ("needs_approval", "auto"):
                blocked.append(f"{a.id if a else 'an action'}: not waiting any more")
                continue
            before[key] = a.status
            try:
                if d["decision"] == "reject":
                    a.status, a.reason = "rejected", f"rejected by {who}"
                    s.log.log("human", "rejected", f"{a.id} {a.type}", a.finding_id)
                else:
                    if a.status == "needs_approval":
                        s.gate.approve(a.id, who)
                    if a.status in ("auto", "approved"):
                        s.gate.execute(a.id)
                done.append(a.id)
            except PermissionError as e:
                blocked.append(f"{a.id}: {e}")
        p.result = {"done": done, "blocked": blocked, "before": before}
    elif p.type == "transform":
        sheet = s.sheets.get(d["file_id"])
        if sheet is None:
            raise Oops(409, "The attached file is gone.", "Attach it again.")
        con = duckdb.connect()
        transform.to_table(con, sheet["headers"], sheet["rows"])
        cols, rows = transform.run_plan(con, transform.Plan.model_validate(d["plan"]))
        out = s.dir / f"sheet_{p.id}.csv"
        transform.write(cols, rows, out)
        p.result = {"download": f"/api/proposals/{p.id}/sheet.csv", "rows": len(rows)}
        if d["use_as"]:
            before = dict(s.files)
            s.files = {**s.files, d["use_as"]: out}
            try:
                _reconcile(
                    s, keep=True, why=f"{sheet['name']} used as the {d['use_as']} input by {who}"
                )
            except Oops:
                s.files = before
                _reconcile(s, keep=True, why="the sheet didn't load; kept the previous files")
                raise
            s.source, s.truth = "upload", None
            p.result["previous"] = str(before[d["use_as"]])
    p.status, p.decided_by = "confirmed", who
    s.log.log("human" if not who.startswith("auto-accept") else "rule", "confirmed", f"{p.id} {p.type}: {p.title}")  # fmt: skip


class ConfirmIn(BaseModel):
    approver: str
    always: bool = False  # "Always allow this kind": a grant in the approval policy


@app.post("/api/proposals/{pid}/confirm")
def confirm_proposal(request: Request, pid: str, body: ConfirmIn):
    """The only way a change card is applied: a named person confirms, and code does it."""
    s = _loaded(request)
    sid, _ = _session(request)
    who = body.approver.strip()[:60]
    if not who:
        raise Oops(
            400, "Type your name to confirm.", "Every change is recorded with who confirmed it."
        )
    p = _proposal(s, pid)
    mc = s.policy.maker_checker_over
    if mc is not None and p.amount > mc:
        if p.proposer and p.proposer.lower() == who.lower():
            raise Oops(403, "You made this change, so someone else has to approve it.", "The two-person rule is on in Settings.")  # fmt: skip
        if who.lower() in (x.lower() for x in p.approvals):
            raise Oops(403, "You already approved this.", "A second, different person has to confirm it.")  # fmt: skip
        p.approvals.append(who)
        # Two people in all: a person who prepared it is the maker, so one other approver
        # completes it; an AI- or system-made change needs two people to approve.
        if len(p.approvals) < (1 if p.proposer else 2):
            s.log.log("human", "first approval", f"{p.id} {p.type}: {p.title} by {who}")
            return _respond(sid, {"proposal": _card(p, s.policy), "needs_second": True, "view": _view(s) | {"loaded": True}})  # fmt: skip
        who = " and ".join(p.approvals)
    if body.always:
        try:
            g = copilot_mod.grant(s.policy, p, who)
        except copilot_mod.Refused as e:
            raise Oops(403, str(e).capitalize() + ".", "Allow it once instead.") from e
        s.log.log("human", "always allow", f"{g['kind']} up to {g['max']} paise, granted by {who}")
    _apply(s, p, who)
    return _respond(sid, {"proposal": _card(p, s.policy), "view": _view(s) | {"loaded": True}})


@app.post("/api/proposals/{pid}/undo")
def undo_proposal(request: Request, pid: str, body: ApproveIn):
    """Reverse a confirmed change, like a checkpoint. Code reverses it; it's a new log entry."""
    s = _loaded(request)
    sid, _ = _session(request)
    who = body.approver.strip()[:60] or "a reviewer"
    p = s.proposals.get(pid)
    if p is None or p.status != "confirmed":
        raise Oops(409, "Only a confirmed change can be undone.", "Nothing changed.")
    d = p.payload
    if p.type == "manual_match":
        s.manual = [m for m in s.manual if not (m.bank_rows == d["bank_rows"] and m.settlement_id == d["settlement_id"])]  # fmt: skip
    elif p.type == "rule":
        s.rules = [r for r in s.rules if not (r.check == d["check"] and r.note == d["note"] and r.ref == d["ref"])]  # fmt: skip
    elif p.type == "actions":
        by_key = {_akey(a): a for a in s.gate.actions.values()}
        for key, status in p.result.get("before", {}).items():
            a = by_key.get(key)
            if a is not None:
                a.status, a.approved_by, a.sent, a.reason = status, "", None, f"undone by {who}"
    elif p.type == "transform" and p.result.get("previous"):
        s.files = {**s.files, d["use_as"]: Path(p.result["previous"])}
    if p.type in ("manual_match", "rule") or (p.type == "transform" and p.result.get("previous")):
        _reconcile(s, keep=True, why=f"undo of {p.id} by {who}")
    p.status, p.decided_by = "undone", who
    s.log.log("human", "undid", f"{p.id} {p.type}: {p.title}")
    return _respond(sid, {"proposal": _card(p, s.policy), "view": _view(s) | {"loaded": True}})


@app.post("/api/proposals/{pid}/reject")
def reject_proposal(request: Request, pid: str, body: ApproveIn):
    s = _loaded(request)
    p = _proposal(s, pid)
    p.status, p.decided_by = "rejected", body.approver.strip()[:60] or "a reviewer"
    s.log.log("human", "rejected", f"{p.id} {p.type}: {p.title}")
    return JSONResponse(jsonable_encoder({"proposal": _card(p, s.policy)}))


@app.get("/api/proposals")
def list_proposals(request: Request):
    """Every change card this session, newest first: for Approvals and undo."""
    s = _loaded(request)
    return JSONResponse(
        jsonable_encoder([_card(p, s.policy) for p in reversed(s.proposals.values())])
    )


@app.get("/api/proposals/{pid}/sheet.csv")
def proposal_sheet(request: Request, pid: str):
    s = _loaded(request)
    path = s.dir / f"sheet_{pid}.csv"
    if not path.exists():
        raise Oops(404, "That file isn't ready.", "Confirm the change card first.")
    return FileResponse(path, media_type="text/csv", filename=f"settle_sheet_{pid}.csv")


# --- Settings: the approval policy ------------------------------------------------------------


def _policy_view(s: Session) -> dict:
    p = s.policy
    preview = None
    if s.gate is not None:  # what this month's proposals would do under this policy
        probe = Gate(s.gate.findings, p)
        auto = [a for a in s.gate.actions.values() if probe._decide(a)[0] == "auto"]
        preview = {
            "auto": len(auto),
            "of": len(s.gate.actions),
            "amount": sum(a.amount for a in auto),
        }
    return {
        "copilot_mode": p.copilot_mode, "modes": list(MODES),
        "kinds": [{"kind": k, "mode": p.kinds.get(k, "ask"), "auto_max": p.limit(k),
                   "locked": LOCKED.get(k)} for k in POLICY_KINDS],
        "hard_cap": p.approval_over, "hard_cap_max": HARD_CAP,
        "maker_checker_over": p.maker_checker_over, "grants": p.grants,
        "ai_calls_per_hour": p.ai_calls_per_hour, "max_tool_steps": p.max_tool_steps,
        "max_file_mb": p.max_file_mb, "ai_enabled": p.ai_enabled, "preview": preview,
    }  # fmt: skip


def _policy_diff(old: dict, new: dict) -> list[str]:
    """What changed, in words a finance person reads: "Manual match: ask → never"."""
    out = []
    before = {k["kind"]: k for k in old["kinds"]}
    for k in new["kinds"]:
        b = before[k["kind"]]
        name = k["kind"].replace("_", " ").capitalize()
        if b["mode"] != k["mode"]:
            out.append(f"{name}: {b['mode']} → {k['mode']}")
        elif b["auto_max"] != k["auto_max"] and k["mode"] == "auto":
            out.append(f"{name} auto limit: {rupees(b['auto_max'])} → {rupees(k['auto_max'])}")
    for key, label, fmt in [("copilot_mode", "Settle AI mode", str), ("hard_cap", "Hard cap", rupees),
                            ("maker_checker_over", "Two-person rule above", lambda v: "off" if v is None else rupees(v)),
                            ("ai_calls_per_hour", "AI requests per hour", str), ("max_tool_steps", "Max tool steps", str),
                            ("max_file_mb", "Max file MB", str), ("ai_enabled", "AI on", str)]:  # fmt: skip
        if old[key] != new[key]:
            out.append(f"{label}: {fmt(old[key])} → {fmt(new[key])}")
    if len(old["grants"]) > len(new["grants"]):
        out.append("revoked an always-allow permission")
    return out


@app.get("/api/policy")
def get_policy(request: Request):
    _, s = _session(request, create=True)
    return JSONResponse(jsonable_encoder(_policy_view(s)))


class PolicyIn(BaseModel):
    model_config = {"extra": "forbid"}  # a misspelt setting must fail loudly, not look saved

    approver: str
    copilot_mode: str | None = None
    kinds: dict[str, str] | None = None
    auto_max: dict[str, int] | None = None
    hard_cap: int | None = None
    maker_checker_over: int | None = None
    maker_checker: bool | None = None
    ai_calls_per_hour: int | None = None
    max_tool_steps: int | None = None
    max_file_mb: int | None = None
    ai_enabled: bool | None = None
    revoke_grant: int | None = None


@app.post("/api/policy")
def set_policy(request: Request, body: PolicyIn):
    """Only a person edits the policy; the model has no route here. Each edit is checked as a
    whole (locked kinds can't be made automatic, the hard cap only goes down) and logged with the
    person's name and the before/after."""
    sid, s = _session(request, create=True)
    who = body.approver.strip()[:60]
    if not who:
        raise Oops(400, "Type your name to change the policy.", "Policy changes are recorded with who made them.")  # fmt: skip
    old = _policy_view(s)
    p = Policy(**{k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                  for k, v in vars(s.policy).items()})  # fmt: skip
    if body.copilot_mode is not None:
        p.copilot_mode = body.copilot_mode
    if body.kinds:
        p.kinds.update(body.kinds)
    if body.auto_max:
        p.auto_max.update(body.auto_max)
        if "journal_post" in body.auto_max:
            p.auto_journal_max = body.auto_max["journal_post"]
    if body.hard_cap is not None:
        p.approval_over = body.hard_cap
    if body.maker_checker is not None:
        p.maker_checker_over = (body.maker_checker_over if body.maker_checker_over is not None else 100_000) if body.maker_checker else None  # fmt: skip
    elif body.maker_checker_over is not None:
        p.maker_checker_over = body.maker_checker_over
    for k in ("ai_calls_per_hour", "max_tool_steps", "max_file_mb", "ai_enabled"):
        if getattr(body, k) is not None:
            setattr(p, k, getattr(body, k))
    if not 1 <= p.ai_calls_per_hour <= 500 or not 1 <= p.max_tool_steps <= 10 or not 1 <= p.max_file_mb <= 15:  # fmt: skip
        raise Oops(422, "A limit is out of range.", "AI calls 1-500 an hour, tool steps 1-10, file size 1-15 MB.")  # fmt: skip
    if body.revoke_grant is not None:
        if not 0 <= body.revoke_grant < len(p.grants):
            raise Oops(404, "That permission no longer exists.", "Reload Settings.")
        gone = p.grants.pop(body.revoke_grant)
        s.log.log("human", "revoked always-allow", f"{gone['kind']} up to {gone['max']} paise, by {who}")  # fmt: skip
    errs = p.check()
    if errs:
        raise Oops(422, f"Can't save that: {errs[0]}.", "Locked rows stay as they are; the hard cap can only be lowered.")  # fmt: skip
    s.policy = p
    if s.gate is not None:  # undecided actions are decided again under the new policy
        s.gate.policy = p
        for aid, a in list(s.gate.actions.items()):
            if a.status in ("needs_approval", "auto") or (a.status == "blocked" and "policy" in a.reason):  # fmt: skip
                del s.gate.actions[aid]
                a.status, a.reason = s.gate._decide(a)
                s.gate.actions[aid] = a
    new = _policy_view(s)
    s.log.log("human", "changed the approval policy", f"by {who}: " + ("; ".join(_policy_diff(old, new)) or "no change"))  # fmt: skip
    return _respond(sid, new)
