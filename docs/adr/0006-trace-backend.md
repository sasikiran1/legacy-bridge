# 0006. Trace backend and GenAI semantic-convention pin

- Status: Accepted
- Date: 2026-10-21

## Context

We need LLM-aware tracing: prompts (tokenized), tool calls, token counts, and per-step latency, all viewable locally at $0. Metrics go to `grafana/otel-lgtm` either way. Instrumentation should follow the OpenTelemetry GenAI semantic conventions, so the trace backend stays swappable.

## Criteria

1. Ingests OTLP and renders `gen_ai.*` spans as LLM calls.
2. Resource footprint on a laptop with ~75 GB free disk that already runs Postgres, Redis, Ollama, the portal, and LGTM.
3. Open license for self-hosting.
4. Evaluation and dataset features usable without an enterprise key.

## Decision

**Arize Phoenix 20.24** (`arizephoenix/phoenix:version-20.24.0`), a single container backed by the existing Postgres.

**Emit only OTel GenAI attributes,** not OpenInference.
- Phoenix ≥ 15.10.0 converts `gen_ai.*` to its OpenInference model on ingest.
- The same spans can therefore go to any OTLP backend, including Tempo in LGTM.

**Semconv pin:**
- `open-telemetry/semantic-conventions-genai` at commit `9ffc7e00c639be7712e544e3e54331e885dccf84` (2026-10-21). The repository has no tagged release yet.
- The attribute names we emit are listed in `proxy/telemetry.py` (M2) and checked by a test.
- When the repository tags a release, re-pin to the tag.

**Content capture:**
- Message content is attached only to spans emitted after masking.
- Pre-mask stages record counts and types, never text (threat model T5).

## Alternatives

- **Self-hosted Langfuse.** MIT core (enterprise features live under `ee/`). Ingests OTLP at `/api/public/otel` and maps `gen_ai.*`. But it needs Postgres, **ClickHouse**, Redis/Valkey, S3-compatible storage, and separate web and worker containers. That is too heavy for this host, for features we don't need.
- **Tempo only (inside LGTM).** Zero extra containers, but no LLM-specific views: message rendering, token and cost rollups.

## Consequences

- Phoenix is **Elastic License 2.0**, which is source-available, not OSI open source. Self-hosting for our own use is permitted. Offering it as a managed service would not be. Recorded so nobody is surprised.
- `gen_ai.*` attributes are still marked development and may be renamed, so the pin and the attribute-list test make renames visible.
