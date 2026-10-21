# 0005. Replay script format and locator strategy

- Status: Proposed. Decided in M4.
- Date: 2026-10-21

## Context

A successful exploration run gets compiled into a script that later runs the same intent with zero LLM calls. The script must be:

- parameterized, so it works for other members and values
- robust to cosmetic DOM drift
- verifiable through recorded post-conditions
- reviewable in a diff

## Starting point

- **Format:** a versioned, Pydantic-validated YAML file per intent, under `agent/replays/<intent>.yaml`. It holds:
  - parameters with entity types
  - steps: action, locator chain, and a value template such as `${member.address}`
  - expected page checkpoints
  - post-conditions
  - provenance: source run ID, portal DOM fingerprint, compile date
- **Values:** always parameters, never literals. A step that types a literal from the source run is a compile error, which keeps PII out of committed scripts.
- **Locator chain:** each step stores several candidates, tried in order:
  1. role + accessible name
  2. label
  3. test ID
  4. text
  5. CSS (last resort, flagged)
- **Self-heal:** when a step fails, the exploration tier is seeded with the failing step, its locator chain, and the current snapshot. A healed run produces a new script version, and the diff is kept.

## To decide with evidence

- YAML interpreted by a runner vs. generated Playwright Python. Interpreted is easier to diff and verify; generated is easier to debug.
- How often the role/label-first chain survives each DOM-drift toggle.
- Guarding against **cached wrong actions:** a script that passes its post-conditions but did the wrong thing. Candidate guard: the post-conditions must cover every field the intent writes.
