from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from itertools import islice
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
_SPAN_DATA_LENGTH_ERROR = "OTEL_SPAN_DATA_MAX_LENGTH must be an integer of at least 128"
_SENSITIVE_DATA_ERROR = "OTEL_TRACE_INCLUDE_SENSITIVE_DATA must be true or false"
_MIN_SPAN_DATA_LENGTH = 128
_logger = logging.getLogger(__name__)


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


def parse_span_data_max_length(value: str) -> int:
    try:
        max_length = int(value)
    except ValueError as error:
        raise ValueError(_SPAN_DATA_LENGTH_ERROR) from error
    if max_length < _MIN_SPAN_DATA_LENGTH:
        raise ValueError(_SPAN_DATA_LENGTH_ERROR)
    return max_length


def parse_include_sensitive_data(value: str) -> bool:
    normalized = value.lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(_SENSITIVE_DATA_ERROR)


def _serialize_span_data(span_data: dict[str, Any], max_length: int) -> str:
    def unsupported_value(value: Any) -> str:
        return f"<{type(value).__name__}>"

    encoder = json.JSONEncoder(default=unsupported_value, ensure_ascii=False)
    chunks: list[str] = []
    size = 0
    for chunk in encoder.iterencode(span_data):
        size += len(chunk)
        if size > max_length:
            break
        chunks.append(chunk)
    else:
        return "".join(chunks)

    def preview_value(value: Any, string_limit: int, depth: int = 0) -> Any:
        if isinstance(value, str):
            return value[:string_limit]
        if depth >= 3:
            return "[nested value omitted]"
        if isinstance(value, dict):
            return {
                key[:string_limit] if isinstance(key, str) else str(key)[:string_limit]:
                preview_value(item, string_limit, depth + 1)
                for key, item in islice(value.items(), 8)
            }
        if isinstance(value, (list, tuple)):
            return [preview_value(item, string_limit, depth + 1) for item in value[:8]]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return preview_value(unsupported_value(value), string_limit, depth + 1)

    envelope = {"truncated": True, "preview": {}}
    for divisor in (8, 16, 32):
        envelope["preview"] = preview_value(span_data, max_length // divisor)
        truncated = json.dumps(envelope, default=unsupported_value, ensure_ascii=False)
        if len(truncated) <= max_length:
            return truncated
    envelope["preview"] = {}
    return json.dumps(envelope, ensure_ascii=False)


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
    """Export detailed Agents SDK spans through OTLP gRPC, including payloads.

    `endpoint` must be an `http://` or `https://` gRPC URL. `sample_ratio` sets
    parent-based head sampling between 0.0 and 1.0. The serialized SDK span
    data can contain prompts, completions, and tool inputs/outputs. HTTP URLs
    use plaintext; HTTPS URLs use TLS.
    """

    def __init__(
        self,
        endpoint: str,
        service_name: str,
        sample_ratio: float = 1.0,
        span_exporter: SpanExporter | None = None,
        span_data_max_length: int = 16_384,
        include_span_data: bool = True,
    ) -> None:
        """Create an OTLP exporter with the selected resource and sampling settings."""
        if not endpoint.lower().startswith(("http://", "https://")):
            raise ValueError("endpoint must be an http:// or https:// URL")
        sample_ratio = _validate_sample_ratio(sample_ratio)
        if span_data_max_length < _MIN_SPAN_DATA_LENGTH:
            raise ValueError(_SPAN_DATA_LENGTH_ERROR)

        exporter = span_exporter or OTLPSpanExporter(
            endpoint=endpoint,
            insecure=endpoint.lower().startswith("http://"),
        )
        span_processor = BatchSpanProcessor(exporter)
        try:
            provider = TracerProvider(
                resource=Resource.create({"service.name": service_name}),
                sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
            )
            provider.add_span_processor(span_processor)
        except Exception:
            span_processor.shutdown()
            raise
        self._provider = provider
        self._tracer = provider.get_tracer("openai-agents")
        self._traces: dict[str, OtelSpan] = {}
        self._spans: dict[str, dict[str, tuple[OtelSpan, int | None]]] = {}
        self._span_data_max_length = span_data_max_length
        self._include_span_data = include_span_data
        self._lock = RLock()

    def on_trace_start(self, sdk_trace: Trace) -> None:
        """Start the OTel workflow span."""
        with self._lock:
            if sdk_trace.trace_id in self._traces:
                self._end_trace(sdk_trace.trace_id)
            self._traces[sdk_trace.trace_id] = self._tracer.start_span(
                sdk_trace.name,
                attributes={"openai_agents.trace_id": sdk_trace.trace_id},
                start_time=time_ns(),
            )

    def on_trace_end(self, sdk_trace: Trace) -> None:
        """End the OTel workflow span and any SDK spans that remained open."""
        with self._lock:
            self._end_trace(sdk_trace.trace_id, time_ns())

    def on_span_start(self, span: Span[Any]) -> None:
        """Start an OTel span with the corresponding SDK parent and identifying attributes."""
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
        """Export detailed SDK payload data, then end the OTel span."""
        with self._lock:
            trace_spans = self._spans.get(span.trace_id)
            span_entry = trace_spans.pop(span.span_id, None) if trace_spans is not None else None
            if trace_spans is not None and not trace_spans:
                self._spans.pop(span.trace_id, None)
        if span_entry is None:
            return

        otel_span, _ = span_entry
        try:
            if span.error is not None:
                otel_span.set_status(Status(StatusCode.ERROR, "Agent operation failed"))
            if self._include_span_data:
                span_data = span.span_data.export()
                otel_span.set_attribute(
                    "openai_agents.span_data",
                    _serialize_span_data(span_data, self._span_data_max_length),
                )
        except Exception as error:
            if span.error is None:
                otel_span.set_status(
                    Status(StatusCode.ERROR, "Trace payload serialization failed")
                )
            _logger.warning(
                "Failed to export Agent SDK span payload (%s); omitting it",
                type(error).__name__,
            )
        finally:
            otel_span.end(end_time=_timestamp_ns(span.ended_at))

    def force_flush(self, timeout_millis: int = 5_000) -> None:
        """Flush spans buffered by the OTel provider."""
        self._provider.force_flush(timeout_millis=timeout_millis)

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
