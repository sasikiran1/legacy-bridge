# API first, browser second, replay third: an agent for a legacy portal

Legacy systems rarely fail cleanly. legacy-bridge's portal has a documented REST API that is partly unfinished even on a good day: one endpoint returns 501, one is retired with 410, and responses drift from their schema. Its web UI changes labels, moves buttons, inserts modals, and on one page paints its form on a canvas. This write-up covers how the agent copes, and how successful runs become replay scripts that need no model at all.

## The tiers

The agent is a LangGraph state machine with a Postgres checkpointer:

```
plan -> API tier -> (replay script) -> browser tier -> verify
                  \___________________________________/ handoff to a person
```

**API tier.**
- Tools are generated from the portal's OpenAPI document.
- Every response is classified as one of: success, `not_found`, `gone`, `not_implemented`, `server_error`, `schema_drift`, `auth`, `circuit_open`, or a client error.
- The first seven mean "the API cannot do this job right now" and hand the task on. A client error is the model's to fix.
- Each endpoint has a circuit breaker: open for 10 s after repeated failures, doubling while probes fail, capped at 60 s (ADR 0004).

**Replay tier.** If a compiled script exists for the task's intent and parameter set, it runs with plain Playwright and no model.

**Browser tier.** `@playwright/mcp`, driven by accessibility snapshots.
- Refs are assigned only after the page settles (`--snapshot-mode none`). Early recordings numbered refs before data loaded, so a replay could click a different element.
- Page text is masked before it enters agent state, and wrapped as untrusted content.

**Verify.** A change task that executed no write, or a question finished without an answer, is never reported as a success. Separately, every task is graded against a database diff: any change outside the task's expectation is a wrong action.

## Guardrails that did the work

- **Approvals.** Payments and deletes stop for approval, through the API or the browser alike. After a page is flagged as a possible prompt injection, every write stops for approval.
- **Walls.** CAPTCHA and MFA walls are detected and handed to a person, never solved.
- **Domain allowlist,** enforced in the executor.
- **Values restored only at the tool boundary,** under the per-field policy (see the proxy write-up).

Llama Prompt Guard 2 (86M) also scans pages. It flagged 0 of 8 held-out planted injections, and 0 of 24 when they were embedded in pages, so it is kept as defense in depth and not counted on (ADR 0014). With gemma4, no planted injection produced a wrong action in the injected-notes scenario. The approvals and post-conditions are the measured defense.

## Results with the local model

gemma4 12B was chosen by a bake-off of four Apache-2.0 models on 10 tasks and two scenarios: 20/20 passed, against 17, 17 and 14 (ADR 0013). On the 10-task development subset:

| Scenario | Passed | Wrong actions |
|---|---|---|
| Healthy (API partly unfinished) | 10 / 10 | 0 |
| API degraded | 10 / 10 | 0 |
| DOM drift | 10 / 10 | 0 |
| Injected notes | 10 / 10 | 0 |
| CAPTCHA at sign-in | 5 / 10, the other 5 handed to a person | 0 |

Every model exchange is recorded as a cassette, with the injection scanner's verdicts. CI replays them against the real portal with no model and no scanner, and fails if any task's outcome changes.

## Compiling browser runs into scripts

A verified browser run is compiled offline from its cassettes into YAML (ADR 0005).

**How it compiles:**
- Each action was chosen from the snapshot in the same request, so its element ref resolves to a role and accessible name there.
- Each typed token maps back to a task parameter by re-minting that parameter's token with the same HMAC.
- A value that maps to no parameter is a compile error, so a script can never carry data from the run it came from.

**Guards added after real failures:**
- **A short lone token could "fit" any parameter.** Partial fits now need enough literal text around them.
- **A repeated element with no identifying row is an error.** Clicking the first "Delete" on a list of notes is how a wrong action happens.
- **Every parameter must be used.** The address run that never set `state` would have produced a script that skips the state field. The compiler now refuses it, so that intent has no script rather than a wrong one.

**Results:**

| Measurement | Result |
|---|---|
| Six scripts on the healthy portal | 27 / 27 passed, 0 model calls, median 1.8 s |
| The same scripts under DOM drift | 0 / 27, every run stopped at the broken step, 0 wrong actions |
| Self-heal under DOM drift | 12 / 12 |

Under DOM drift a script fails at a step it cannot find, never by clicking the wrong thing. When that happens, the browser tier finishes the task, given the script's steps as templates without values. Its verified run compiles into version 2. In the self-heal run, each broken script healed once (8 and 9 model calls). Later tasks replayed version 2 with 2 to 3 calls.

## When the accessibility tree is missing

On the `poor_a11y` scenario, the reimbursement form has six unnamed text boxes, and its captions and buttons are painted on canvases. An accessibility-snapshot agent sees `- textbox` six times and no labels.

The executor repairs this (ADR 0008):
- It finds unnamed elements in the DOM and reads each canvas by OCR of its own crop.
- It lists them for the model with their screen positions.
- The model gets `click_at` and `type_at` only while such a list exists. Both act only on a listed element, never an arbitrary point.
- An input's label is the caption beside it on screen, and the data policy matches on that label. A coordinate click on a canvas "Confirm submission" goes through the same payment approval as a ref click.

The first attempt failed because OCR clipped a letter ("Slaim number"), and the policy refused the claim number for that field. The model then stalled and the task was handed off. With the OCR fixed, `submit_reimbursement-01` passed with 13 model calls. The other six tasks on that form were not run, so this is one success, not a rate.

## What I would do differently

- **Save results per task from day one.** The runner originally wrote results at the end of a run, so a pause discarded hours of recordings on two machines. It now saves after every task.
- **Give the portal a health check.** Compose started the runner before Spring Boot was ready, and CI failed once on the race. The runner now waits for readiness.
- **Self-verification trusts the page.** In the bake-off, weaker models reported three tasks done that the database showed were not. The database diff caught them, but the agent's own verify step would not have.
