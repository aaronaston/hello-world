from datetime import datetime, timezone
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from hello_agent import get_otlp_endpoint, load_environment
from otel_tracing import OpenTelemetryTracingProcessor, _timestamp_ns, parse_sample_ratio


def _iso_timestamp(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class AgentExampleTests(unittest.TestCase):
    def test_loads_values_from_dotenv_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dotenv_path = Path(directory) / ".env"
            dotenv_path.write_text("HELLO_WORLD_DOTENV_TEST=loaded\n")
            with patch.dict(os.environ, {}, clear=True):
                load_environment(str(dotenv_path))
                self.assertEqual(os.environ["HELLO_WORLD_DOTENV_TEST"], "loaded")

    def test_trace_endpoint_overrides_general_endpoint(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://general:4317",
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://traces:4317",
            },
        ):
            self.assertEqual(get_otlp_endpoint(), "https://traces:4317")

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
        generation_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="generation",
            parent_id=None,
            span_data=SimpleNamespace(type="generation", name=None, model="test-model"),
            started_at=_iso_timestamp(2),
            ended_at=_iso_timestamp(3),
            error=None,
        )
        processor.on_trace_start(sdk_trace)
        processor.on_span_start(agent_span)
        processor.on_span_start(generation_span)
        processor.on_span_start(tool_span)
        processor.on_span_end(tool_span)
        processor.on_span_end(generation_span)
        processor.on_span_end(agent_span)
        processor.on_trace_end(sdk_trace)
        processor.force_flush()

        spans = exporter.get_finished_spans()
        try:
            tool = next(span for span in spans if span.name == "function: get_weather")
            agent = next(span for span in spans if span.name == "agent: Assistant")
            workflow = next(span for span in spans if span.name == "test workflow")
            generation = next(span for span in spans if span.name == "generation")
            self.assertEqual(tool.parent.span_id, agent.context.span_id)
            self.assertEqual(agent.parent.span_id, workflow.context.span_id)
            self.assertEqual(generation.parent.span_id, workflow.context.span_id)
            self.assertEqual(tool.start_time, _timestamp_ns(_iso_timestamp(2)))
            self.assertEqual(tool.status.status_code.name, "ERROR")
            self.assertEqual(tool.status.description, "Agent operation failed")
            self.assertEqual(generation.status.status_code.name, "UNSET")
            self.assertEqual(generation.attributes["gen_ai.request.model"], "test-model")
            self.assertNotIn("input", tool.attributes)
            self.assertNotIn("output", tool.attributes)
            self.assertNotIn("sensitive error", str(tool.attributes))
            self.assertEqual(
                {span.name for span in spans},
                {
                    "test workflow",
                    "agent: Assistant",
                    "function: get_weather",
                    "generation",
                },
            )
        finally:
            processor.shutdown()

    def test_trace_end_closes_unfinished_spans(self) -> None:
        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
        )
        sdk_trace = SimpleNamespace(
            trace_id="trace_0123456789abcdef0123456789abcdef",
            name="unfinished workflow",
            started_at=_iso_timestamp(1),
            ended_at=_iso_timestamp(4),
        )
        agent_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="agent",
            parent_id=None,
            span_data=SimpleNamespace(type="agent", name="Assistant"),
            started_at=_iso_timestamp(5),
            ended_at=None,
            error=None,
        )
        processor.on_trace_start(sdk_trace)
        processor.on_span_start(agent_span)
        processor.on_trace_end(sdk_trace)
        processor.force_flush()

        spans = exporter.get_finished_spans()
        try:
            agent = next(span for span in spans if span.name == "agent: Assistant")
            self.assertEqual(agent.status.status_code.name, "ERROR")
            self.assertEqual(agent.end_time, agent.start_time)
            workflow = next(span for span in spans if span.name == "unfinished workflow")
            self.assertEqual(workflow.end_time, agent.end_time)
        finally:
            processor.shutdown()

    def test_late_span_end_after_trace_end_is_ignored(self) -> None:
        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
        )
        sdk_trace = SimpleNamespace(
            trace_id="trace_0123456789abcdef0123456789abcdef",
            name="late end workflow",
            started_at=_iso_timestamp(1),
            ended_at=_iso_timestamp(3),
        )
        agent_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="agent",
            parent_id=None,
            span_data=SimpleNamespace(type="agent", name="Assistant"),
            started_at=_iso_timestamp(1),
            ended_at=None,
            error=None,
        )
        processor.on_trace_start(sdk_trace)
        processor.on_span_start(agent_span)
        processor.on_trace_end(sdk_trace)
        processor.on_span_end(agent_span)
        processor.force_flush()
        try:
            spans = exporter.get_finished_spans()
            self.assertEqual(len(spans), 2)
            self.assertEqual(
                {span.name for span in spans},
                {"late end workflow", "agent: Assistant"},
            )
        finally:
            processor.shutdown()

    def test_late_span_start_after_trace_end_is_ignored(self) -> None:
        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
        )
        sdk_trace = SimpleNamespace(
            trace_id="trace_0123456789abcdef0123456789abcdef",
            name="late start workflow",
            started_at=_iso_timestamp(1),
            ended_at=_iso_timestamp(3),
        )
        agent_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="late-agent",
            parent_id=None,
            span_data=SimpleNamespace(type="agent", name="Late"),
            started_at=_iso_timestamp(2),
            ended_at=_iso_timestamp(2),
            error=None,
        )
        processor.on_trace_start(sdk_trace)
        processor.on_trace_end(sdk_trace)
        processor.on_span_start(agent_span)
        processor.force_flush()
        try:
            spans = exporter.get_finished_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].name, "late start workflow")
        finally:
            processor.shutdown()

    def test_shutdown_closes_unfinished_trace_and_spans(self) -> None:
        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
        )
        sdk_trace = SimpleNamespace(
            trace_id="trace_0123456789abcdef0123456789abcdef",
            name="shutdown workflow",
            started_at=_iso_timestamp(1),
            ended_at=None,
        )
        agent_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="agent",
            parent_id=None,
            span_data=SimpleNamespace(type="agent", name="Assistant"),
            started_at=_iso_timestamp(1),
            ended_at=None,
            error=None,
        )
        processor.on_trace_start(sdk_trace)
        processor.on_span_start(agent_span)
        processor.shutdown()

        spans = exporter.get_finished_spans()
        agent = next(span for span in spans if span.name == "agent: Assistant")
        workflow = next(span for span in spans if span.name == "shutdown workflow")
        self.assertEqual(agent.status.status_code.name, "ERROR")
        self.assertEqual(agent.end_time, workflow.end_time)

    def test_duplicate_trace_start_closes_previous_trace(self) -> None:
        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
        )
        sdk_trace = SimpleNamespace(
            trace_id="trace_0123456789abcdef0123456789abcdef",
            name="duplicate workflow",
            started_at=_iso_timestamp(1),
            ended_at=_iso_timestamp(3),
        )
        processor.on_trace_start(sdk_trace)
        processor.on_trace_start(sdk_trace)
        processor.on_trace_end(sdk_trace)
        processor.force_flush(timeout_millis=1000)

        spans = exporter.get_finished_spans()
        self.assertEqual(len(spans), 2)
        self.assertEqual([span.name for span in spans], ["duplicate workflow"] * 2)
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
        with self.assertRaisesRegex(ValueError, "OTEL_TRACE_SAMPLE_RATIO"):
            parse_sample_ratio("not a number")
        self.assertEqual(parse_sample_ratio("0.5"), 0.5)

    def test_endpoint_requires_http_or_https_scheme(self) -> None:
        for endpoint in ("localhost:4317", "ftp://host"):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(
                ValueError, "endpoint must be an http:// or https:// URL"
            ):
                OpenTelemetryTracingProcessor(
                    endpoint,
                    "test-agent",
                    span_exporter=InMemorySpanExporter(),
                )

        for endpoint in ("http://localhost:4317", "https://collector.example:4317"):
            with self.subTest(endpoint=endpoint):
                processor = OpenTelemetryTracingProcessor(
                    endpoint,
                    "test-agent",
                    span_exporter=InMemorySpanExporter(),
                )
                processor.shutdown()

    def test_timestamp_conversion_preserves_microseconds(self) -> None:
        self.assertEqual(
            _timestamp_ns("1970-01-01T00:00:01.123456Z"),
            1_123_456_000,
        )
        self.assertEqual(
            _timestamp_ns("1970-01-01T00:00:01.123456"),
            1_123_456_000,
        )
        self.assertIsNone(_timestamp_ns(None))
        self.assertIsNone(_timestamp_ns("not an ISO timestamp"))


if __name__ == "__main__":
    unittest.main()
