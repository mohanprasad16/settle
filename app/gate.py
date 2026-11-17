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


@dataclass
class Policy:
    auto_journal_max: int = 50_000  # paise: ₹500
    approval_over: int = 500_000  # paise: ₹5,000, always needs a person above this
    max_executions: int = 25  # per session


@dataclass
class Action:
    type: str
    amount: int  # paise the action concerns (0 for pure reads)
    payload: dict
    finding_id: str | None = None  # None only for routine fee journals
    idempotency_key: str = ""
    id: str = ""
    status: str = "proposed"  # auto | needs_approval | blocked | approved | executed
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
        if any(x.idempotency_key == a.idempotency_key and x.status != "blocked" for x in self.actions.values()):  # fmt: skip
            return "blocked", "duplicate: this exact action was already proposed"
        if a.finding_id is not None:
            f = self.findings.get(a.finding_id)
            if f is None:
                return "blocked", f"no finding {a.finding_id}"
            if a.amount > abs(f.amount):
                return "blocked", f"amount is above the finding's {abs(f.amount)} paise; the cap is the amount at stake"  # fmt: skip
        if a.type == "journal_post" and a.finding_id is None and a.amount <= p.auto_journal_max:
            return "auto", "routine fee journal under the auto limit"
        if a.amount > p.approval_over:
            return "needs_approval", "above the approval threshold"
        return "needs_approval", "touches money, a customer or Razorpay: a person decides"

    def approve(self, action_id: str, approver: str) -> Action:
        """The only way a non-auto action becomes executable. Called from the user's click."""
        a = self.actions[action_id]
        if not approver.strip():
            raise PermissionError("approval needs a named person")
        if a.status != "needs_approval":
            raise PermissionError(f"{a.id} is {a.status}; only actions waiting for approval can be approved")  # fmt: skip
        a.status, a.approved_by = "approved", approver.strip()
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
