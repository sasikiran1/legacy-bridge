# 0007. Notional pricing

- Status: Accepted
- Date: 2026-10-21

## Context

Every hosted call runs on a free tier, so the real cost is $0 and says nothing about the design. The cost argument (API < replay < exploration < vision) needs a consistent dollar figure.

## Decision

**Price hosted calls at the provider's published paid list price** (standard tier, not batch), from `config/models.yaml`.
- Each price entry carries `effective_from`, an optional `effective_to`, the source URL, and the date it was read.
- A call is priced with the entry in effect on the **price date** of the run (default: the run's start date). The price date is written to the run manifest and printed under every cost chart.

**What gets counted:**
- input, cached-input (where a provider lists it), and output tokens, as reported by the provider
- thinking/reasoning tokens count as output where the provider bills them that way (Gemini 3.x)

**Models with no public price** (e.g. Groq "Contact Sales") can't be priced, so they aren't used.

**Local models** (Ollama) are priced at **$0 notional**. Their GPU-seconds and wall time are recorded separately and shown next to dollar cost, never converted into dollars.

**Replay** costs $0 LLM; its wall time is reported. Retries and 429-fallback calls are counted, because they're real spend at list price.

## Alternatives

- **Convert local GPU time to dollars** (cloud GPU hourly rate × seconds). Adds an assumption-heavy number the reader can't check. Rejected; showing the raw seconds instead is more honest.
- **One flat blended $/token.** Hides the tier economics this project is about. Rejected.

## Consequences

- Gemini 3.8/3.6 Flash list prices double on 2027-01-01. Results from runs either side of that date aren't comparable without re-pricing, so the report builder can re-price any run at a chosen price date from its token counts.
- Because local-first exploration (see `docs/milestones.md`) costs $0 notional, the hosted arm is what anchors the dollar curves. The report says so.
