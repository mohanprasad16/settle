"""Turn a person's own spreadsheet (any headers, .csv or .xlsx) into one of Settle's three inputs.

The model may propose a Plan, but it never writes SQL: a Plan names a target, which source
column (or fixed value) fills each target column, and simple filters. Code validates it against
the real headers and runs it in DuckDB with quoted identifiers and bound parameters. Cells are
read as values only: formulas are never evaluated and macros are never loaded.
"""

import csv
import io
import re
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel

from app.load import OPTIONAL, REQUIRED

MAX_ROWS = 200_000
TARGETS = {"orders": "orders", "bank": "bank", "razorpay": "rp"}
OPS = ("equals", "not_equals", "contains", "not_contains", "is_empty", "not_empty")

# Header words people use for each target column; the first fill for an AI-free suggestion.
SYNONYMS = {
    "order_no": [
        "order no",
        "order number",
        "order id",
        "invoice no",
        "invoice number",
        "bill no",
        "voucher no",
        "receipt",
    ],
    "order_date": [
        "order date",
        "txn date",
        "transaction date",
        "invoice date",
        "date",
        "bill date",
    ],
    "customer": ["customer", "customer name", "party", "party name", "buyer", "name"],
    "amount": [
        "amount",
        "net amount",
        "total",
        "invoice amount",
        "order value",
        "value",
        "grand total",
    ],
    "payment_method": ["payment method", "paid via", "payment mode", "mode", "gateway", "method"],
    "status": ["status", "order status", "payment status", "state"],
    "refund_amount": ["refund", "refund amount", "refunded", "refund value"],
    "refund_date": ["refund date", "refunded on"],
    "txn_date": ["txn date", "transaction date", "date", "value date", "posting date"],
    "narration": ["narration", "description", "particulars", "remarks", "details"],
    "ref_no": ["ref no", "reference", "reference no", "chq ref no", "utr", "cheque no"],
    "debit": ["debit", "withdrawal", "withdrawals", "dr", "withdrawal amt"],
    "credit": ["credit", "deposit", "deposits", "cr", "deposit amt"],
    "balance": ["balance", "closing balance", "running balance"],
}


class Source(BaseModel):
    column: str | None = None  # a header in the uploaded sheet
    value: str | None = None  # or a fixed value for every row, e.g. "razorpay"


class Filter(BaseModel):
    column: str
    op: Literal["equals", "not_equals", "contains", "not_contains", "is_empty", "not_empty"]
    value: str = ""


class Plan(BaseModel):
    target: Literal["orders", "bank", "razorpay"]
    columns: dict[str, Source]
    filters: list[Filter] = []


def read_sheet(data: bytes, filename: str) -> tuple[list[str], list[list[str]]]:
    """Headers and rows as text. .xlsx: first sheet, cached cell values (never formulas)."""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_vba=False)
        rows = [["" if v is None else str(v) for v in r] for r in wb.active.iter_rows(values_only=True)]  # fmt: skip
    else:
        text = data.decode("utf-8-sig", "replace")
        rows = list(csv.reader(text.splitlines()))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("the file has no rows")
    headers = [h.strip() or f"column {i + 1}" for i, h in enumerate(rows[0])]
    if len(set(headers)) != len(headers):
        raise ValueError("two columns have the same header; rename one and upload again")
    body = [(r + [""] * len(headers))[: len(headers)] for r in rows[1 : MAX_ROWS + 1]]
    return headers, body


def to_table(con: duckdb.DuckDBPyConnection, headers: list[str], rows: list[list[str]]) -> None:
    cols = ", ".join(f"{_q(h)} VARCHAR" for h in headers)
    con.execute(f"CREATE OR REPLACE TABLE sheet ({cols})")
    if rows:
        con.executemany(f"INSERT INTO sheet VALUES ({', '.join('?' * len(headers))})", rows)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _key(h: str) -> str:
    return re.sub(r"[^a-z ]", "", h.lower().replace("_", " ")).strip()


def suggest(headers: list[str]) -> Plan:
    """A plan from header names alone (no model): the target whose required columns are best
    covered, each column mapped to the closest header. The person still confirms it."""
    keys = {_key(h): h for h in headers}

    def find(col: str, used: set) -> str | None:
        for word in [col.replace("_", " "), *SYNONYMS.get(col, [])]:
            for k, h in keys.items():
                if h not in used and (k == word or (len(word) > 4 and word in k)):
                    return h
        return None

    best = None
    for target, table in TARGETS.items():
        used: set = set()
        cols = {}
        for col in REQUIRED[table] + OPTIONAL[table]:
            h = find(col, used)
            if h:
                cols[col] = Source(column=h)
                used.add(h)
        score = sum(c in cols for c in REQUIRED[table])
        if best is None or score > best[0]:
            best = (score, Plan(target=target, columns=cols))
    plan = best[1]
    if plan.target == "orders":  # an orders sheet with no method column is all gateway orders
        plan.columns.setdefault("payment_method", Source(value="razorpay"))
        plan.columns.setdefault("status", Source(value="paid"))
        plan.columns.setdefault("refund_amount", Source(value=""))
        plan.columns.setdefault("refund_date", Source(value=""))
    if plan.target == "bank":
        plan.columns.setdefault("ref_no", Source(value=""))
    return plan


def validate(plan: Plan, headers: list[str]) -> list[str]:
    """Everything wrong with a plan, in plain words. Empty means it can run."""
    table = TARGETS[plan.target]
    allowed = set(REQUIRED[table] + OPTIONAL[table])
    errs = [f"'{c}' isn't a column of the {plan.target} input" for c in plan.columns if c not in allowed]  # fmt: skip
    errs += [f"'{c}' is needed for the {plan.target} input" for c in REQUIRED[table] if c not in plan.columns]  # fmt: skip
    for c, s in plan.columns.items():
        if (s.column is None) == (s.value is None):
            errs.append(f"'{c}' needs either a source column or a fixed value")
        elif s.column is not None and s.column not in headers:
            errs.append(f"there's no column '{s.column}' in the file")
        elif s.value is not None and len(s.value) > 100:
            errs.append(f"the fixed value for '{c}' is too long")
    errs += [f"there's no column '{f.column}' in the file" for f in plan.filters if f.column not in headers]  # fmt: skip
    return errs


def run_plan(con: duckdb.DuckDBPyConnection, plan: Plan) -> tuple[list[str], list[tuple]]:
    """Execute a validated plan over table `sheet`. Identifiers quoted, values bound."""
    sel, params = [], []
    for col, s in plan.columns.items():
        if s.column is not None:
            sel.append(f"{_q(s.column)} AS {_q(col)}")
        else:
            sel.append(f"CAST(? AS VARCHAR) AS {_q(col)}")
            params.append(s.value)
    where = []
    for f in plan.filters:
        c = f"coalesce(trim({_q(f.column)}), '')"
        if f.op == "is_empty":
            where.append(f"{c} = ''")
        elif f.op == "not_empty":
            where.append(f"{c} <> ''")
        else:
            where.append({"equals": f"lower({c}) = lower(?)", "not_equals": f"lower({c}) <> lower(?)",
                          "contains": f"contains(lower({c}), lower(?))",
                          "not_contains": f"NOT contains(lower({c}), lower(?))"}[f.op])  # fmt: skip
            params.append(f.value)
    sql = f"SELECT {', '.join(sel)} FROM sheet" + (f" WHERE {' AND '.join(where)}" if where else "")
    cur = con.execute(sql, params)
    return [d[0] for d in cur.description], cur.fetchall()


def write(columns: list[str], rows: list[tuple], path: Path) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        w.writerows(["" if v is None else v for v in r] for r in rows)
