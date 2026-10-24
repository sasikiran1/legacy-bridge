# 0005. Replay script format and locator strategy

- Status: Accepted (2026-10-24). Proposed 2026-10-21, decided in M4 from the replay evaluation and the self-heal run.
- Date: 2026-10-21

## Context

A successful browser run should compile into a script that later runs the same intent with no model calls. The script must be:

- parameterized, so it works for other members and values
- robust to cosmetic DOM drift, or able to recover from it
- verifiable
- reviewable in a diff

It must never carry a value from the run it was compiled from.

## Decision

- **Format:** an interpreted, versioned YAML script, `agent/replays/<intent>--<parameters>.yaml`, validated by Pydantic. There is one script per intent and parameter set, because changing an email and changing a phone are different flows. Each step holds:
  - an action
  - a locator: role and accessible name, plus `within_row`, the parameter whose value identifies the table row
  - a value template such as `${email}`
- **Compiled offline from cassettes:**
  - Each action was chosen from the page snapshot in the same request, so its element ref resolves to a role and name there.
  - Each typed token maps back to a task parameter by minting that parameter's token with the same keyed HMAC.
  - A partly masked text, such as a note containing an email, maps to the parameter it fits, but only when enough literal text constrains the fit.
- **Compile errors:**
  - a value that maps to no parameter, so no source-run value can be committed
  - an element with no accessible name
  - a repeated element that no parameter's row identifies
  - **a parameter the source run never wrote** (the cached-wrong-action guard)
- **Runner:** Playwright with no model.
  - Locators are tried in order: role and name, then label, then visible text, inside the anchored row.
  - The payment and delete approval gates still apply.
  - A verification wall hands off to a person.
  - Success requires the portal's confirmation message. The runner waits for it, because it renders after the write returns.
- **Place in the agent:** after an API fallback and before the browser tier. Scripts are opt-in (`--scripts`). The script's real parameter values live in the runtime, never in checkpointed state (ADR 0012).
- **Self-heal:**
  - A broken step falls through to the browser tier, with the script's steps given to the model as templates.
  - With `--compile`, a verified browser run becomes the next script version.
  - The YAML diff records what changed.

## Evidence

[Replay evaluation](../../analysis/results/20261024T171309Z-replay-eval/replay_eval.json): scripts compiled from the API-degraded recordings, run on every task of the same intent and parameter set, graded by the database diff.

| | Healthy portal | DOM drift |
|---|---|---|
| Runs | 27 | 27 |
| Passed | 27 | 0 (every run stopped at its first renamed element) |
| Wrong actions / false successes | 0 / 0 | 0 / 0 |
| Model calls | 0 | 0 |
| Median time per task | 1.8 s | 1.8 s |

Six scripts compiled. update_address was refused, because its source run never wrote the state. Before that guard, the update_address script passed its own check on 7 tasks while leaving the old state in place.

[Self-heal under DOM drift](../../analysis/results/20261024T172131Z-agent-run/agent_runs.jsonl), with scripts and compiling on:

| Task | Path | Model calls | Time |
|---|---|---|---|
| delete_note-01 | API → script broke → browser, compiled v2 | 8 | 494 s |
| delete_note-02 to -04 | API → v2 script | 3 each | 84–104 s |
| update_contact-01 | API → script broke → browser, compiled v2 | 9 | 826 s |
| update_contact-03, -04 | API → v2 script | 2, 3 | 70–79 s |

All 7 passed with 0 wrong actions. The remaining model calls are the plan and the API attempt that come before the replay tier. The v2 diffs show the drift: renamed buttons and fields, and a "Got it" interstitial.

## Alternatives

- **Generated Playwright Python.** Easier to step through in a debugger, but harder to diff and to check for literal values. Rejected.
- **Selectors (CSS or test IDs) in the locator chain.** They survive renamed labels, but DOM drift also renames IDs, and selectors would bypass the label-based data policy. Rejected.
- **Scripts on by default.** Recordings made without scripts would then replay differently in CI. Rejected; the cassette index records whether a recording used scripts.

## Consequences

- **Scripts track the portal's last-seen layout.** A v2 healed under DOM drift would break on the healthy layout, for example at its "Got it" step, and heal again.
- **Writes only.** Questions (claim status, policy lookup) are not compiled: a script cannot yet extract an answer.
- **The replay tier still pays for the plan and the API attempt** (2–3 model calls). Skipping the API tier when its breaker is open is a further saving, not yet built.
- **Compiling needs the cassettes of the run,** so self-heal requires recording mode.
