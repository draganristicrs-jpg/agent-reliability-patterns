import unittest

from patterns.approval_gate import Action, ApprovalGate
from patterns.quality_breaker import QualityBreaker, require_keys
from patterns.retry_backoff import (CircuitBreaker, CircuitOpenError, RetryableError,
                                    backoff_delay, call_with_retry)
from patterns.scoped_credentials import CredentialBroker, CredentialError


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class RetryTests(unittest.TestCase):
    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RetryableError("429", status=429)
            return "ok"

        self.assertEqual(call_with_retry(flaky, sleep=lambda s: None), "ok")
        self.assertEqual(calls["n"], 3)

    def test_non_retryable_propagates_immediately(self):
        calls = {"n": 0}

        def bug():
            calls["n"] += 1
            raise ValueError("bad input")

        with self.assertRaises(ValueError):
            call_with_retry(bug, sleep=lambda s: None)
        self.assertEqual(calls["n"], 1)

    def test_retry_after_is_honored(self):
        self.assertGreaterEqual(backoff_delay(1, retry_after=7.0, rng=lambda: 0.0), 7.0)

    def test_breaker_opens_and_recovers(self):
        clock = FakeClock()
        br = CircuitBreaker(failure_threshold=2, reset_timeout=10, clock=clock)

        def down():
            raise RetryableError("503", status=503)

        with self.assertRaises(RetryableError):
            call_with_retry(down, max_attempts=2, breaker=br, sleep=lambda s: None)
        self.assertEqual(br.state, "open")
        with self.assertRaises(CircuitOpenError):
            call_with_retry(lambda: "ok", breaker=br, sleep=lambda s: None)
        clock.t = 11
        self.assertEqual(br.state, "half-open")
        self.assertEqual(call_with_retry(lambda: "ok", breaker=br), "ok")
        self.assertEqual(br.state, "closed")


class ApprovalGateTests(unittest.TestCase):
    def setUp(self):
        self.done = []
        self.notified = []
        self.gate = ApprovalGate(lambda a: self.done.append(a.name) or "done",
                                 value_ceiling=100,
                                 notify=lambda i, a: self.notified.append(i))

    def test_small_reversible_runs(self):
        d = self.gate.submit(Action("tag_ticket", {}, value=0))
        self.assertEqual(d.status, "executed")
        self.assertEqual(self.done, ["tag_ticket"])

    def test_irreversible_waits_for_human(self):
        d = self.gate.submit(Action("send_email", {}, irreversible=True))
        self.assertEqual(d.status, "pending")
        self.assertEqual(self.done, [])
        rid = self.notified[0]
        d2 = self.gate.approve(rid, approver="dragan")
        self.assertEqual(d2.status, "executed")
        self.assertEqual(d2.decided_by, "dragan")

    def test_over_ceiling_can_be_rejected(self):
        self.gate.submit(Action("refund", {"order": 1}, value=2340))
        d = self.gate.reject(self.notified[0], approver="dragan", note="amount mismatch")
        self.assertEqual(d.status, "rejected")
        self.assertEqual(self.done, [])


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.broker = CredentialBroker(clock=self.clock)

    def test_valid_use(self):
        tok = self.broker.issue("run-1", {"crm:read"}, ttl_seconds=60)
        self.broker.check(tok, "run-1", "crm:read")

    def test_expired(self):
        tok = self.broker.issue("run-1", {"crm:read"}, ttl_seconds=60)
        self.clock.t = 61
        with self.assertRaises(CredentialError):
            self.broker.check(tok, "run-1", "crm:read")

    def test_other_run_is_flagged(self):
        tok = self.broker.issue("run-1", {"crm:read"}, ttl_seconds=60)
        with self.assertRaises(CredentialError):
            self.broker.check(tok, "run-2", "crm:read")
        self.assertEqual(self.broker.events[-1][0], "forgery_suspected")

    def test_revoke_on_completion(self):
        tok = self.broker.issue("run-1", {"crm:read"}, ttl_seconds=60)
        self.assertEqual(self.broker.revoke_run("run-1"), 1)
        with self.assertRaises(CredentialError):
            self.broker.check(tok, "run-1", "crm:read")


class QualityBreakerTests(unittest.TestCase):
    def test_bad_answer_trips_breaker(self):
        qb = QualityBreaker(call_agent=lambda r: {"answer": "x", "confidence": 0.2},
                            validate=require_keys("answer", min_confidence=0.7),
                            fallback=lambda r: "cached")
        for _ in range(3):
            res = qb.ask({})
            self.assertFalse(res.trusted)
        res = qb.ask({})
        self.assertEqual(res.source, "fallback")
        self.assertEqual(res.problem, "circuit open")

    def test_good_answer_is_trusted(self):
        qb = QualityBreaker(call_agent=lambda r: {"answer": "x", "confidence": 0.9},
                            validate=require_keys("answer", min_confidence=0.7),
                            fallback=lambda r: "cached")
        self.assertTrue(qb.ask({}).trusted)


if __name__ == "__main__":
    unittest.main()
