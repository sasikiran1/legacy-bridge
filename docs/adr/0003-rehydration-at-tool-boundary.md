# 0003. Rehydration at the tool-execution boundary

- Status: Accepted
- Date: 2026-10-21

## Context

The model works in token space, but actions on the portal need real values: typing a member's new address, or filling a form. Where the swap from token to value happens decides who sees real values, and what an injected model can make the system reveal.

## Decision

Tokens are swapped for values in exactly three places.

1. **Tool-call arguments, in the executor.** This is the only place where the agent acts.
   - The API client and the Playwright runner receive tokenized arguments and resolve them immediately before the HTTP request or browser action.
   - The resolved values never flow back into the agent state or a prompt.
   - Tool *results* are re-tokenized before they return to the agent.
2. **Final operator-facing text, in the proxy.** Only when the caller sets `x-lb-rehydrate: final`.
   - By default the proxy returns tokens, because the agent loop is itself a client of the proxy and should stay in token space.
3. **SSE streams, in the proxy.** Same opt-in as (2).
   - A lookahead buffer holds output from a `⟦` until the matching `⟧`, or until 48 characters pass without one (then it flushes verbatim).
   - Tokens split across chunks are reassembled before resolution.
   - Tested with every split point of every token in a fixture stream.

**Rehydration policy:** the executor resolves a token only when all of these hold:
- the token belongs to the current task's vault
- the policy allows its `(entity type, tool, argument)` triple, e.g. an `ADDRESS` may go into `update_mailing_address.address` but not into `notes` or `search.query`
- the target host is on the domain allowlist
- the entity type is not marked non-rehydratable (credentials are always resolved from the vault by the executor itself, never from model output)

If any check fails, the action is refused, audited as `unmask_denied`, and the agent sees an error. Policies are Pydantic-validated YAML.

**Unresolved tokens:** a token-shaped string with no vault entry fails the tool call in (1). In (2) and (3) it is left as-is and audited as `unresolved`.

## Alternatives

- **Rehydrate model output in the proxy and let the agent hold real values.** Simplest, but the next prompt is then built from real values and relies entirely on re-masking. It also gives an injected model a direct path to reveal any value it can name. Rejected.
- **Let the model call a `reveal(token)` tool.** Makes the trust decision the model's. Rejected.
- **No policy, only task scoping.** Leaves the confused-deputy hole (threat model T3), e.g. writing a member's SSN into a free-text field that a later page renders to someone else.

## Consequences

- The executor is the most security-sensitive component, and it gets the most adversarial tests.
- Each tool needs a declared argument schema with entity-type permissions. The OpenAPI-generated tools in M3 must emit this.
- Final-answer rehydration is an explicit choice by the caller, so the reports and the demo show it happening.
