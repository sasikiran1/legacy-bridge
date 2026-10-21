# 0001. Token format and determinism scope

- Status: Accepted. The short-ID length is provisional until the M2 token-overhead measurement.
- Date: 2026-10-21

## Context

The proxy replaces sensitive values with tokens before any text reaches a model. The tokens must:

- let the model reason about references ("the same person as before"), so one value maps to one token within a task;
- be unlinkable across tasks, so a provider can't join records;
- resist confirmation attacks: a provider holding a token must not be able to test guesses of low-entropy values (phone numbers, DOBs) against it;
- be reproducible between cassette recording and CI replay, because cassettes are keyed on the tokenized request;
- cost few LLM tokens, because they appear in every step's prompt.

## Decision

**Syntax:** `⟦TYPE:hhhhhhhhhh⟧`, for example `⟦PERSON:3f9a1c2e7b⟧`.
- `TYPE` is an uppercase entity type from the PII policy (`config/pii-policy.yaml`, M2).
- `hhhhhhhhhh` is the first 10 hex characters (40 bits) of the canonical ID.
- The brackets are U+27E6/U+27E7, which are rare in portal text.

**Canonical ID:** a 128-bit UUIDv8 (RFC 9562 custom layout), stored in the vault and the audit log.

```
k_task = HMAC-SHA256(TOKEN_KEY, "lb-token-v1" || RUN_SEED || task_id)
raw    = HMAC-SHA256(k_task, TYPE || 0x1F || value)[0:16]
id     = raw with UUIDv8 version and variant bits set
```

**Determinism scope:** the same `(TOKEN_KEY, RUN_SEED, task_id, TYPE, value)` gives the same token.
- Different tasks give different tokens for the same value.
- Same seed and task gives the same token across runs, which cassettes rely on.

**Exact-value identity:** no normalization. `John Smith` and `JOHN SMITH` get different tokens. This keeps mask→unmask round-trips byte-exact, which is a tested property.

**Collisions:** a short-ID collision within a task fails the task loudly at mint time. It is never silently suffixed, because order-dependent suffixes would break cross-run determinism. At 40 bits and 500 entities per task, the collision probability is about 1e-7.

**Strict parsing:** anything that looks like a token but doesn't resolve in the current task's vault is treated as unresolved. It is never fuzzy-matched. See ADR 0003 for how each rehydration site handles that.

## Alternatives

- **Random UUIDv4 per value, stored in the vault.** Unlinkable and simple, but not reproducible across runs, so cassette replay breaks.
- **Unkeyed hash of the value.** Deterministic, but a provider can confirm guesses offline. Rejected.
- **Full 36-character UUID in prompts.** No collision handling needed, but roughly 3× the LLM tokens per entity. To be quantified in M2; if the overhead is negligible we switch, since it's simpler.
- **Format-preserving fakes** (a fake name for a real name). More natural for the model, but fakes can collide with real values in page text, and rehydration becomes ambiguous. Rejected.

## Consequences

- `TOKEN_KEY` is a new secret in `.env`, separate from the vault key.
- **For cassette recording and CI, `TOKEN_KEY` is a fixed, non-secret value.** The data is synthetic, so linkage is harmless. A real deployment would keep it secret and accept that cassettes don't replay across key rotations (see threat model, residual risk).
- The type is visible to the provider by design (threat model T4).
- Case and spacing variants of one value are not co-referenced. If M2 shows that hurts task success, revisit with normalization plus a stored surface form per occurrence.
