from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from otel_tracing import OpenTelemetryTracingProcessor, _timestamp_ns


def _iso_timestamp(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class OpenTelemetryTracingProcessorTests(unittest.TestCase):
    def test_exports_nested_spans_without_payload_attributes(self) -> None:
        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
        )
        sdk_trace = SimpleNamespace(
            trace_id="trace_0123456789abcdef0123456789abcdef",
            name="test workflow",
            started_at=_iso_timestamp(1),
            ended_at=_iso_timestamp(4),
        )
        agent_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="agent",
            parent_id=None,
            span_data=SimpleNamespace(type="agent", name="Assistant", model=None),
            started_at=_iso_timestamp(1),
            ended_at=_iso_timestamp(4),
            error=None,
        )
        tool_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="tool",
            parent_id="agent",
            span_data=SimpleNamespace(
                type="function",
                name="get_weather",
                input="sensitive input",
                output="sensitive output",
            ),
            started_at=_iso_timestamp(2),
            ended_at=_iso_timestamp(3),
            error={"message": "sensitive error", "data": None},
        )

        processor.on_trace_start(sdk_trace)
        processor.on_span_start(agent_span)
        processor.on_span_start(tool_span)
        processor.on_span_end(tool_span)
        processor.on_span_end(agent_span)
        processor.on_trace_end(sdk_trace)
        processor.force_flush()

        spans = exporter.get_finished_spans()
        try:
            tool = next(span for span in spans if span.name == "function: get_weather")
            agent = next(span for span in spans if span.name == "agent: Assistant")
            workflow = next(span for span in spans if span.name == "test workflow")
            self.assertEqual(tool.parent.span_id, agent.context.span_id)
            self.assertEqual(agent.parent.span_id, workflow.context.span_id)
            self.assertEqual(tool.start_time, _timestamp_ns(_iso_timestamp(2)))
            self.assertEqual(tool.status.status_code.name, "ERROR")
            self.assertEqual(tool.status.description, "Agent operation failed")
            self.assertNotIn("input", tool.attributes)
            self.assertNotIn("output", tool.attributes)
            self.assertNotIn("sensitive error", str(tool.attributes))
            self.assertEqual(
                {span.name for span in spans},
                {"test workflow", "agent: Assistant", "function: get_weather"},
            )
        finally:
            processor.shutdown()

    def test_rejects_invalid_sampling_ratio(self) -> None:
        for ratio in (-0.1, 1.1, float("nan")):
            with self.subTest(ratio=ratio), self.assertRaisesRegex(
                ValueError, "OTEL_TRACE_SAMPLE_RATIO"
            ):
                OpenTelemetryTracingProcessor(
                    "http://127.0.0.1:9",
                    "test-agent",
                    sample_ratio=ratio,
                    span_exporter=InMemorySpanExporter(),
                )

    def test_timestamp_conversion_preserves_microseconds(self) -> None:
        self.assertEqual(
            _timestamp_ns("1970-01-01T00:00:01.123456Z"),
            1_123_456_000,
        )


if __name__ == "__main__":
    unittest.main()
