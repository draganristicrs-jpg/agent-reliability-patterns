"""Approval gate: the agent proposes, a human approves irreversible actions.

Pattern write-up: https://orbiresearch.com/lab/approval-queue-pattern
Payments variant: https://orbiresearch.com/lab/agent-payment-authorization-pattern

Rules this module enforces:
- Reversible, low-value actions run immediately.
- Irreversible actions, or anything above a value ceiling, go to a queue.
- Nothing in the queue runs until a named person approves it.
- Every decision is recorded with who made it and why.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Action:
    name: str
    params: Dict[str, Any]
    irreversible: bool = False
    value: float = 0.0            # money or other risk measure, 0 if none
    reason: str = ""              # the agent's stated reason, kept for audit


@dataclass
class Decision:
    action: Action
    status: str                    # "executed" | "pending" | "rejected"
    decided_by: Optional[str] = None
    note: str = ""
    result: Any = None
    at: float = field(default_factory=time.time)


class ApprovalGate:
    def __init__(self, executor: Callable[[Action], Any], value_ceiling: float = 0.0,
                 notify: Optional[Callable[[int, Action], None]] = None):
        """
        executor:      performs the action for real.
        value_ceiling: actions with value above this always need approval.
        notify:        called when something is queued (e.g. post to Telegram/Slack).
        """
        self._execute = executor
        self._ceiling = value_ceiling
        self._notify = notify
        self._ids = itertools.count(1)
        self.pending: Dict[int, Action] = {}
        self.log: List[Decision] = []

    def needs_approval(self, action: Action) -> bool:
        return action.irreversible or action.value > self._ceiling

    def submit(self, action: Action) -> Decision:
        if not self.needs_approval(action):
            d = Decision(action, "executed", decided_by="auto",
                         result=self._execute(action))
            self.log.append(d)
            return d
        request_id = next(self._ids)
        self.pending[request_id] = action
        if self._notify:
            self._notify(request_id, action)
        d = Decision(action, "pending", note=f"request_id={request_id}")
        self.log.append(d)
        return d

    def approve(self, request_id: int, approver: str, note: str = "") -> Decision:
        action = self.pending.pop(request_id)   # KeyError if unknown: fail loud
        d = Decision(action, "executed", decided_by=approver, note=note,
                     result=self._execute(action))
        self.log.append(d)
        return d

    def reject(self, request_id: int, approver: str, note: str = "") -> Decision:
        action = self.pending.pop(request_id)
        d = Decision(action, "rejected", decided_by=approver, note=note)
        self.log.append(d)
        return d
