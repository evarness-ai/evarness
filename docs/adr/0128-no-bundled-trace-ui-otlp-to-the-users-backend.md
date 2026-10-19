# ADR-0128: No bundled trace UI: OTLP to the user's backend

Status: Accepted (owner, 2026-10-01). Built on `refactor/remove-phoenix-otlp-only`.
Relates to: OSS plan R19 (lean, OSI-only core; addendum of 2026-10-01),
`docs/architecture/learning-observability.md` (D1: learning never depends on tracing),
ADR-0074 (per-agent console, built from session logs).

## Context

IRIS shipped Arize Phoenix as its trace UI. Since R19 it was the optional `[phoenix]`
extra (Elastic-2.0, so not in the OSI-only core), with two modes behind `IRIS_PHOENIX_*`:

- **embedded**: the Phoenix app launched inside the `iris_api` process, with its own
  web server and SQLite trace store, and with it the stack the extra pulls in (some 60
  locked packages only it needs: pandas, SQLAlchemy, strawberry-graphql, boto3, ...;
  see also `docs/experiments/2026-09-22-1gib-memory-budget.md`).
- **external**: `scripts/start_iris.sh` ran `phoenix serve` as a managed service and the
  API exported spans to it over OTLP.

What the user actually needs from IRIS is the spans, not a particular viewer. The
external mode already proved the point: spans went over plain OTLP, and any OTLP backend
could take them. Meanwhile the bundling carried cost everywhere: an extra, its
`IRIS_PHOENIX_*` settings (one with no production effect), a service in the start script,
a port and a build arg in Compose, a doctor row, a mypy override, a licence exception to
explain, and a second configuration language that duplicated what the standard
OpenTelemetry variables already say.

The web console's Call trace and Sessions screens never depended on it: they are built
from IRIS's own session logs (`foundation/observability/trace_builder.py`,
`session_log.py`), not from OpenTelemetry.

## Decision

1. **Remove Phoenix from the harness.** No `[phoenix]` extra, no `arize-phoenix`
   dependency, no `phoenix_setup.py`, no embedded or external mode, no `IRIS_PHOENIX_*`
   setting, no Phoenix service in `start_iris.sh`, Compose or the Dockerfile, and no
   doctor row.
2. **OpenTelemetry stays core, configured by the standard variables.**
   `foundation/observability/otlp_setup.py` builds a tracer provider only when
   `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` is set. The
   exporter is constructed with no arguments, so it reads the endpoint, headers, timeout,
   compression and TLS settings itself. `OTEL_EXPORTER_OTLP_(TRACES_)PROTOCOL` picks
   `http/protobuf` (default) or `grpc`. `Resource.create()` reads `OTEL_SERVICE_NAME` and
   `OTEL_RESOURCE_ATTRIBUTES`; IRIS fills `service.name=iris` only when neither names one.
   `OTEL_SDK_DISABLED=true` and `OTEL_TRACES_EXPORTER=none` turn export off.
3. **No endpoint, nothing.** No provider, no exporter, no instrumentation, and `/healthz`
   reports `tracing_enabled: false` with `observability_error: null`.
4. **Batched export, logged egress.** Spans go through a `BatchSpanProcessor`. The
   exporter is wrapped so that each batch sent is one `iris.egress` line naming the host
   (credentials, query and fragment stripped), like every other outbound call.
5. **IRIS settings only where the standard has none.** `IRIS_OTEL_LANGCHAIN_ENABLED` and
   `IRIS_OTEL_HTTPX_ENABLED` (default on) choose the auto-instrumented libraries; the
   standard `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS` applies only to the zero-code agent.
   `IRIS_CHANNEL_GATEWAY_TRACING_ENABLED` keeps gating the gateway. `IRIS_OTEL_ENABLED`
   is removed: its only production caller always passed `enabled=True`, and the standard
   off switches cover it.
6. **`opentelemetry-instrumentation-httpx` becomes a core dependency**, so the httpx
   switch works on a plain install. It follows the contrib instrumentation line of the
   installed SDK (0.62b1 with SDK 1.41.1).
7. **Kept, because they are plain OpenTelemetry:** the span helpers (`maybe_span`,
   `maybe_current_span`, `set_span_attributes`, `current_trace_ids`), the global
   `iris_harness.llm` tracer's `llm.invoke` spans, audit rows' trace ids, the tracer name
   `iris.runtime`, the OpenInference LangChain instrumentation (Apache-2.0; its attributes
   are ordinary span attributes), and the `openinference.project.name` resource
   attribute, now set to the service name (Phoenix files projects by it; every other
   backend ignores it).
8. **`arize-phoenix` stays on the licence deny list** as a guard, so it cannot come back
   as a dependency unnoticed. Running Phoenix as one's own backend is unaffected.

## Alternatives considered

- **Keep the `[phoenix]` extra and the embedded mode.** Rejected: it keeps the memory
  cost, the extra configuration language and the licence exception, for a viewer the
  user can run themselves in one command.
- **Keep `IRIS_PHOENIX_ENDPOINT` as an alias of `OTEL_EXPORTER_OTLP_ENDPOINT`.** Rejected:
  two names for one thing, and the standard name is what every backend's documentation
  already tells users to set.
- **Bundle a different viewer (Jaeger all-in-one) in Compose.** Rejected for the same
  reason as Phoenix: the backend is the user's choice, and the guide shows each in a few
  lines.

## Consequences

- Positive: a lighter install and API process; one configuration language that every
  OTLP backend documents; the core install has no source-available dependency to explain.
- Negative: `/healthz` and `/observability/llm-metrics` change shape (`phoenix_url` and
  `phoenix_enabled` become `otlp_endpoint` and `tracing_enabled`; the metrics backend
  `kind` is `otlp`). Neither is in the stable API. An existing `.env` with
  `IRIS_PHOENIX_*` no longer traces: set `OTEL_EXPORTER_OTLP_ENDPOINT` instead (for a
  local `phoenix serve`, `http://127.0.0.1:6006`). `start_iris.sh --no-phoenix` is gone,
  and a Phoenix it started earlier is no longer stopped by `--stop`.
- Privacy: trace content (LangChain spans include prompts and replies) goes wherever the
  operator points the endpoint. The guide says so. The variables are not in the settings
  catalog, so the app cannot change them.
- Reversibility: high. Re-adding a viewer is a Compose service or a guide section; the
  core needs no change to export to it.
