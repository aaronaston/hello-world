from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import warnings

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agents.tracing.traces import TraceImpl
from hello_agent import get_otlp_endpoint, load_environment, warn_if_remote_sensitive_endpoint
from otel_tracing import (
    OpenTelemetryTracingProcessor,
    _timestamp_ns,
    parse_include_sensitive_data,
    parse_sample_ratio,
    parse_span_data_max_length,
)


def _iso_timestamp(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _span_data(span_type: str, name: str | None = None, **data: object) -> SimpleNamespace:
    exported = {"type": span_type, **data}
    if name is not None:
        exported["name"] = name
    return SimpleNamespace(
        type=span_type,
        name=name,
        **data,
        export=lambda: exported,
    )


class AgentExampleTests(unittest.TestCase):
    def test_sdk_trace_callbacks_do_not_require_timestamp_attributes(self) -> None:
        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
        )
        sdk_trace = TraceImpl(
            name="SDK trace",
            trace_id="test-trace",
            group_id=None,
            metadata=None,
            processor=processor,
        )
        sdk_trace.start()
        sdk_trace.finish()
        processor.force_flush()
        try:
            spans = exporter.get_finished_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].name, "SDK trace")
        finally:
            processor.shutdown()

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

    def test_warns_for_remote_sensitive_trace_endpoint(self) -> None:
        with self.assertWarnsRegex(UserWarning, "non-loopback OTLP endpoint"):
            warn_if_remote_sensitive_endpoint("https://collector.example:4317", True)
        with warnings.catch_warnings(record=True) as caught:
            warn_if_remote_sensitive_endpoint("http://127.0.0.1:4317", True)
            warn_if_remote_sensitive_endpoint("https://collector.example:4317", False)
            self.assertEqual(caught, [])

    def test_exports_nested_spans_with_payload_attributes(self) -> None:
        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
        )
        sdk_trace = SimpleNamespace(
            trace_id="trace_0123456789abcdef0123456789abcdef",
            name="test workflow",
        )
        agent_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="agent",
            parent_id=None,
            span_data=_span_data("agent", "Assistant", model=None),
            started_at=_iso_timestamp(1),
            ended_at=_iso_timestamp(4),
            error=None,
        )
        tool_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="tool",
            parent_id="agent",
            span_data=_span_data(
                "function",
                "get_weather",
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
            span_data=_span_data(
                "generation",
                input=[{"role": "user", "content": "private prompt"}],
                output=[{"role": "assistant", "content": "private response"}],
                model="test-model",
            ),
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
            span_data = json.loads(tool.attributes["openai_agents.span_data"])
            self.assertEqual(span_data["input"], "sensitive input")
            self.assertEqual(span_data["output"], "sensitive output")
            generation_data = json.loads(generation.attributes["openai_agents.span_data"])
            self.assertEqual(generation_data["input"][0]["content"], "private prompt")
            self.assertEqual(generation_data["output"][0]["content"], "private response")
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

    def test_truncates_large_payload_to_configured_limit(self) -> None:
        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
            span_data_max_length=128,
        )
        sdk_trace = SimpleNamespace(trace_id="limited-trace", name="limited workflow")
        span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="large-output",
            parent_id=None,
            # NUL expands to six characters in JSON, exercising worst-case preview escaping.
            span_data=_span_data("generation", output="\x00" * 100),
            started_at=_iso_timestamp(1),
            ended_at=_iso_timestamp(2),
            error=None,
        )
        processor.on_trace_start(sdk_trace)
        processor.on_span_start(span)
        processor.on_span_end(span)
        processor.on_trace_end(sdk_trace)
        processor.force_flush()

        try:
            exported = next(
                finished
                for finished in exporter.get_finished_spans()
                if finished.name == "generation"
            )
            payload = exported.attributes["openai_agents.span_data"]
            self.assertLessEqual(len(payload), 128)
            self.assertTrue(payload.startswith("{"))
            self.assertIn("[truncated;", payload)
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
        )
        agent_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="agent",
            parent_id=None,
            span_data=_span_data("agent", "Assistant"),
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
            self.assertGreaterEqual(agent.end_time, agent.start_time)
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
        )
        agent_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="agent",
            parent_id=None,
            span_data=_span_data("agent", "Assistant"),
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
        )
        agent_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="late-agent",
            parent_id=None,
            span_data=_span_data("agent", "Late"),
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
        )
        agent_span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="agent",
            parent_id=None,
            span_data=_span_data("agent", "Assistant"),
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
        self.assertEqual(parse_span_data_max_length("1024"), 1024)
        with self.assertRaisesRegex(ValueError, "OTEL_SPAN_DATA_MAX_LENGTH"):
            parse_span_data_max_length("64")
        self.assertTrue(parse_include_sensitive_data("true"))
        self.assertFalse(parse_include_sensitive_data("false"))
        with self.assertRaisesRegex(ValueError, "OTEL_TRACE_INCLUDE_SENSITIVE_DATA"):
            parse_include_sensitive_data("sometimes")

    def test_sensitive_payload_export_can_be_disabled(self) -> None:
        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
            include_span_data=False,
        )
        sdk_trace = SimpleNamespace(trace_id="redacted-trace", name="redacted workflow")
        span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="redacted-generation",
            parent_id=None,
            span_data=_span_data("generation", input="private prompt", output="private answer"),
            started_at=_iso_timestamp(1),
            ended_at=_iso_timestamp(2),
            error=None,
        )
        processor.on_trace_start(sdk_trace)
        processor.on_span_start(span)
        processor.on_span_end(span)
        processor.on_trace_end(sdk_trace)
        processor.force_flush()

        try:
            exported = next(
                finished
                for finished in exporter.get_finished_spans()
                if finished.name == "generation"
            )
            self.assertNotIn("openai_agents.span_data", exported.attributes)
        finally:
            processor.shutdown()

    def test_payload_export_failure_does_not_escape_callback(self) -> None:
        def fail_export() -> dict[str, str]:
            raise RuntimeError("sensitive failure detail")

        exporter = InMemorySpanExporter()
        processor = OpenTelemetryTracingProcessor(
            "http://127.0.0.1:9",
            "test-agent",
            span_exporter=exporter,
        )
        sdk_trace = SimpleNamespace(trace_id="failed-export", name="failure workflow")
        span = SimpleNamespace(
            trace_id=sdk_trace.trace_id,
            span_id="bad-payload",
            parent_id=None,
            span_data=SimpleNamespace(type="generation", export=fail_export),
            started_at=_iso_timestamp(1),
            ended_at=_iso_timestamp(2),
            error=None,
        )
        processor.on_trace_start(sdk_trace)
        processor.on_span_start(span)
        processor.on_span_end(span)
        processor.on_trace_end(sdk_trace)
        processor.force_flush()
        try:
            exported = next(
                finished
                for finished in exporter.get_finished_spans()
                if finished.name == "generation"
            )
            self.assertEqual(exported.status.status_code.name, "ERROR")
            self.assertNotIn("sensitive failure detail", str(exported.attributes))
        finally:
            processor.shutdown()

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
