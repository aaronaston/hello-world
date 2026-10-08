from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Any

from agents.tracing import Span, Trace, TracingProcessor
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span as OtelSpan
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Status, StatusCode
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter


def _timestamp_ns(value: str | None) -> int | None:
    if value is None:
        return None
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(timestamp.timestamp() * 1_000_000_000)


class OpenTelemetryTracingProcessor(TracingProcessor):
    def __init__(
        self,
        endpoint: str,
        service_name: str,
        sample_ratio: float = 1.0,
    ) -> None:
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
            sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=endpoint,
                    insecure=endpoint.startswith("http://"),
                )
            )
        )
        self._provider = provider
        self._tracer = provider.get_tracer("openai-agents")
        self._traces: dict[str, OtelSpan] = {}
        self._spans: dict[str, OtelSpan] = {}
        self._lock = RLock()

    def on_trace_start(self, trace: Trace) -> None:
        with self._lock:
            self._traces[trace.trace_id] = self._tracer.start_span(
                trace.name,
                attributes={"openai_agents.trace_id": trace.trace_id},
                start_time=_timestamp_ns(trace.started_at),
            )

    def on_trace_end(self, trace: Trace) -> None:
        with self._lock:
            span = self._traces.pop(trace.trace_id, None)
            if span is not None:
                span.end(end_time=_timestamp_ns(trace.ended_at))

    def on_span_start(self, span: Span[Any]) -> None:
        with self._lock:
            parent = self._spans.get(span.parent_id or "") or self._traces.get(span.trace_id)
            context = trace.set_span_in_context(parent, Context()) if parent else Context()
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

    def on_span_end(self, span: Span[Any]) -> None:
        with self._lock:
            otel_span = self._spans.pop(span.span_id, None)
            if otel_span is not None:
                if span.error is not None:
                    otel_span.set_status(Status(StatusCode.ERROR))
                otel_span.end(end_time=_timestamp_ns(span.ended_at))

    def force_flush(self) -> None:
        self._provider.force_flush()

    def shutdown(self) -> None:
        self._provider.shutdown()

