# Agent experiments

A learning playground for agent frameworks, and how agents use MCP tools and talk to other agents.

## Experiments

- [`openai-agents/`](openai-agents/) – OpenAI Agents SDK (Python): a minimal agent with a function tool.

```
cd openai-agents
pip install -r requirements.txt
export OPENAI_API_KEY=...
docker compose up -d
python hello_agent.py
```

The Agents SDK traces runs, model calls, and tool calls to a local OpenTelemetry
Collector, which forwards them to Jaeger. Open Jaeger at
<http://localhost:16686>; stop the services with `docker compose down`.
The published Collector and Jaeger ports are bound to localhost.

Set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (or `OTEL_EXPORTER_OTLP_ENDPOINT`) to
change the collector endpoint, and `OTEL_SERVICE_NAME` to change the service
name. `OTEL_TRACE_SAMPLE_RATIO` controls sampling from `0.0` to `1.0` (defaults
to `1.0`). The endpoint must be the collector's OTLP gRPC address (port `4317`).
This example replaces the SDK's default OpenAI trace exporter with the OTLP
exporter and excludes prompts and tool inputs/outputs from traces. Span names
and agent/tool names are still exported; avoid putting sensitive information
in those names.
