"""Rules a person adds when a finding is expected ("coupon at checkout"). The engine applies
them after its checks: a matching finding leaves the problem list and shows under "Resolved by
your rules", and still has its own line in the bridge so nothing goes unexplained.

Stored as a JSON list; in the demo the path is per session.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path


@dataclass
class Rule:
    check: str
    note: str
    ref: str | None = None  # pattern matched against the finding's refs, e.g. "KK-11*"
    max_amount: int | None = None  # paise: only findings up to this size
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def matches(self, f) -> bool:
        return (
            f.check == self.check
            and (self.max_amount is None or abs(f.amount) <= self.max_amount)
            and (self.ref is None or any(fnmatch(r, self.ref) for r in f.refs if r))
        )


@dataclass
class ManualMatch:
    """A person's decision on bank lines the engine wouldn't guess: link them to a settlement
    (they must add up to its net exactly) or, with no settlement, say what they are ("prior-month
    settlement"). It never changes an amount; the engine re-checks the tie when it applies it."""

    note: str
    by: str
    bank_rows: list[int]
    settlement_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


SAFE_TO_SUGGEST = ("amount_mismatch", "gst_mismatch", "settlement_over", "orphan_payment",
                   "refund_not_in_books", "ghost_success")  # fmt: skip


def suggest_rules(findings) -> list[dict]:
    """Rules worth offering: a books-side check that fired twice or more this month. Money-loss
    checks are never suggested; a person can still mark one of those as expected by hand."""
    by: dict[str, list] = {}
    for f in findings:
        if f.check in SAFE_TO_SUGGEST:
            by.setdefault(f.check, []).append(f)
    out = []
    for check, fs in by.items():
        if len(fs) < 2:
            continue
        cap = max(abs(f.amount) for f in fs)
        out.append({"check": check, "title": fs[0].title, "count": len(fs), "max_amount": cap,
                    "finding_ids": [f.id for f in fs]})  # fmt: skip
    return out


def load_rules(path: Path) -> list[Rule]:
    return [Rule(**r) for r in json.loads(path.read_text())] if path.exists() else []


def add_rule(path: Path, rule: Rule) -> list[Rule]:
    rules = [*load_rules(path), rule]
    path.write_text(json.dumps([asdict(r) for r in rules], indent=2))
    return rules
