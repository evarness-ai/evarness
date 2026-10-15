# Run with Docker

Docker Compose runs IRIS as a set of services that stay up: the IRIS API (with the web
console and the scheduled email jobs), the Governor, and Ollama. It builds the image
from a clone of the repository.

```bash
git clone https://github.com/evarness-ai/iris-harness.git
cd iris-harness
echo "IRIS_AUTH_SECRET=$(openssl rand -hex 32)" >> .env   # once; .env is git-ignored
docker compose up        # or: make up
```

Put the secret in `.env` rather than `export`ing it. An export lasts for one shell, and
Compose needs the variable for **every** `docker compose` command, not only `up` (see
below).

| Service | Port | Role |
|---|---|---|
| `ollama` | 11434 | The local model runtime. |
| `ollama-init` | | One shot: pulls the tier models, then exits. |
| `governor` | 8080 | The HTTP front of the governance kernel: policy, rate limits, audit. |
| `iris-api` | 8003 | Chat, streaming chat, and the web console. |

Every port binds to `127.0.0.1`. `iris-api` waits for the Governor to be healthy and for
the models to finish pulling, so the first start is slow (about 6 GB of model weights)
and later ones are fast.

## The one required variable

Compose refuses to load `docker-compose.yml` without `IRIS_AUTH_SECRET`. That applies to
every `docker compose` subcommand, including `ps`, `logs`, `exec` and `down`, which start
nothing. In a new terminal where the variable is not set they stop with:

```text
error while interpolating services.governor.environment.IRIS_AUTH_SECRET: required variable IRIS_AUTH_SECRET is missing a value
```

The stack is not broken; Compose cannot read the file. Keep the secret in `.env` (Compose
reads it from there for every command) or export the same value again. Use the same value
as the one the running containers started with: the API authenticates calls against it, so
a different one is a different login. `make down` and `make logs` set a placeholder
themselves, since they never use the value. The requirement is deliberate: a shipped
default would be a publicly known secret.

Every API call except the `/healthz` probe must carry it as a bearer token. A secret in
`.env` is read by Compose, not exported to your shell, so load it for the call:

```bash
export IRIS_AUTH_SECRET="$(grep '^IRIS_AUTH_SECRET=' .env | cut -d= -f2-)"
curl -s localhost:8003/chat -H "authorization: Bearer $IRIS_AUTH_SECRET" \
  -H 'content-type: application/json' \
  -d '{"message": "what time is it?", "session_id": "demo"}'
```

Everything else defaults to local-only: the model endpoints point at the bundled
Ollama. A `.env` beside `docker-compose.yml` (see `.env.example`) is where credentials
and overrides go; it is loaded when present.

No trace UI runs in the stack. To see OpenTelemetry traces, run a backend of your own and
set `OTEL_EXPORTER_OTLP_ENDPOINT` in `.env` (from a container, the host is
`host.docker.internal`); see [Send traces to your backend](tracing.md).

## On a Mac: Metal-accelerated models

Ollama in a container runs on the CPU. For Metal speed, run Ollama natively
(`ollama serve`, with the tier models pulled) and start IRIS against it:

```bash
make up-host
```

## Lifecycle

```bash
make logs                # follow iris-api and the Governor
make down                # stop and remove the stack; the volumes stay
docker compose down -v   # also drop the data and model volumes
```

## The web console

Open `http://127.0.0.1:8003/` in a browser. The page is served without a token, but
only to a browser navigation (it sends `Sec-Fetch-Mode: navigate`); a bare `curl` of `/`
gets a 401, as does any data call without the bearer token.

## Models

`ollama-init` pulls the models `config/llm_tiers.yaml` routes to. To change them, edit
both that file and the `ollama-init` command in `docker-compose.yml`, or pull by hand:

```bash
docker compose exec ollama ollama pull <model>
```
