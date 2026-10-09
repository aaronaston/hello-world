# Agent experiments

A small learning playground for agent frameworks, MCP tools, and agent
observability. This tutorial walks through the Python OpenAI Agents SDK example
and its local OpenTelemetry-to-Jaeger pipeline.

## What you will build

The `openai-agents/` directory contains one agent with a weather function tool.
When run, the program asks the agent for the weather in Paris. The agent can
call `get_weather`, which returns a fixed, fictional sunny forecast.

The example also exports traces so you can inspect what happened:

1. The Agents SDK records the workflow and its agent, model, and tool spans.
2. `otel_tracing.py` converts those SDK spans into OpenTelemetry spans and sends
   them over OTLP gRPC.
3. The OpenTelemetry Collector receives the spans and forwards them to Jaeger.
4. Jaeger provides a web UI for searching and inspecting traces.

The Collector and Jaeger run locally with Docker Compose; the Python agent runs
on your machine.

## 1. Install the Python dependencies

Use Python 3.10 or newer. From the repository root:

```bash
cd openai-agents
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate the environment with
`.venv\Scripts\Activate.ps1` instead.

## 2. Start the local trace services

Make sure Docker is running, then start the Collector and Jaeger:

```bash
docker compose up -d
```

The Collector listens for OTLP on localhost ports `4317` (gRPC) and `4318`
(HTTP). This example uses gRPC on port `4317`. Jaeger’s UI is at
<http://localhost:16686>. The published ports are bound to localhost only.

## 3. Configure and run the agent

Create `openai-agents/.env` with your API key:

```dotenv
OPENAI_API_KEY=your-api-key
```

The `.env` file is ignored by Git. Alternatively, set `OPENAI_API_KEY` in your
shell. Shell environment variables take precedence over values in `.env`. Then
run the example:

```bash
python hello_agent.py
```

The SDK sends the request to the model, which can choose the weather function
tool; the program prints the agent’s final response. The weather data itself is
hard-coded in `get_weather`—it does not call a weather service.

In a separate browser tab, open Jaeger at <http://localhost:16686>. Find the
`hello-world-agent` service, select a trace, and inspect its workflow and child
spans. A tool span appears when the model calls the function.

## 4. Run the tests

The unit tests use an in-memory OpenTelemetry exporter and do not need an API
key or a running Collector:

```bash
python -m unittest -v test_otel_tracing
```

They check span parent/child relationships, timestamps, sampling validation,
error handling, incomplete-span cleanup, and that model/tool input and output
payloads are included in exported spans.

## Configuration

All settings are optional:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | `http://localhost:4317` | OTLP gRPC traces endpoint. Takes precedence over the general endpoint. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Fallback OTLP gRPC endpoint. |
| `OTEL_SERVICE_NAME` | `hello-world-agent` | Service name shown in Jaeger. |
| `OTEL_TRACE_SAMPLE_RATIO` | `1.0` | Parent-based sampling ratio from `0.0` (none) to `1.0` (all). |
| `OTEL_SPAN_DATA_MAX_LENGTH` | `16384` | Maximum characters per serialized span payload; must be at least `128`. |
| `OTEL_TRACE_INCLUDE_SENSITIVE_DATA` | `true` for localhost; `false` otherwise | Set explicitly to `true` or `false` to control model/tool payload capture in both the SDK and Jaeger export. Accepts `true`, `false`, `1`, or `0`. |

Use a gRPC endpoint, not the Collector’s HTTP port `4318`. An `http://` endpoint
uses plaintext transport; `https://` uses TLS.

## Privacy and sensitive trace contents

This example replaces the Agents SDK’s default trace exporter with OTLP, so
traces go to the configured Collector rather than the OpenAI Traces dashboard.
For localhost endpoints, sensitive-data tracing is enabled by default. Span
details exported to Jaeger include model inputs and outputs and tool arguments
and results, allowing you to inspect what the agent received and produced. For
non-loopback endpoints, sensitive-data tracing defaults to off; explicitly set
`OTEL_TRACE_INCLUDE_SENSITIVE_DATA=true` to enable it, which emits a warning.
Payloads longer than `OTEL_SPAN_DATA_MAX_LENGTH` are truncated into a valid JSON
preview; raise the limit to keep more detail, keeping Collector/Jaeger
message-size limits in mind. The limit caps the exported attribute, not the work
of building the SDK payload and serializing it before truncation, so
exceptionally large payloads can still consume memory and CPU.

These traces can contain prompts, personal data, or other secrets. The local
Jaeger UI is bound to localhost, but data is still sent to the configured
Collector and retained according to the Jaeger setup. Use only with data you
are allowed to send there. Set `OTEL_TRACE_INCLUDE_SENSITIVE_DATA=false` to
disable capture and export before using real user data or a shared/production
collector. Non-local endpoints default to this safer setting.

## Stop the services

When finished, stop and remove the local containers:

```bash
docker compose down
```

## Project files

- `openai-agents/hello_agent.py` — agent, function tool, and run configuration.
- `openai-agents/otel_tracing.py` — Agents SDK to OpenTelemetry span processor.
- `openai-agents/otel-collector-config.yaml` — Collector OTLP receiver and
  Jaeger exporter configuration.
- `openai-agents/compose.yaml` — local Collector and Jaeger services.
- `openai-agents/test_otel_tracing.py` — unit tests for tracing behavior.
