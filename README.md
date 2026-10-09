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
error handling, incomplete-span cleanup, and that prompt/tool payloads are not
exported as span attributes.

## Configuration

All settings are optional:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | `http://localhost:4317` | OTLP gRPC traces endpoint. Takes precedence over the general endpoint. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Fallback OTLP gRPC endpoint. |
| `OTEL_SERVICE_NAME` | `hello-world-agent` | Service name shown in Jaeger. |
| `OTEL_TRACE_SAMPLE_RATIO` | `1.0` | Parent-based sampling ratio from `0.0` (none) to `1.0` (all). |

Use a gRPC endpoint, not the Collector’s HTTP port `4318`. An `http://` endpoint
uses plaintext transport; `https://` uses TLS.

## Privacy notes

This example replaces the Agents SDK’s default trace exporter with OTLP, so
traces go to the configured Collector rather than the OpenAI Traces dashboard.
It disables sensitive data in the SDK run configuration and the OpenTelemetry
processor exports span types, names, and model names—not prompts, tool
arguments, or tool results. Agent and tool names can still reveal information,
so keep sensitive content out of names. Tracing and data retention in other
backends are controlled by those services.

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
