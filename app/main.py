"""The web app: a JSON API over the engine and one static page.

Each browser gets a session (cookie) holding its own DuckDB connection, result, rules, action
gate and decision log, kept in memory for SESSION_TTL. Loading data never calls the model:
explanations and drafts come from the sample cache or the per-check templates, and the model is
only called when a person asks (Ask, "Explain with AI", reading a PDF), behind a per-IP limit.

Every error leaves as {"error": plain English, "hint": what to do next}; never a stack trace.
"""

import io
import logging
import random
import shutil
import tempfile
import time
import uuid
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import drafts as drafts_mod
from app import explain as explain_mod
from app import journal, sample
from app.actions import propose_all
from app.ask import ask
from app.audit import DecisionLog, pack, render
from app.config import settings
from app.engine import KINDS, Result, run
from app.gate import Gate
from app.llm import GeminiLLM, Usage
from app.load import REQUIRED, LoadError, load
from app.money import to_paise
from app.pdf_bank import PdfLocked, check, extract, pages, write_csv
from app.roi import roi
from app.rules import Rule
from app.trace import trace

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample"
STATIC = Path(__file__).resolve().parent / "static"

SESSION_TTL = 60 * 60  # seconds idle before a session is dropped
MAX_SESSIONS = 200
MAX_UPLOAD = 15 * 1024 * 1024  # bytes per file
AI_CALLS_PER_WINDOW, AI_WINDOW = 20, 10 * 60  # per IP: 20 model-backed requests / 10 min
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


def _llm() -> GeminiLLM:
    if not settings.gemini_api_key:
        raise Oops(
            503,
            "The AI isn't available right now.",
            "Everything else still works; explanations are written from templates.",
        )
    return GeminiLLM()


# --- the pipeline ----------------------------------------------------------------------------


def _reconcile(s: Session) -> None:
    """Load the session's three files, run the engine, and rebuild everything downstream."""
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
    result = run(con, period, rules=s.rules)
    exp_cache = explain_mod.load_cache(explain_mod.SAMPLE_CACHE)
    draft_cache = drafts_mod.load_cache(drafts_mod.SAMPLE_CACHE)
    exp_cache |= (
        s.ai_explained
    )  # keep what a person asked the model for, if the finding is unchanged
    s.con, s.result = con, result
    s.explanations = explain_mod.explain_all(result, None, exp_cache)
    s.drafts = drafts_mod.draft_all(result, None, draft_cache)
    s.log = DecisionLog()
    s.log.record_run(result, s.files)
    s.log.record_ai(s.explanations, s.drafts)
    for r in s.rules:
        s.log.log("human", "added rule", f"{r.check}: {r.note}")
    s.gate = Gate({f.id: f for f in result.findings}, log=s.log)
    s.journals = journal.entries(con, result)
    propose_all(s.gate, result, s.journals, s.drafts)


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
            "available": bool(settings.gemini_api_key),
            "templates": sum(1 for x in exps.values() if x.source != "model"),
        },
        "chaos": _compare(r, s.truth) if s.truth else None,
        "seed": s.truth.get("seed") if s.truth else None,
        "rules": [{"check": x.check, "note": x.note, "ref": x.ref} for x in s.rules],
        "demo_mode": settings.demo_mode,
    }


# --- endpoints: loading data -----------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "ai": bool(settings.gemini_api_key), "demo_mode": settings.demo_mode}


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
    paths, truth = sample.chaos(seed)
    s.files = {"razorpay": paths["rp"], "bank": paths["bank"], "orders": paths["orders"]}
    s.source, s.truth, s.rules, s.ai_explained = "chaos", truth | {"seed": seed}, [], {}
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
    _reconcile(s)
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


@app.get("/api/report", response_class=HTMLResponse)
def report(request: Request):
    s = _loaded(request)
    return HTMLResponse(_report_html(s))


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
    paths = pack(s.result, s.journals, out, s.log)
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
