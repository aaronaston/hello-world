"""Minimal OpenAI Agents SDK example: an agent with one function tool.

Run: export OPENAI_API_KEY=...; python hello_agent.py
Docs: https://openai.github.io/openai-agents-python/
"""
import os

from agents import Agent, Runner, function_tool
from agents import RunConfig, flush_traces, set_trace_processors

from otel_tracing import OpenTelemetryTracingProcessor


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
    endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
    )
    service_name = os.getenv("OTEL_SERVICE_NAME", "hello-world-agent")
    try:
        sample_ratio = float(os.getenv("OTEL_TRACE_SAMPLE_RATIO", "1.0"))
    except ValueError as error:
        raise ValueError("OTEL_TRACE_SAMPLE_RATIO must be a number between 0.0 and 1.0") from error
    processor = OpenTelemetryTracingProcessor(endpoint, service_name, sample_ratio)
    set_trace_processors([processor])
    try:
        result = Runner.run_sync(
            agent,
            "What's the weather in Paris?",
            run_config=RunConfig(trace_include_sensitive_data=False),
        )
        print(result.final_output)
    finally:
        flush_traces()
        processor.shutdown()
