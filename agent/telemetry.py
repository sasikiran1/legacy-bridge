"""OpenTelemetry tracing with GenAI semantic-convention attributes (ADR 0006).

Spans go to OTEL_EXPORTER_OTLP_ENDPOINT (Phoenix in compose) when it is set; otherwise tracing is a no-op.
Message content is never attached: agent state is already tokenized, but traces stay counts-and-ids only.
"""

from __future__ import annotations

import os
from functools import cache

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

SEMCONV_COMMIT = "9ffc7e00c639be7712e544e3e54331e885dccf84"
"""open-telemetry/semantic-conventions-genai, pinned in ADR 0006."""


@cache
def tracer() -> Tracer:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider = TracerProvider(resource=Resource.create({"service.name": "legacy-bridge-agent"}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
        )
        trace.set_tracer_provider(provider)
    return trace.get_tracer("legacy-bridge.agent")


def flush() -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.force_flush()
