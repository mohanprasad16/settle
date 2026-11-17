"""Reads the three files into DuckDB tables with money in integer paise.

Tables: rp (Razorpay recon rows), bank, orders. Each keeps `row_no` (1 = first data row of the
file) so every finding can point at the exact lines it came from.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

REQUIRED = {
    "rp": ["entity_id", "type", "debit", "credit", "amount", "fee", "tax", "created_at",
           "settled_at", "settlement_id", "settlement_utr", "order_receipt", "payment_id"],
    "bank": ["txn_date", "narration", "ref_no", "debit", "credit"],
    "orders": ["order_no", "order_date", "amount", "payment_method", "status", "refund_amount",
               "refund_date"],
}  # fmt: skip
OPTIONAL = {
    "rp": ["description", "order_id", "method", "card_network", "card_type", "dispute_id"],
    "bank": ["balance"],
    "orders": ["customer"],
}

LABEL = {"rp": "Razorpay settlement report", "bank": "bank statement", "orders": "orders export"}


class LoadError(ValueError):
    """A file is missing columns or has values we can't read. Message is user-facing."""


@dataclass
class Period:
    start: date
    end: date


def _paise(col: str) -> str:
    """Rupee text ('1,234.50', '', '-') -> BIGINT paise."""
    clean = f"NULLIF(trim(replace(replace({col}, ',', ''), '₹', '')), '')"
    return f"COALESCE(CAST(round(CAST({clean} AS DECIMAL(18, 2)) * 100) AS BIGINT), 0)"


def _network(col: str) -> str:
    """Card networks as the rate card names them, whatever the export calls them."""
    n = f"upper(trim({col}))"
    return f"""CASE WHEN {n} IN ('AMEX', 'AMERICAN EXPRESS') THEN 'American Express'
        WHEN {n} LIKE '%DINERS%' THEN 'Diners Club' WHEN {n} = 'RUPAY' THEN 'RuPay'
        WHEN {n} = 'VISA' THEN 'Visa' WHEN {n} IN ('MASTERCARD', 'MASTER CARD', 'MC') THEN 'MasterCard'
        ELSE NULLIF(trim({col}), '') END"""


def _int(col: str) -> str:
    return f"COALESCE(CAST(NULLIF(trim({col}), '') AS BIGINT), 0)"


def _ist(col: str) -> str:
    """Unix seconds -> naive IST timestamp (no timezone extension needed)."""
    return f"make_timestamp((CAST(NULLIF(trim({col}), '') AS BIGINT) + 19800) * 1000000)"


def _date(col: str) -> str:
    formats = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y"]
    tries = ", ".join(f"try_strptime(trim({col}), '{f}')" for f in formats)
    return f"CAST(COALESCE({tries}) AS DATE)"


def _read(con, name: str, path: Path) -> None:
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE raw_{name} AS "
        "SELECT row_number() OVER () AS row_no, * FROM read_csv(?, all_varchar = true, header = true)",
        [str(path)],
    )
    cols = {c[0].strip().lower(): c[0] for c in con.execute(f"DESCRIBE raw_{name}").fetchall()}
    missing = [c for c in REQUIRED[name] if c not in cols]
    if missing:
        raise LoadError(f"The {LABEL[name]} is missing columns: {', '.join(missing)}.")
    for c in OPTIONAL[name]:
        if c not in cols:
            con.execute(f"ALTER TABLE raw_{name} ADD COLUMN {c} VARCHAR")


def load(con: duckdb.DuckDBPyConnection, rp: Path, bank: Path, orders: Path) -> Period:
    for name, path in (("rp", rp), ("bank", bank), ("orders", orders)):
        _read(con, name, path)
    try:
        con.execute(f"""
            CREATE OR REPLACE TABLE rp AS SELECT
                row_no, trim(entity_id) AS entity_id, lower(trim(type)) AS type,
                {_int("debit")} AS debit, {_int("credit")} AS credit, {_int("amount")} AS amount,
                {_int("fee")} AS fee, {_int("tax")} AS tax,
                {_ist("created_at")} AS created_at, {_ist("settled_at")} AS settled_at,
                trim(settlement_id) AS settlement_id, NULLIF(trim(settlement_utr), '') AS utr,
                NULLIF(upper(trim(order_receipt)), '') AS order_receipt,
                NULLIF(trim(payment_id), '') AS payment_id, NULLIF(trim(order_id), '') AS order_id,
                NULLIF(trim(description), '') AS description, lower(trim(method)) AS method,
                {_network("card_network")} AS card_network, lower(NULLIF(trim(card_type), '')) AS card_type,
                NULLIF(trim(dispute_id), '') AS dispute_id
            FROM raw_rp""")
        con.execute(f"""
            CREATE OR REPLACE TABLE bank AS SELECT
                row_no, {_date("txn_date")} AS txn_date, trim(narration) AS narration,
                NULLIF(trim(ref_no), '') AS ref_no, {_paise("debit")} AS debit,
                {_paise("credit")} AS credit
            FROM raw_bank""")
        con.execute(f"""
            CREATE OR REPLACE TABLE orders AS SELECT
                row_no, upper(trim(order_no)) AS order_no, {_date("order_date")} AS order_date,
                trim(customer) AS customer, {_paise("amount")} AS amount,
                lower(trim(payment_method)) AS payment_method, lower(trim(status)) AS status,
                {_paise("refund_amount")} AS refund_amount, {_date("refund_date")} AS refund_date
            FROM raw_orders""")
    except duckdb.Error as e:
        raise LoadError(f"Couldn't read a value in one of the files: {e}") from e

    bad = con.execute("SELECT count(*) FROM bank WHERE txn_date IS NULL").fetchone()[0]
    if bad:
        raise LoadError(f"{bad} bank statement rows have a date we couldn't read.")
    first = con.execute("SELECT min(txn_date) FROM bank").fetchone()[0]
    if first is None:
        raise LoadError("The bank statement has no rows.")
    # The statement month is the reconciliation period.
    last_day = calendar.monthrange(first.year, first.month)[1]
    return Period(first.replace(day=1), first.replace(day=last_day))
