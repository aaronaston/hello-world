"""Minimal OpenAI Agents SDK example: an agent with one function tool.

Run: add OPENAI_API_KEY=... to .env (or export it in your shell); python hello_agent.py

All settings may be set in .env or as environment variables:
  OPENAI_API_KEY, OPENAI_MODEL, AGENT_NAME, AGENT_INSTRUCTIONS, AGENT_PROMPT,
  OTEL_ENABLED (default false: use the default OpenAI tracing; true: export via OpenTelemetry),
  OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_EXPORTER_OTLP_TRACES_ENDPOINT, OTEL_SERVICE_NAME,
  OTEL_TRACE_SAMPLE_RATIO, OTEL_SPAN_DATA_MAX_LENGTH, OTEL_TRACE_INCLUDE_SENSITIVE_DATA
Docs: https://openai.github.io/openai-agents-python/
"""
from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit
import warnings

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


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def is_otel_enabled() -> bool:
    return parse_bool(os.getenv("OTEL_ENABLED"), default=False)


def parse_include_sensitive_data(value: str) -> bool:
    from otel_tracing import parse_include_sensitive_data as parse

    return parse(value)


def get_otlp_endpoint() -> str:
    return os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
    ).strip()


def is_loopback_endpoint(endpoint: str) -> bool:
    hostname = urlsplit(endpoint.strip()).hostname
    try:
        return hostname is not None and ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return hostname == "localhost"


def get_include_sensitive_data(endpoint: str, setting: str | None) -> bool:
    if setting is None:
        return is_loopback_endpoint(endpoint)
    return parse_include_sensitive_data(setting)


def warn_if_remote_sensitive_endpoint(endpoint: str, include_sensitive_data: bool) -> None:
    if include_sensitive_data and not is_loopback_endpoint(endpoint):
        warnings.warn(
            "Sensitive agent trace payloads are enabled and will be sent to a non-loopback "
            "OTLP endpoint.",
            UserWarning,
            stacklevel=2,
        )


@function_tool
def get_weather(city: str) -> str:
    """Return a (fake) weather report for a city."""
    return f"The weather in {city} is sunny."


agent_kwargs = {}
if os.getenv("OPENAI_MODEL", "").strip():
    agent_kwargs["model"] = os.environ["OPENAI_MODEL"].strip()

agent = Agent(
    name=os.getenv("AGENT_NAME", "Assistant"),
    instructions=os.getenv(
        "AGENT_INSTRUCTIONS", "You are a helpful assistant. Use tools when useful."
    ),
    tools=[get_weather],
    **agent_kwargs,
)

def main() -> None:
    prompt = os.getenv("AGENT_PROMPT", "What's the weather in Paris?")
    processor = None
    run_config = RunConfig()
    if is_otel_enabled():
        from otel_tracing import (
            OpenTelemetryTracingProcessor,
            parse_sample_ratio,
            parse_span_data_max_length,
        )

        endpoint = get_otlp_endpoint()
        service_name = os.getenv("OTEL_SERVICE_NAME", "hello-world-agent")
        sample_ratio = parse_sample_ratio(os.getenv("OTEL_TRACE_SAMPLE_RATIO", "1.0"))
        span_data_max_length = parse_span_data_max_length(
            os.getenv("OTEL_SPAN_DATA_MAX_LENGTH", "16384")
        )
        include_sensitive_data = get_include_sensitive_data(
            endpoint,
            os.getenv("OTEL_TRACE_INCLUDE_SENSITIVE_DATA"),
        )
        warn_if_remote_sensitive_endpoint(endpoint, include_sensitive_data)
        processor = OpenTelemetryTracingProcessor(
            endpoint,
            service_name,
            sample_ratio,
            span_data_max_length=span_data_max_length,
            include_span_data=include_sensitive_data,
        )
        set_trace_processors([processor])
        run_config = RunConfig(trace_include_sensitive_data=include_sensitive_data)
    try:
        result = Runner.run_sync(agent, prompt, run_config=run_config)
        print(result.final_output)
    finally:
        flush_traces()
        if processor is not None:
            processor.shutdown()


if __name__ == "__main__":
    main()
