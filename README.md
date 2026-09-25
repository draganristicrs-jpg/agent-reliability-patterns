# agent-reliability-patterns

Small, dependency-free Python implementations of the reliability patterns we use
when AI agents run in production — not in a demo.

Maintained by **[ORBIRESEARCH](https://orbiresearch.com)**, a production-grade
agent engineering studio. Each pattern here has a longer write-up in our
[Lab](https://orbiresearch.com/lab) explaining the failure it prevents.

> Most AI agents fail in production. These are some of the reasons they don't have to.

---

## Patterns

| Pattern | What it prevents | Code | Write-up |
|---|---|---|---|
| Retry + backoff + circuit breaker | Rate-limit storms, retry loops that burn budget, hammering a dependency that is down | [`retry_backoff.py`](patterns/retry_backoff.py) | [Lab](https://orbiresearch.com/lab/llm-api-retry-backoff-circuit-breakers) · [Failure report](https://orbiresearch.com/lab/agent-crashed-on-429) |
| Approval gate | An agent sending, paying, deleting or publishing without a human decision | [`approval_gate.py`](patterns/approval_gate.py) | [Lab](https://orbiresearch.com/lab/approval-queue-pattern) · [Payments](https://orbiresearch.com/lab/agent-payment-authorization-pattern) |
| Run-bound, expiring credentials | Leaked or cloned agent credentials that stay valid for days | [`scoped_credentials.py`](patterns/scoped_credentials.py) | [Lab](https://orbiresearch.com/lab/agent-credential-expiry-pattern) |
| Agent-to-agent quality breaker | Confident, well-formed wrong answers (HTTP 200) propagating as fact through a multi-agent chain | [`quality_breaker.py`](patterns/quality_breaker.py) | [Lab](https://orbiresearch.com/lab/agent-to-agent-circuit-breaker) |

Every module is standard-library Python 3.9+, framework-agnostic, and small
enough to read in a few minutes and adapt to your stack.

## Quick start

```bash
git clone https://github.com/draganristicrs-jpg/agent-reliability-patterns.git
cd agent-reliability-patterns
python -m unittest -v
```

### Retry with backoff behind a circuit breaker

```python
from patterns.retry_backoff import CircuitBreaker, RetryableError, call_with_retry

breaker = CircuitBreaker(failure_threshold=5, reset_timeout=30)

def call_model():
    resp = client.post(...)                      # your LLM / API call
    if resp.status_code in (429, 500, 502, 503, 529):
        raise RetryableError(resp.text, status=resp.status_code,
                             retry_after=float(resp.headers.get("retry-after", 0)) or None)
    return resp.json()

result = call_with_retry(call_model, max_attempts=5, breaker=breaker)
```

### Approval gate for irreversible actions

```python
from patterns.approval_gate import Action, ApprovalGate

gate = ApprovalGate(executor=run_action, value_ceiling=100,
                    notify=lambda rid, a: post_to_slack(f"Approve #{rid}: {a.name} {a.params}"))

gate.submit(Action("tag_ticket", {"id": 42}))                       # runs immediately
gate.submit(Action("refund", {"order": 991}, value=340,
                   reason="eligible per return policy"))            # waits for a human
gate.approve(1, approver="ops-lead")                                # now it runs
```

## Design principles

- **Fail loud, not open.** Unknown request IDs, reused credentials and invalid
  agent output raise or get flagged; they never pass silently.
- **Limits live outside the model.** Ceilings, scopes and expiry are enforced
  in code the agent can't talk its way around, not in the prompt.
- **Everything leaves a record.** Decisions, credential use and breaker trips
  are logged so you can answer "what did the agent do, and why" later.

## Working with us

We design, build and operate production AI agents for companies — with
permission boundaries, evals, observability and human approval built in from
day one. If you're moving an agent from pilot to production:

**[Book a 30-minute discovery call →](https://orbiresearch.com/hire)** · hello@orbiresearch.com

## License

MIT — see [LICENSE](LICENSE).
