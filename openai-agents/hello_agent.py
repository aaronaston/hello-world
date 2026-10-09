"""Minimal OpenAI Agents SDK example: an agent with one function tool.

Run: add OPENAI_API_KEY=... to .env (or export it in your shell); python hello_agent.py
Docs: https://openai.github.io/openai-agents-python/
"""
from __future__ import annotations

import os

from dotenv import load_dotenv


def load_environment(dotenv_path: str | None = None) -> None:
    load_dotenv(dotenv_path=dotenv_path)


# Load credentials before importing the SDK, which may read environment configuration.
load_environment()

from agents import (  # noqa: E402
    Agent,
    RunConfig,
    Runner,
    flush_traces,
    function_tool,
    set_trace_processors,
)

from otel_tracing import (  # noqa: E402
    OpenTelemetryTracingProcessor,
    parse_include_sensitive_data,
    parse_sample_ratio,
    parse_span_data_max_length,
)


def get_otlp_endpoint() -> str:
    return os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
    )


@function_tool
def get_weather(city: str) -> str:
    """Return a (fake) weather report for a city."""
    return f"The weather in {city} is sunny."


agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant. Use tools when useful.",
    tools=[get_weather],
)

if __name__ == "__main__":
    endpoint = get_otlp_endpoint()
    service_name = os.getenv("OTEL_SERVICE_NAME", "hello-world-agent")
    sample_ratio = parse_sample_ratio(os.getenv("OTEL_TRACE_SAMPLE_RATIO", "1.0"))
    span_data_max_length = parse_span_data_max_length(
        os.getenv("OTEL_SPAN_DATA_MAX_LENGTH", "16384")
    )
    include_sensitive_data = parse_include_sensitive_data(
        os.getenv("OTEL_TRACE_INCLUDE_SENSITIVE_DATA", "true")
    )
    processor = OpenTelemetryTracingProcessor(
        endpoint,
        service_name,
        sample_ratio,
        span_data_max_length=span_data_max_length,
        include_span_data=include_sensitive_data,
    )
    set_trace_processors([processor])
    try:
        result = Runner.run_sync(
            agent,
            "What's the weather in Paris?",
            run_config=RunConfig(trace_include_sensitive_data=include_sensitive_data),
        )
        print(result.final_output)
    finally:
        flush_traces()
        processor.shutdown()
