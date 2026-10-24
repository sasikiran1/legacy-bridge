# 0012. Masking boundaries for the agent

- Status: Accepted
- Date: 2026-10-22

## Context

M2 measured masking on static documents. Running the agent end to end against the portal (M3) showed that masking choices change whether the agent can work at all:

- **Prompt-only masking left raw data in the agent's own state.** Tool results reach the agent before they reach a prompt, so the LangGraph checkpoints in Postgres and the traces would hold raw PII.
- **NER on the model's own messages created false positives.** For example, `"element": "member name or number"`. The known-values pass then spread those tokens across every later page. In one recorded run, "Member Services Portal" became `[[PERSON:…]] Services Portal` throughout, and the model could no longer find the links it needed.
- **spaCy's NER tagged UI labels as people.** "Mailing Address" scored 0.85.
- **NER tagged column headers as the entities they describe.** "Date of birth" was tagged as a date of birth, "SSN" as an SSN.
- **NER folded city, state and ZIP into one street-address token**, so the agent could not fill the separate form fields. One update typed the whole address into the Street box.

## Decision

1. **Agent state holds tokens only.**
   - The executors send every API response and page snapshot through the proxy's `/v1/mask` before it enters LangGraph state.
   - The instruction is masked before the graph starts.
   - A per-task cache keeps the second, LLM-bound pass from re-running detection.
2. **Model-authored text gets pattern rules and known values, not NER.**
   - The model has only ever seen tokens, so raw PII in its output has to be reconstructed.
   - Precise detectors (SSN, email, phone, portal IDs) plus exact known-value matching catch that.
   - Within a request, user, system and tool messages are masked first, so their values are known before the assistant messages are masked.
3. **Only trusted values propagate.**
   - The known-values pass re-matches values that came from structured JSON fields, pattern rules, or multi-word NER spans.
   - A one-word NER guess is masked where it was found and nowhere else.
4. **Detector refinements in the production (`full`) configuration:**
   - spaCy NER is dropped when GLiNER is present.
   - PERSON needs a score of 0.75. GLiNER scores real names at 0.98 or above; the false positives seen scored 0.35–0.59.
   - Numeric entity types need at least two digits.
   - Street spans stop before "City, ST 12345", and spans that are only city, state and ZIP are dropped. City, state and ZIP are not tokenized by policy.

The canary leak gate stays at zero with all four changes (tests/leak).

## Alternatives

- **Mask only at the proxy, and let the agent hold raw tool results.** This puts raw PII in checkpoints and traces. Rejected.
- **Run NER on model output too.** This produced the cascades above. Rejected.
- **No masking of model output.** This loses defense in depth for reconstructed, patterned values. Rejected.

## Consequences

- A value the model reconstructs that is neither patterned nor already known (for example, a real-looking name it invents) is not masked on the way out. The model never saw real names, so such a value is not a leak of task data.
- The detector configuration changed after the M2 benchmark. The M2 numbers in the README describe the M2 configuration, and the benchmark is rerun with the M3 configuration in the M3 results.
- Masking quality is now partly a task-success concern, not only a privacy one. The agent's failure analysis should check for masking-induced failures.

## Amendment (2026-10-24)

Decision 3 is widened: a one-word NER span also propagates when it is a numeric entity type with at least six digits.

Under DOM drift, a canary bank account number reached a recorded prompt:
- NER tokenized it in the task instruction, beside "bank account number".
- It missed the same number on the review page, where the label had drifted to "Account no.".
- As a single-word NER span, the value had not been propagated, so the known-values pass could not re-catch it.

The cassette leak gate found it before the recording was committed. Long digit strings carry none of the risk that made single-word propagation unsafe: a mis-tagged word such as "Member" being masked on every later page.
