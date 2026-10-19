# ADR-0120: Settings the app changes live in one store on the data volume

Status: Accepted (2026-09-23). The owner decided the rules below and signed off a
clickable prototype on the same day. PR 1 of the track implements the store and
heartbeats; the rest is planned in `docs/architecture/editable-settings-plan.md`.
Relates to: ADR-0117 (paired devices hold their own tokens), ADR-0074 (agent settings),
ADR-0083 (hot-toggled learning controls).

## Context

The owner asked to change every setting from the app, starting with heartbeat
schedules. Everything the app showed was read-only:

- Heartbeat schedules come from `heartbeats.yaml`. On the cloud VM the deploy mounts it
  **read-only** from the repo checkout (`deploy/server/config/heartbeats.yaml`), and the
  next deploy replaces it anyway.
- Feature flags come from `/srv/iris/env/server.env`, read when the process starts.
- The scheduler dropped a disabled heartbeat entirely, so the app could not even list
  the four Mac-only jobs the VM turns off, let alone turn one back on.

Two older write paths already existed, and neither survives a deploy on the VM:

- `PATCH /agents/{name}/settings` writes `config/agent_settings.local.yaml`, which on
  the VM is inside the container, not on a volume.
- The learning controls (ADR-0083) hot-toggle in memory; a restart reverts them.

Every database on the VM is already on a host volume under `/srv/iris` and in the
nightly encrypted backup to Blob (checked with `docker diff` on 2026-09-23).

## Decision

1. **One settings store, on the data volume.** `foundation/settings` keeps a SQLite file,
   `settings.db`, in the data dir (`/srv/iris/data` on the VM). It holds *overrides*
   keyed by `(section, key)` and a *history* of every change with who made it and the
   value before and after. The nightly backup copies it with no change. The store sits
   in `foundation` because flag readers, the lowest layer, will read it.

2. **Files stay the defaults.** An override lays over the shipped value; it never writes
   the YAML or the env file. Reset drops the override. An override stores only the
   fields that differ from the default, so a later deploy that changes a field the owner
   never touched still takes effect.

3. **Each owner validates and applies its own section.** The store knows nothing about
   what a value means. The heartbeat scheduler validates a schedule (minimum 30 s,
   5-field cron), refuses to turn on a heartbeat this harness has no handler for,
   reschedules the job at once, and reads overrides when a heartbeat registers, so an
   edit survives a restart and a fresh container.

4. **An API for every edit; the app and the REPL are surfaces.** `PATCH /heartbeat/{name}`,
   `DELETE /heartbeat/{name}/override`, `GET /settings/history`, and `/heartbeats set |
   reset` in the REPL. The existing deny-by-default write guard decides who may call
   them: a paired `control` device may, a `read` device may not, the shared secret only
   when the operator opted in. No route adds a second policy.

5. **Guarded settings need a confirm, enforced by the API.** Settings that weaken a
   guard, raise a spend limit, or let private data reach a cloud model are editable, but
   only with an explicit confirm the API checks, not only the UI. This applies from the
   PR that first makes such a setting editable.

6. **Secrets are never shown or edited from the app.** API keys, tokens and passwords
   stay in the env file.

## Alternatives considered

- **Write the YAML files.** Refused: they are read-only mounts on the VM, and a deploy
  replaces them. A change would either fail or be lost.
- **Mount the config directory writable.** Refused: the deploy owns those files, so a
  deploy would still overwrite the owner's changes, and the store would have no history.
- **A separate Azure data disk for `/srv/iris`.** Refused by the owner for now: the
  nightly backup is enough, at no extra cost.

## Consequences

- Positive: changes survive restarts and deploys; every change is attributed and can be
  undone; the app can see and turn on disabled heartbeats.
- Negative: the effective value of a setting is now "file default plus override", so
  support has to look in two places. `GET /heartbeat` reports both (`schedule` and
  `default_schedule`, plus `overridden`) so nobody has to guess.
- Reversible: deleting `settings.db` returns every setting to its shipped value.
