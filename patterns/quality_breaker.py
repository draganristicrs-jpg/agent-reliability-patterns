"""Agent-to-agent circuit breaker that trips on bad answers, not just errors.

Pattern write-up: https://orbiresearch.com/lab/agent-to-agent-circuit-breaker

When you call another agent, the dangerous failure isn't a 500. It's a
well-formed, confident, wrong answer that returns HTTP 200. A normal breaker
watches status codes and never sees it. This one also counts quality failures.

Rules this module enforces:
- Validate the response shape before anything downstream uses it.
- A failed validation counts as a failure for the breaker, same as an error.
- Low-confidence or invalid results are passed on as flagged, never as fact.
- When the breaker is open, use a fallback that doesn't depend on that agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .retry_backoff import CircuitBreaker, CircuitOpenError


@dataclass
class AgentResult:
    value: Any
    trusted: bool          # False = downstream must treat it as uncertain
    source: str            # "agent" | "fallback"
    problem: str = ""


Validator = Callable[[Dict[str, Any]], Optional[str]]  # returns problem text or None


def require_keys(*keys: str, min_confidence: Optional[float] = None) -> Validator:
    """Simple shape validator: required keys present, optional confidence floor."""
    def validate(resp: Dict[str, Any]) -> Optional[str]:
        if not isinstance(resp, dict):
            return "response is not an object"
        missing = [k for k in keys if k not in resp]
        if missing:
            return f"missing keys: {', '.join(missing)}"
        if min_confidence is not None:
            conf = resp.get("confidence")
            if not isinstance(conf, (int, float)) or conf < min_confidence:
                return f"confidence below {min_confidence}"
        return None
    return validate


class QualityBreaker:
    def __init__(self, call_agent: Callable[[Dict[str, Any]], Dict[str, Any]],
                 validate: Validator,
                 fallback: Callable[[Dict[str, Any]], Any],
                 breaker: Optional[CircuitBreaker] = None):
        self._call = call_agent
        self._validate = validate
        self._fallback = fallback
        self.breaker = breaker or CircuitBreaker(failure_threshold=3, reset_timeout=60)

    def ask(self, request: Dict[str, Any]) -> AgentResult:
        try:
            self.breaker.before_call()
        except CircuitOpenError:
            return AgentResult(self._fallback(request), trusted=False,
                               source="fallback", problem="circuit open")
        try:
            resp = self._call(request)
        except Exception as e:  # transport or agent error
            self.breaker.record_failure()
            return AgentResult(self._fallback(request), trusted=False,
                               source="fallback", problem=f"error: {e}")
        problem = self._validate(resp)
        if problem:
            # Quality failure: counts exactly like an error.
            self.breaker.record_failure()
            return AgentResult(resp, trusted=False, source="agent", problem=problem)
        self.breaker.record_success()
        return AgentResult(resp, trusted=True, source="agent")
