# Evarness — developer convenience targets.
#
# The experiment* targets drive the PRIVATE iris-evals behavioural harness
# (Evarness-as-SUT, exp-006) against a live Evarness, end to end, from a cold start.
# Full runbook: ../agent-workbench/iris-evals/RUNNING.md
#
#   make experiment         cold start, fully dockerized (Evarness + containerized Ollama)
#   make experiment-host    Evarness in docker -> your native Metal Ollama (faithful tiers)
#   make experiment-native  harness only, against an Evarness you already started
#
# ! macOS: `experiment` and `experiment-host` build the evarness:latest Linux image,
#   whose Dockerfile compiles macOS-native deps (pyobjc/pyaudio) that cannot build
#   in the Linux container, so both fail at image build (iris-evals/PREREQUISITES.md
#   gap #7). On a Mac, start Evarness natively and use `make experiment-native`.
#
# Override any of these on the command line, e.g.:
#   make experiment MODELS="llama3.2:3b" EVARNESS_EVALS=/path/to/iris-evals

COMPOSE      ?= docker compose
EVARNESS_API_URL ?= http://localhost:8003
EVARNESS_EVALS   ?= ../agent-workbench/iris-evals
# Models pulled into the containerized Ollama; keep in sync with config/llm_tiers.yaml
# (router=llama3.2:3b, tier1=granite4:latest, tier2=qwen2.5:7b-instruct).
MODELS       ?= llama3.2:3b granite4:latest qwen2.5:7b-instruct

.PHONY: help up up-host up-local down logs wait pull-models run-harness \
        experiment experiment-host experiment-native

help:
	@echo "Evarness make targets:"
	@echo "  make up                 one-command local stack: build + Ollama + models + Governor + Evarness + Phoenix"
	@echo "  make down | logs"
	@echo "  make experiment         cold start (all-docker): Evarness + Ollama up, pull models, run harness"
	@echo "  make experiment-host    Evarness in docker -> native Metal Ollama, then run harness (faithful)"
	@echo "  make experiment-native  harness only, against an already-running Evarness  [use this on macOS]"
	@echo "  NOTE: experiment / experiment-host fail to build on macOS (native deps); see iris-evals/PREREQUISITES.md gap #7"
	@echo "  make up-local | up-host | down | logs | wait | pull-models | run-harness"
	@echo "  vars: EVARNESS_API_URL=$(EVARNESS_API_URL)  EVARNESS_EVALS=$(EVARNESS_EVALS)  MODELS='$(MODELS)'"

# --- one command: full local stack (Ollama + models + Governor + Evarness + Phoenix) ---
up:
	$(COMPOSE) up -d --build
	@$(MAKE) wait
	@echo ""
	@echo "  Evarness API:   $(EVARNESS_API_URL)   (POST /chat)"
	@echo "  Phoenix UI: http://localhost:6006"
	@echo "  Governor:   http://localhost:8080"

# --- bring Evarness up (pick one) ---
# Everything in docker, including Ollama (portable; CPU-only on a Mac).
up-local:
	$(COMPOSE) up -d

# Evarness in docker, talking to the host's native (Metal) Ollama. --no-deps so the
# containerized Ollama + model puller aren't dragged in by evarness-api's depends_on.
up-host:
	$(COMPOSE) -f docker-compose.yml -f docker-compose.hostllm.yml up -d --no-deps governor evarness-api

# docker-compose.yml refuses to load without EVARNESS_AUTH_SECRET, even for commands that start
# nothing. down and logs never use the value, so a placeholder lets them run in a shell
# that has lost the export; a real value in the environment or .env is kept.
down logs: export EVARNESS_AUTH_SECRET ?= unused

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100 evarness-api governor

# --- helpers ---
# Block until Evarness reports its runtime is built (cold model load can take a couple
# minutes). /healthz is the open probe; /health is data and needs a token (ADR-0117).
wait:
	@echo "waiting for Evarness at $(EVARNESS_API_URL)/healthz ..."
	@for i in $$(seq 1 60); do \
	  curl -sf $(EVARNESS_API_URL)/healthz 2>/dev/null | grep -q '"runtime_ready": *true' && { echo "  Evarness up"; exit 0; }; \
	  sleep 3; \
	done; echo "  timed out; check 'make logs'"; exit 1

# Pull tier models into the containerized Ollama (only needed for 'up-local').
pull-models:
	@for m in $(MODELS); do echo "pulling $$m ..."; $(COMPOSE) exec -T ollama ollama pull $$m; done

# Run probe + all four batteries; proofs land in $(EVARNESS_EVALS)/docs/.../results/.
run-harness:
	EVARNESS_API_URL=$(EVARNESS_API_URL) $(EVARNESS_EVALS)/scripts/run-experiment.sh

# --- one-command experiments (cold start) ---
experiment: up-local wait pull-models run-harness
	@echo "done — proofs in $(EVARNESS_EVALS)/docs/exp-006-evarness-agent-e2e-behavioural/results/"

experiment-host: up-host wait run-harness
	@echo "done — proofs in $(EVARNESS_EVALS)/docs/exp-006-evarness-agent-e2e-behavioural/results/"

experiment-native: wait run-harness
	@echo "done — proofs in $(EVARNESS_EVALS)/docs/exp-006-evarness-agent-e2e-behavioural/results/"
