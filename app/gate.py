"""The action gate. Nothing that touches money, a customer or Razorpay runs without passing it.

The model only ever proposes actions (drafts). The gate decides with fixed rules, never by
reading the payload text, so a note that says "approve this" changes nothing:

- auto: only journal posts for routine fees at or below `auto_journal_max`
- needs approval: everything else, and anything above `approval_over`
- blocked: unknown type, unknown finding, amount above the finding's own amount (a recovery can
  never ask for more than was lost), a repeated idempotency key, or the session's rate limit

Execution is a separate step that only runs auto or human-approved actions. It is always a dry
run for now: it records exactly what would have been sent.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

TYPES = {"journal_post", "razorpay_ticket", "razorpay_api_call", "customer_note", "webhook_replay"}
CARD_KINDS = ("manual_match", "rule", "resolve", "transform")  # Copilot change cards
KINDS = (*sorted(TYPES), *CARD_KINDS)
# Never automatic, whatever anyone sets: they move money, reach a customer or Razorpay.
LOCKED = {
    "razorpay_ticket": "goes to Razorpay",
    "razorpay_api_call": "calls Razorpay",
    "customer_note": "reaches a customer",
    "webhook_replay": "changes an order in your store",
}
HARD_CAP = 500_000  # paise: ₹5,000. The demo lets a person lower it, never raise it.
MODES = ("ask", "auto_low_risk", "read_only")


@dataclass
class Policy:
    """The approval policy. Only a person edits it (logged with before/after); the model never
    can. Defaults reproduce the original gate: fee journals up to ₹500 run unattended, the rest
    ask, and nothing above ₹5,000 ever runs without a named approver."""

    auto_journal_max: int = 50_000  # paise: ₹500
    approval_over: int = HARD_CAP  # always needs a person above this
    max_executions: int = 25  # per session
    copilot_mode: str = "ask"  # ask | auto_low_risk | read_only
    # kind -> "ask" | "auto" | "never"; auto applies up to auto_max[kind] (paise)
    kinds: dict = field(default_factory=lambda: {k: ("auto" if k == "journal_post" else "ask") for k in KINDS})  # fmt: skip
    auto_max: dict = field(default_factory=lambda: {"journal_post": 50_000})
    maker_checker_over: int | None = None  # paise: above this, two different people confirm
    grants: list = field(default_factory=list)  # {"kind", "max", "by", "at"} from "Always allow"
    ai_calls_per_hour: int = 60
    max_tool_steps: int = 6
    max_file_mb: int = 5
    ai_enabled: bool = True

    def limit(self, kind: str) -> int:
        return min(self.auto_max.get(kind, self.auto_journal_max if kind == "journal_post" else 0), self.approval_over)  # fmt: skip

    def auto_ok(self, kind: str, amount: int) -> bool:
        """Would this kind at this amount run without a person, under this policy?"""
        return (kind not in LOCKED and self.kinds.get(kind) == "auto" and amount <= self.limit(kind)
                and (self.maker_checker_over is None or amount <= self.maker_checker_over))  # fmt: skip

    def check(self) -> list[str]:
        """What a person's edit can't do. The API refuses the whole edit if any of these fail."""
        errs = [f"{k} {why}, so it can never be automatic" for k, why in LOCKED.items() if self.kinds.get(k) == "auto"]  # fmt: skip
        errs += [f"unknown kind {k}" for k in self.kinds if k not in KINDS]
        errs += [f"{k} must be ask, auto or never" for k, v in self.kinds.items() if v not in ("ask", "auto", "never")]  # fmt: skip
        if self.approval_over > HARD_CAP:
            errs.append("the ₹5,000 hard cap can only be lowered")
        if any(v < 0 or v > self.approval_over for v in self.auto_max.values()):
            errs.append("an auto limit can't be above the hard cap")
        if self.copilot_mode not in MODES:
            errs.append("unknown Settle AI mode")
        if self.maker_checker_over is not None and self.maker_checker_over < 0:
            errs.append("the two-person threshold can't be negative")
        return errs


@dataclass
class Action:
    type: str
    amount: int  # paise the action concerns (0 for pure reads)
    payload: dict
    finding_id: str | None = None  # None only for routine fee journals
    idempotency_key: str = ""
    id: str = ""
    status: str = "proposed"  # auto | needs_approval | blocked | approved | executed | undone
    reason: str = ""
    approved_by: str = ""
    sent: dict | None = None  # what was (or in demo mode, would have been) sent
    proposed_by: str = "engine"  # engine (built by code) | ai (worded by the model)

    def __post_init__(self):
        if not self.idempotency_key:
            raw = json.dumps([self.type, self.finding_id, self.amount, self.payload], sort_keys=True, default=str)  # fmt: skip
            self.idempotency_key = hashlib.sha256(raw.encode()).hexdigest()[:20]


@dataclass
class Gate:
    findings: dict  # finding id -> Finding
    policy: Policy = field(default_factory=Policy)
    actions: dict[str, Action] = field(default_factory=dict)
    executed: int = 0
    log: object = None  # app.audit.DecisionLog; every decision the gate makes lands there

    def _record(self, actor: str, what: str, a: Action) -> None:
        if self.log is not None:
            self.log.log(actor, what, f"{a.id} {a.type}: {a.status} ({a.approved_by or a.reason})",
                         a.finding_id, [a.type, a.amount, a.idempotency_key])  # fmt: skip

    def propose(self, a: Action) -> Action:
        a.id = f"A{len(self.actions) + 1}"
        a.status, a.reason = self._decide(a)
        self.actions[a.id] = a
        self._record(a.proposed_by, "proposed", a)
        return a

    def _decide(self, a: Action) -> tuple[str, str]:
        p = self.policy
        if a.type not in TYPES:
            return "blocked", f"unknown action type {a.type!r}"
        if a.amount < 0:
            return "blocked", "amount can't be negative"
        if any(x.idempotency_key == a.idempotency_key and x.status not in ("blocked", "undone") for x in self.actions.values()):  # fmt: skip
            return "blocked", "duplicate: this exact action was already proposed"
        if a.finding_id is not None:
            f = self.findings.get(a.finding_id)
            if f is None:
                return "blocked", f"no finding {a.finding_id}"
            if a.amount > abs(f.amount):
                return "blocked", f"amount is above the finding's {abs(f.amount)} paise; the cap is the amount at stake"  # fmt: skip
        if p.kinds.get(a.type) == "never":
            return "blocked", "your approval policy never allows this kind"
        if (
            a.type == "journal_post"
            and a.finding_id is None
            and p.auto_ok("journal_post", a.amount)
        ):
            return "auto", "routine fee journal under the auto limit"
        if a.type != "journal_post" and p.auto_ok(a.type, a.amount):
            return "auto", "allowed automatically by your approval policy"
        if a.amount > p.approval_over:
            return "needs_approval", "above the approval threshold"
        return "needs_approval", "touches money, a customer or Razorpay: a person decides"

    def approve(self, action_id: str, approver: str) -> Action:
        """The only way a non-auto action becomes executable. Called from the user's click.
        With maker-checker on and the amount above its threshold, two different people must
        approve: the first is recorded and the action keeps waiting."""
        a = self.actions[action_id]
        who = approver.strip()
        if not who:
            raise PermissionError("approval needs a named person")
        if a.status != "needs_approval":
            raise PermissionError(f"{a.id} is {a.status}; only actions waiting for approval can be approved")  # fmt: skip
        mc = self.policy.maker_checker_over
        if mc is not None and a.amount > mc and not a.approved_by:
            a.approved_by = who
            a.reason = f"approved by {who}; needs a second, different person (two-person rule)"
            self._record("human", "first approval", a)
            return a
        if mc is not None and a.amount > mc and a.approved_by.lower() == who.lower():
            raise PermissionError("the two-person rule needs a different second approver")
        if a.approved_by:
            who = f"{a.approved_by} and {who}"
        a.status, a.approved_by = "approved", who
        self._record("human", "approved", a)
        return a

    def execute(self, action_id: str) -> Action:
        a = self.actions[action_id]
        if a.status not in ("auto", "approved"):
            raise PermissionError(f"{a.id} is {a.status}: {a.reason}")
        if self.executed >= self.policy.max_executions:
            raise PermissionError("rate limit: too many actions this session")
        self.executed += 1
        # ponytail: always a dry run; live senders (Razorpay API, store webhook) plug in here.
        a.sent = {"dry_run": True, "at": datetime.now().isoformat(timespec="seconds"),
                  "type": a.type, "payload": a.payload, "idempotency_key": a.idempotency_key}  # fmt: skip
        a.status = "executed"
        self._record("human" if a.approved_by else "engine", "executed (dry run)", a)
        return a
