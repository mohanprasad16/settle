"""Text in the files (payment descriptions, customer names, bank narrations) is written by
outsiders: a customer can type anything into a UPI note. It is data, never instructions.

`as_data` is how such text goes into a prompt. `scan` flags records whose text reads like an
instruction to an AI, so a person sees the attempt; it changes nothing else in the run.
"""

import csv
import re
import unicodedata

INSTRUCTION = re.compile(
    r"ignore (all |the )?(previous|prior|above)|system (override|prompt)|"
    r"mark (this|it|as)\b.{0,40}\b(resolved|reconciled|approved)|auto[_ ]?resolved?|"
    r"skip (the )?(human|approval|review)|\bapprove (this|the|all)\b|you are now|"
    r"disregard (your|the|all)",
    re.IGNORECASE,
)
# Every free-text column a third party can fill.
FIELDS = {"rp": ["description"], "bank": ["narration"], "orders": ["customer", "order_no"]}


def as_data(text, limit: int = 200) -> str:
    """Untrusted text as a fenced, single-line, length-capped data value for a prompt."""
    s = "".join(" " if unicodedata.category(c)[0] == "C" else c for c in str(text or ""))
    s = re.sub(r"\s+", " ", s).strip().replace("<", "‹").replace(">", "›")
    if len(s) > limit:
        s = s[:limit] + "…"
    return f"<data>{s}</data>"


def scan(con) -> list[dict]:
    """Records whose free text looks like an instruction. Kind 'risk', for a person to look at."""
    flags = []
    for table, cols in FIELDS.items():
        for col in cols:
            for row_no, text in con.execute(
                f"SELECT row_no, {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY row_no"
            ).fetchall():
                if INSTRUCTION.search(text):
                    flags.append({
                        "kind": "risk",
                        "title": "Suspicious instruction text in a payment record",
                        "source": table, "row_no": row_no, "field": col, "text": as_data(text),
                    })  # fmt: skip
    return flags


_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(v):
    """A cell Excel would run as a formula (=HYPERLINK..., +cmd...) gets a leading apostrophe.
    Numbers like -1234.50 stay numbers. Order numbers and notes come from files and people."""
    if isinstance(v, str) and v.startswith(_FORMULA):
        try:
            float(v.replace(",", ""))
        except ValueError:
            return "'" + v
    return v


class CsvWriter:
    """csv.writer for anything a person will open in Excel: every cell goes through csv_cell."""

    def __init__(self, fh):
        self.w = csv.writer(fh)

    def writerow(self, row) -> None:
        self.w.writerow([csv_cell(v) for v in row])

    def writerows(self, rows) -> None:
        for r in rows:
            self.writerow(r)
