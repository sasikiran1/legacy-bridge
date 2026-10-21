# 0004. Circuit breaker parameters

- Status: Proposed. Decided in M3 from the recovery-timeline experiment.
- Date: 2026-10-21

## Context

The API tier keeps one breaker per endpoint (method + path template). When an endpoint fails, the agent should stop paying for doomed API calls and use the browser. When it recovers, the agent should return to the API, which is cheaper and more reliable. The parameters trade wasted calls during an outage against time spent on the browser after recovery.

## Starting point (to be tuned)

- **What counts as a failure:**
  - HTTP 404/410/501
  - 401/403 after one re-auth attempt
  - timeout
  - a response that fails Pydantic validation (schema drift)
  - 429 is **not** a failure; it goes to the provider-style rate limiter
- **Open after:** 3 consecutive failures, or ≥50% failures over the last 10 calls.
- **Open for:** 30 s at first, doubling up to 10 min while probes keep failing.
- **Half-open:** a single probe. For writes, the probe uses a read-only idempotent call on the same resource family, so a probe never performs a write.
- **Close:** after 1 successful probe.
- **State storage:** Postgres alongside the checkpointer, so breakers survive agent restarts and are shared across concurrent tasks.

## To decide with evidence

- the open interval vs. measured time to return to the API after a chaos-toggled recovery
- whether schema drift should open the breaker immediately (permanent breakage) rather than counting toward the threshold
