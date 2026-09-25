"""Short-lived, run-bound credentials for agents.

Pattern write-up: https://orbiresearch.com/lab/agent-credential-expiry-pattern

Scope answers "what can this agent do". This module adds the two things scope
doesn't: "for how long" and "is this the same run that received it".

Rules this module enforces:
- Credentials are issued per run, never per agent.
- Every credential expires (TTL shorter than your longest normal task).
- Every credential is bound to a run_id; use from another run is refused
  and flagged as a possible forgery.
- Revocation happens when the run ends, not on a cleanup schedule.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Tuple


class CredentialError(Exception):
    pass


@dataclass(frozen=True)
class _Record:
    run_id: str
    scopes: FrozenSet[str]
    expires_at: float


class CredentialBroker:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._records: Dict[str, _Record] = {}   # keyed by token hash, never the token
        self.events: List[Tuple[str, str, str]] = []  # (event, run_id, detail)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self, run_id: str, scopes: set, ttl_seconds: float) -> str:
        token = secrets.token_urlsafe(32)
        self._records[self._hash(token)] = _Record(
            run_id, frozenset(scopes), self._clock() + ttl_seconds)
        self.events.append(("issued", run_id, ",".join(sorted(scopes))))
        return token

    def check(self, token: str, run_id: str, scope: str) -> None:
        """Raise CredentialError unless the token is valid for this run and scope."""
        rec = self._records.get(self._hash(token))
        if rec is None:
            self.events.append(("denied_unknown", run_id, scope))
            raise CredentialError("unknown or revoked credential")
        if self._clock() >= rec.expires_at:
            self.events.append(("denied_expired", run_id, scope))
            raise CredentialError("credential expired")
        if not hmac.compare_digest(rec.run_id, run_id):
            # Same credential presented by a different run: fail loud.
            self.events.append(("forgery_suspected", run_id, f"issued_to={rec.run_id}"))
            raise CredentialError("credential used outside the run it was issued to")
        if scope not in rec.scopes:
            self.events.append(("denied_scope", run_id, scope))
            raise CredentialError(f"scope '{scope}' not granted")
        self.events.append(("used", run_id, scope))

    def revoke_run(self, run_id: str) -> int:
        """Revoke every credential issued to a run. Call this when the run ends."""
        doomed = [h for h, r in self._records.items() if r.run_id == run_id]
        for h in doomed:
            del self._records[h]
        self.events.append(("revoked", run_id, str(len(doomed))))
        return len(doomed)
