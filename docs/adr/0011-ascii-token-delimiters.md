# 0011. ASCII token delimiters

- Status: Accepted. Supersedes the rendering in ADR 0001; derivation and the 10-hex short ID are unchanged.
- Date: 2026-10-22

## Context

ADR 0001 rendered tokens as `⟦TYPE:hhhhhhhhhh⟧`. The mathematical white square brackets (U+27E6/U+27E7) were chosen because they are rare in page text, without measuring what they cost. Every PII value in every prompt becomes one of these tokens, so their LLM-token cost multiplies across every step of every task.

Measured with the `openai/gpt-oss-20b` tokenizer (the Groq models we route to) on the synthetic corpus, using gold spans so detection quality doesn't affect the comparison ([result](../../analysis/results/20261022T023527Z-token-overhead/token_overhead.json)):

| Rendering | LLM tokens per PII token | Prompt size vs raw text |
|---|---|---|
| `⟦TYPE:uuid⟧` | 32.0 | +167% |
| `⟦TYPE:hex10⟧` (ADR 0001) | 15.3 | +62% |
| `[[TYPE:hex10]]` (this ADR) | 11.3 | +35% |
| `[[TYPE:hex8]]` | 10.1 | +27% |

Each `⟦` or `⟧` encodes as three byte-level tokens, so the delimiters alone account for 6 of the 15 tokens.

## Decision

Render tokens as `[[TYPE:hhhhhhhhhh]]`: ASCII double brackets, the same type names, the same 10-hex short ID.

## Alternatives

- **Keep `⟦…⟧`.** Rarer in page text, but costs about 4 more LLM tokens per PII value.
- **8-hex IDs (`[[TYPE:hhhhhhhh]]`).** Saves roughly one more token per value, but drops from 40 to 32 bits per task. Collisions fail the task loudly (ADR 0001), so this stays an option if prompt cost dominates in M4.
- **Shorter type codes** (`P` for `PERSON`). Most type names are already a single token; the model also uses the type to reason, so readability wins.

## Consequences

- `[[` occurs in ordinary text: nested JSON arrays, wiki links. The parser stays strict (an uppercase type, a colon, exactly 10 lowercase hex characters), so `[[1, 2]]` is never treated as a token.
- **The streaming rehydrator** now also holds a trailing single `[`, which may be half of an opener. When held text turns out not to be a token, it releases one character at a time, so a real token that starts right after a bracket is still caught. Tests cover JSON arrays and every split point.
- **A forged `[[TYPE:…]]` string planted in page content** can't be resolved: it isn't in the task's vault, and minting a matching ID requires the HMAC key (threat model T3/T4).
- **Models must copy tokens verbatim.** A local 14B model did so in a streamed smoke test. M3 tracks the unresolved-token rate from cassettes as the ongoing check.
