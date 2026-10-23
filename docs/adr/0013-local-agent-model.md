# 0013. Local agent model: gemma4 12B

- Status: Accepted
- Date: 2026-10-23

## Context

The local profile sends every agent node to one Ollama model. The M3 recordings used `qwen2.5-14b`, and most of its failures were model failures:
- tool calls written as text
- placeholder brackets dropped
- drift out of English

Three Apache-2.0 candidates that fit the 12 GB GPU were compared on the same 10-task development subset, under the healthy and API-degraded scenarios.

Conditions held equal:
- Each model ran as a `num_ctx 16384` variant on Ollama 0.40.9.
- Each was loaded on its own before its runs.
- The injection scanner was off.
- The code was the same commit.

Results ([bake-off summary](../../analysis/results/20261023T154359Z-bakeoff/bakeoff.json)):

| Model | Healthy | API degraded | False successes | Median task time (healthy / degraded) |
|---|---|---|---|---|
| `gemma4:12b` | 10 / 10 | 10 / 10 | 0 | 43 s / 81 s |
| `qwen3.5:9b` | 8 / 10 | 9 / 10 | 2 | 50 s / 91 s |
| `qwen3:14b` | 8 / 10 | 9 / 10 | 1 | 117 s / 234 s |
| `qwen2.5:14b` (baseline) | 8 / 10 | 6 / 10 | 0 | 43 s / 114 s |

No run made a wrong action. A false success is a task the agent reported as done that the database check failed. An earlier run of the same subset on Ollama 0.40.7, with the runtime not recorded, also gave gemma4 10 / 10 in both scenarios. The other models moved by one or two tasks between the two runs.

## Decision

`gemma4-12b-16k` (`gemma4:12b` with `num_ctx 16384`) is the `local_text` model and the default `LOCAL_TEXT_MODEL`. `make local-model` creates it.

## Alternatives

- **Keep `qwen2.5:14b`.** Six fewer tasks passed out of twenty. Rejected.
- **`qwen3:14b`.** It does not fit fully on the 12 GB GPU at this context length, so it runs partly on the CPU. Its median task took two to three times as long as gemma4's. Rejected.
- **`qwen3.5:9b`.** It is close on pass rate, but it is the only model with false successes in both scenarios, which is the failure the project most wants to avoid. Rejected.

## Consequences

- **The subset is small.** Ten tasks and two scenarios separate gemma4 from the baseline, but not the three challengers from each other. The full matrix in M5 re-measures the choice.
- **Recordings must be redone.** The committed cassettes were recorded with `qwen2.5:14b`, so they are re-recorded with gemma4.
- **gemma4 is a thinking model.** It returns a reasoning field next to its answer. The proxy forwards that field unchanged, and the agent does not use it.
