from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from threading import RLock
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


def _timestamp_ns(value: str | None) -> int | None:
    """Convert SDK timestamps with integer arithmetic to preserve microsecond precision."""
    if value is None:
        return None
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    delta = timestamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000_000
        + delta.microseconds * 1_000
    )


class OpenTelemetryTracingProcessor(TracingProcessor):
    def __init__(
        self,
        endpoint: str,
        service_name: str,
        sample_ratio: float = 1.0,
        span_exporter: SpanExporter | None = None,
    ) -> None:
        if not isfinite(sample_ratio) or not 0.0 <= sample_ratio <= 1.0:
            raise ValueError("OTEL_TRACE_SAMPLE_RATIO must be a number between 0.0 and 1.0")

        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
            sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                span_exporter
                or OTLPSpanExporter(
                    endpoint=endpoint,
                    insecure=endpoint.startswith("http://"),
                )
            )
        )
        self._provider = provider
        self._tracer = provider.get_tracer("openai-agents")
        self._traces: dict[str, OtelSpan] = {}
        self._spans: dict[str, OtelSpan] = {}
        self._span_trace_ids: dict[str, str] = {}
        self._lock = RLock()

    def on_trace_start(self, sdk_trace: Trace) -> None:
        with self._lock:
            self._traces[sdk_trace.trace_id] = self._tracer.start_span(
                sdk_trace.name,
                attributes={"openai_agents.trace_id": sdk_trace.trace_id},
                start_time=_timestamp_ns(sdk_trace.started_at),
            )

    def on_trace_end(self, sdk_trace: Trace) -> None:
        with self._lock:
            self._end_trace(sdk_trace.trace_id, _timestamp_ns(sdk_trace.ended_at))

    def on_span_start(self, span: Span[Any]) -> None:
        with self._lock:
            parent = self._spans.get(span.parent_id or "")
            if parent is None:
                parent = self._traces.get(span.trace_id)
            context = (
                otel_trace.set_span_in_context(parent, Context())
                if parent is not None
                else Context()
            )
            span_data = span.span_data
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
            self._spans[span.span_id] = self._tracer.start_span(
                span_name,
                context=context,
                attributes=attributes,
                start_time=_timestamp_ns(span.started_at),
            )
            self._span_trace_ids[span.span_id] = span.trace_id

    def on_span_end(self, span: Span[Any]) -> None:
        with self._lock:
            otel_span = self._spans.pop(span.span_id, None)
            self._span_trace_ids.pop(span.span_id, None)
            if otel_span is not None:
                if span.error is not None:
                    otel_span.set_status(Status(StatusCode.ERROR, "Agent operation failed"))
                otel_span.end(end_time=_timestamp_ns(span.ended_at))

    def force_flush(self) -> None:
        self._provider.force_flush()

    def shutdown(self) -> None:
        with self._lock:
            for trace_id in list(self._traces):
                self._end_trace(trace_id)
        self._provider.shutdown()

    def _end_trace(self, trace_id: str, end_time: int | None = None) -> None:
        span_ids = [
            span_id
            for span_id, span_trace_id in self._span_trace_ids.items()
            if span_trace_id == trace_id
        ]
        for span_id in reversed(span_ids):
            span = self._spans.pop(span_id, None)
            self._span_trace_ids.pop(span_id, None)
            if span is not None:
                span.set_status(Status(StatusCode.ERROR, "Agent operation did not complete"))
                span.end()

        trace_span = self._traces.pop(trace_id, None)
        if trace_span is not None:
            trace_span.end(end_time=end_time)
