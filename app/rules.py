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


def load_rules(path: Path) -> list[Rule]:
    return [Rule(**r) for r in json.loads(path.read_text())] if path.exists() else []


def add_rule(path: Path, rule: Rule) -> list[Rule]:
    rules = [*load_rules(path), rule]
    path.write_text(json.dumps([asdict(r) for r in rules], indent=2))
    return rules
