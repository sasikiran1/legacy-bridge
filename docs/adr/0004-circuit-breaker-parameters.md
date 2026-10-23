# 0004. Circuit breaker parameters

- Status: Accepted (2026-10-23). Proposed 2026-10-21 with starting values, decided in M3 by the recovery experiment.
- Date: 2026-10-21

## Context

The API tier keeps one breaker per endpoint (method + path template). When an endpoint fails, the agent should stop paying for doomed API calls and use the browser. When it recovers, the agent should return to the API, which is cheaper and more reliable. The parameters trade wasted calls during an outage against time spent on the browser after recovery.

The proposal started from:
- open after 3 consecutive failures or ≥50% of the last 10 calls
- open for 30 s, doubling up to 10 min
- one probe when half-open
- state in Postgres

[The experiment](../../analysis/results/20261023T161000Z-breaker-recovery/breaker_recovery.json) (`analysis/breaker_recovery.py`) sent a `getMember` call every 10 simulated seconds while the portal's chaos endpoint broke that operation:
- **Portal runs:** 500 and 404 outages of 2, 10 and 20 minutes. All 54 call-by-call timelines matched a simulation using the same breaker code.
- **Simulation:** every outage length from 1 to 30 minutes in 10-second steps (175 lengths). This was needed because one outage's result depends on where it ends relative to the next probe. In the portal runs, all three lengths happened to end 50 s before a probe.

| Initial / cap | Mean time back on the API | Worst | Browser tasks after recovery (mean) | Failed API calls, mean (worst) |
|---|---|---|---|---|
| no breaker | 0 s | 0 s | 0 | 93 (180) |
| 10 s / 600 s | 239 s | 590 s | 23.9 | 8.7 (10) |
| 30 s / 600 s (proposed) | 253 s | 590 s | 25.3 | 7.3 (9) |
| 120 s / 600 s | 266 s | 590 s | 26.6 | 5.5 (7) |
| 10 s / 120 s | 54 s | 110 s | 5.4 | 12.8 (20) |
| **10 s / 60 s** | **25 s** | **50 s** | **2.5** | **19.4 (34)** |
| 30 s / 60 s | 25 s | 50 s | 2.5 | 18.1 (33) |

The cap decides recovery. The initial interval barely matters. 500 and 404 outages produced identical timelines.

## Decision

- **Open interval:** 10 s at first, doubling to a **60 s cap** while probes fail.
- **Open after:** 3 consecutive failures or ≥50% of the last 10 calls, unchanged.
- **Close:** after one successful probe, unchanged.
- **Failures:**
  - 404 with a bare "Not Found" (a removed route), 410, 501 and 5xx
  - 401/403
  - a 2xx that fails schema validation (drift)
  - a missing record, i.e. a 404 that names the record, is the model's error and does not count
  - 429 does not count
- **Probe:** the real call. A failed call to this API never mutates state, so no separate read-only probe is needed yet.
- **State:** in memory, per agent process.

The 60 s cap costs about ten more failed probes per outage than a 10-minute cap and avoids about 21 browser fallbacks after recovery. In the bake-off, a browser task took about twice as long as an API task: gemma4 medians of 81 s and 43 s ([bake-off](../../analysis/results/20261023T154359Z-bakeoff/bakeoff.json)). A failed probe wastes one model step.

## Alternatives

- **The proposed 30 s / 10 min.** It stays on the browser for over 4 minutes on average after the API recovers. Rejected.
- **A 120 s cap.** Fewer failed probes (12.8 against 19.4), but recovery takes about twice as long. A reasonable choice where probes are expensive, which they aren't here.
- **No breaker.** It sends every call into the outage. Rejected.

## Consequences

- **Load on a failing endpoint:** at most one probe a minute per process.
- **State is per process.** Breakers reset when the agent restarts and aren't shared between concurrent processes. Postgres storage, as first proposed, waits until there's more than one agent process.
- **Not decided here:** whether schema drift should open the breaker immediately, as permanent breakage. Drift counts towards the threshold like any other failure.
- **The workload is one task every 10 s.** With sparser traffic, fewer failed probes land during an outage, and the cap matters less.
