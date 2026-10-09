from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from threading import RLock
from time import time_ns
from typing import Any

from agents.tracing import Span, Trace, TracingProcessor
from opentelemetry import trace as otel_trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import Status, StatusCode

_SAMPLE_RATIO_ERROR = "OTEL_TRACE_SAMPLE_RATIO must be a number between 0.0 and 1.0"


def _validate_sample_ratio(sample_ratio: float) -> float:
    if not isfinite(sample_ratio) or not 0.0 <= sample_ratio <= 1.0:
        raise ValueError(_SAMPLE_RATIO_ERROR)
    return sample_ratio


def parse_sample_ratio(value: str) -> float:
    try:
        sample_ratio = float(value)
    except ValueError as error:
        raise ValueError(_SAMPLE_RATIO_ERROR) from error
    return _validate_sample_ratio(sample_ratio)


def _timestamp_ns(value: str | None) -> int | None:
    """Convert SDK timestamps with integer arithmetic to preserve microsecond precision."""
    if value is None:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    delta = timestamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000_000
        + delta.microseconds * 1_000
    )


class OpenTelemetryTracingProcessor(TracingProcessor):
    """Export Agents SDK spans through OTLP gRPC without prompt or tool payloads.

    `endpoint` must be an `http://` or `https://` gRPC URL. `sample_ratio` sets
    parent-based head sampling between 0.0 and 1.0. HTTP URLs use plaintext;
    HTTPS URLs use TLS.
    """

    def __init__(
        self,
        endpoint: str,
        service_name: str,
        sample_ratio: float = 1.0,
        span_exporter: SpanExporter | None = None,
    ) -> None:
        """Create an OTLP exporter with the selected resource and sampling settings."""
        if not endpoint.lower().startswith(("http://", "https://")):
            raise ValueError("endpoint must be an http:// or https:// URL")
        sample_ratio = _validate_sample_ratio(sample_ratio)

        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
            sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                span_exporter
                or OTLPSpanExporter(
                    endpoint=endpoint,
                    insecure=endpoint.lower().startswith("http://"),
                )
            )
        )
        self._provider = provider
        self._tracer = provider.get_tracer("openai-agents")
        self._traces: dict[str, OtelSpan] = {}
        self._spans: dict[str, dict[str, tuple[OtelSpan, int | None]]] = {}
        self._lock = RLock()

    def on_trace_start(self, sdk_trace: Trace) -> None:
        """Start the OTel workflow span."""
        with self._lock:
            self._traces[sdk_trace.trace_id] = self._tracer.start_span(
                sdk_trace.name,
                attributes={"openai_agents.trace_id": sdk_trace.trace_id},
                start_time=_timestamp_ns(sdk_trace.started_at),
            )

    def on_trace_end(self, sdk_trace: Trace) -> None:
        """End the OTel workflow span and any SDK spans that remained open."""
        with self._lock:
            self._end_trace(sdk_trace.trace_id, _timestamp_ns(sdk_trace.ended_at))

    def on_span_start(self, span: Span[Any]) -> None:
        """Start an OTel span with the corresponding SDK parent and safe attributes."""
        with self._lock:
            trace_span = self._traces.get(span.trace_id)
            if trace_span is None:
                return

            parent_entry = self._spans.get(span.trace_id, {}).get(span.parent_id or "")
            # Spans without an active parent are rooted in the workflow span.
            if parent_entry is None:
                parent = trace_span
            else:
                parent = parent_entry[0]
            context = otel_trace.set_span_in_context(parent, Context())
            span_data = span.span_data
            start_time = _timestamp_ns(span.started_at)
            attributes: dict[str, Any] = {
                "openai_agents.trace_id": span.trace_id,
                "openai_agents.span_id": span.span_id,
                "openai_agents.span_type": span_data.type,
            }
            name = getattr(span_data, "name", None)
            model = getattr(span_data, "model", None)
            if isinstance(name, str):
                attributes["openai_agents.name"] = name
            if isinstance(model, str):
                attributes["gen_ai.request.model"] = model

            span_name = f"{span_data.type}: {name}" if name else span_data.type
            otel_span = self._tracer.start_span(
                span_name,
                context=context,
                attributes=attributes,
                start_time=start_time,
            )
            self._spans.setdefault(span.trace_id, {})[span.span_id] = (otel_span, start_time)

    def on_span_end(self, span: Span[Any]) -> None:
        """End an OTel span without exporting SDK error details."""
        with self._lock:
            trace_spans = self._spans.get(span.trace_id)
            span_entry = trace_spans.pop(span.span_id, None) if trace_spans is not None else None
            if trace_spans is not None and not trace_spans:
                self._spans.pop(span.trace_id, None)
            if span_entry is not None:
                otel_span, _ = span_entry
                if span.error is not None:
                    otel_span.set_status(Status(StatusCode.ERROR, "Agent operation failed"))
                otel_span.end(end_time=_timestamp_ns(span.ended_at))

    def force_flush(self) -> None:
        """Flush spans buffered by the OTel provider."""
        self._provider.force_flush()

    def shutdown(self) -> None:
        """Close incomplete spans and shut down the OTel provider."""
        with self._lock:
            for trace_id in list(self._traces):
                self._end_trace(trace_id)
        self._provider.shutdown()

    def _end_trace(self, trace_id: str, end_time: int | None = None) -> None:
        spans = self._spans.pop(trace_id, {})
        end_time = max(
            end_time if end_time is not None else time_ns(),
            max(
                (start for _, start in spans.values() if start is not None),
                default=0,
            ),
        )
        # Agent SDK nesting starts parents before children, so reverse insertion ends children first.
        for span, _ in reversed(list(spans.values())):
            span.set_status(Status(StatusCode.ERROR, "Agent operation did not complete"))
            span.end(end_time=end_time)

        trace_span = self._traces.pop(trace_id, None)
        if trace_span is not None:
            trace_span.end(end_time=end_time)
