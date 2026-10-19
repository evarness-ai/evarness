# ADR-0117: Paired devices hold their own tokens; the shared secret is for services

Status: Accepted (2026-09-20)
Relates to: ADR-0108 (web approval channel), ADR-0069 (System Health — `GET /health`), ADR-0116 (health watch).
Implements: `docs/architecture/mobile-cloud-ui-plan.md`, Track 1 PR 2 (decision "per-device pairing tokens") and PR 3a (the pairing flow: API + CLI — see "Pairing flow (PR 3)" below).

## Context

Every client of `iris_api` authenticates with one value, `IRIS_AUTH_SECRET`
(`foundation/auth.py`, security-floor phase 0). The CLI reads it from the environment,
the Vite dev proxy injects it, and the services use it to call each other. That was
the right floor for one user on one machine. The mobile + cloud-native UI plan moves
the console to a cloud VM reached over the tailnet from a phone, and there the single
secret fails in three ways:

1. **It cannot be given to a browser.** A phone has no proxy to inject it, and a
   secret that every service shares must not sit in a page's JavaScript.
2. **It cannot be taken back from one client.** A lost phone is locked out only by
   rotating the secret for every service and every other client.
3. **It has one level.** A wall display that should only *show* the console holds the
   same authority as the owner's own phone.

While planning this, a hole in the existing floor turned up: `DEFAULT_EXEMPT_PATHS` is
`{"/healthz", "/health"}`. On the governor and the evaluator `/health` is a liveness
probe. On `iris_api` it is the full System Health snapshot (ADR-0069) — services,
connected accounts, hardware, open incidents — and it was served with no token.

## Decision

1. **A paired device holds its own token.** `kernel/governance/devices/` (store +
   service, SQLite at `<governance data dir>/devices.db`, beside `approvals.db`,
   `0600`). A device has an id, a name, a kind (`app` | `browser`), a scope
   (`read` | `control`), and created / last-seen / revoked timestamps. The token is
   `irisd_` + 32 random bytes (urlsafe base64), shown once at pairing and **stored only
   as its SHA-256**. A slow KDF is deliberately not used: it buys nothing against 256
   bits of entropy and would tax every request. The prefix makes a leaked token
   greppable and lets the verifier discard anything else without touching the DB.

2. **Pairing is a short-lived code.** `start_pairing(scope)` mints an 8-character code
   from an alphabet without look-alikes (`ABCD-EFGH`), stored hashed, valid for **5
   minutes**, **single use**. `claim(code, name, kind)` spends the code and creates the
   device in one transaction (the `UPDATE` is the lock: two racing claims, one
   winner). A wrong guess matches no row, so it cannot be charged to "its" code;
   instead **every live code is charged**, and a code that reaches **5** failed claims
   is void. Guessing while a code is live kills the code — the owner starts another.
   All refusals are one error (`PairingRefusedError`), so a guesser cannot tell a dead
   code from a wrong one. The HTTP routes, the global throttle and the CLI are the
   next PR; the rule "the capability lives in the service, the surfaces are thin" is
   the one `approvals/service.py` set.

3. **The policy stays pure and the secret stays a credential.**
   `foundation/auth.authorize()` is unchanged. Beside it, `resolve_principal()` answers
   *who* a request is: the shared secret → `service` (always `control`); a live device
   token → `device` with its scope; else 401. The device lookup is injected
   (`DeviceVerifier`), so the foundation still imports no storage.
   `server/auth.install_bearer_auth` takes an optional `device_verifier` and sets
   `request.state.principal`. **Only `iris_api` passes one.** The governor and the
   evaluator stay secret-only: nothing but another IRIS service calls them.
   - An unset secret is still **503 for everyone**, devices included. A service with
     no secret is misconfigured; a device token must not turn that into a working
     deployment.
   - A bearer header, when present, is the only credential considered. A bad one is
     refused rather than falling back to the cookie.
   - The secret is never accepted from the cookie slot.

4. **A browser carries its token in an `HttpOnly` cookie, `iris_device`**, so page
   script never sees it. The cookie is set at claim time (next PR) with
   `SameSite=Strict` and `Secure` behind HTTPS. Because a browser attaches a cookie on
   its own, **a cookie-authenticated `POST`/`PUT`/`PATCH`/`DELETE` must carry an
   `Origin` whose host:port equals the request's `Host`** — missing, `null` or foreign
   is 403. Only host and port are compared: behind `tailscale serve` the browser speaks
   https and the app sees http. Bearer-authenticated requests skip the check (a browser
   never attaches that header by itself), and so do cookie reads (a cross-site `GET`
   cannot read its response). This applies to the ungated write surface too — chat from
   a hostile page is still CSRF.

5. **Scope is enforced by the write guard that already exists.** `iris_api`'s
   deny-by-default guard (`_is_gated_write`) refuses a gated write from a `read`
   device with 403 "this device is paired read-only". A `read` device keeps the
   ungated surface the guard has always left open — chat, RAG upload/search,
   feedback. Chat acts only through the governance kernel, and answering an approval
   is a gated write, so a `read` device can ask but cannot approve.

   **Amended 2026-09-20 (see the note below).** This decision first kept
   `IRIS_WEBUI_ALLOW_WRITES` as a global kill switch *above* the scope check, so a
   `control` device was refused when the switch was off. That was wrong for the
   deployment this ADR was written for.

6. **Pair, revoke and code-voiding go to the governance ledger** (`AuditLog`,
   `agent_type="devices"`, `hook_point="device_pairing"`, `run_id="device:<id>"`), with
   who asked. Neither a token nor a code is ever written to the ledger or a log.
   Revoking twice writes one row. `last_seen` moves at most once a minute, so a
   polling console is not a write per request.

7. **`iris_api` exempts `/healthz` only.** `/health` takes a token like every other
   data route. `DEFAULT_EXEMPT_PATHS` is unchanged for the governor and the evaluator,
   whose `/health` *is* a probe. `iris status` — which, contrary to the plan's note,
   sent no header — now sends the secret. Nothing else called `/health` bare: the
   compose healthcheck and the health service's own probes use `/healthz`.

## Pairing flow (PR 3)

Amended 2026-09-20 with Track 1 PR 3a. Decisions 1-7 are the mechanism; this is the
flow over it: five routes on `iris_api` (`server/iris_api/device_routes.py`) and the
CLI `iris device pair | list | revoke` (`cli/device.py`). Both are thin. The CLI calls
the routes with the service secret and never opens `devices.db` — which is also how
the **first** device is paired: from the host's shell, where the secret already is.

| Route | Who | Answer |
|---|---|---|
| `POST /api/v1/devices/pair/start` | service, or a `control` device | `{code, scope, expires_at}`; a `read` device gets 403 |
| `POST /api/v1/devices/pair/claim` | **no credential** — the code is the credential | `app`: `{device, token}`; `browser`: `{device}` + the cookie |
| `GET /api/v1/devices` | any principal | every device, newest first, revoked included, `current` on the caller's row |
| `GET /api/v1/devices/me` | any principal | `{kind, scope, via, device}` |
| `DELETE /api/v1/devices/{id}` | service or `control`: any device. **Any device: itself** | `{device}` with `revoked_at`; idempotent; unknown id 404 |

### Amendment, 2026-09-20 — a paired device is the authentication the switch stood in for

`IRIS_WEBUI_ALLOW_WRITES` predates this ADR. Its purpose, in its own words in
`iris_api/main.py`, was to hold control mutations shut "until real authentication
lands", because the server could not tell who was asking. This ADR is that
authentication.

Keeping the switch above the scope check had a cost we only saw on the first cloud
deployment: the console there is reachable *only* by a paired device over the tailnet,
so the owner's own phone, paired at `control`, was read-only until an env var was set
on the VM by hand. The switch was not adding a decision — it was asking the operator to
say twice what pairing already said once.

The guard now reads:

| caller | may perform a gated write |
|---|---|
| paired `control` device | yes — the switch does not apply |
| paired `read` device | no, however the switch is set |
| shared secret (`IRIS_AUTH_SECRET`) | only when the switch is on |
| no credential | rejected earlier, by the bearer check |

The shared secret keeps the switch because it names a *process* — the CLI, the Vite
dev proxy, service-to-service — and never a person, so it is not the authentication
the gate was waiting for. A laptop running `start_iris.sh` therefore behaves exactly
as before.

`/capabilities.writes_enabled` now answers for the calling principal rather than for
the process, because the console hides its write controls from that field; reporting
the process-wide switch made a `control` device render itself read-only.

8. **Device administration is outside the console write gate.** `IRIS_WEBUI_ALLOW_WRITES`
   defaults to off, and decision 5 put every gated write behind it and behind the
   blanket `read`-device refusal. Pairing and revoking are not console writes; they are
   *authentication administration*. Behind the switch, a default install could never
   pair its first device, and a lost phone could not be revoked from a read-only
   console — the two things this ADR exists for. So exactly three requests are exempt
   from both (`is_device_admin_write`: `POST …/pair/start`, `POST …/pair/claim`,
   `DELETE …/devices/{id}`), and **scope is enforced in the route** as the table says.
   The match is exact, not a prefix: the guard is deny-by-default so that a new
   mutating route is gated without anyone remembering to, and a prefix would quietly
   exempt whatever is added under `/api/v1/devices/` next. The CSRF rule of decision 4
   is in the auth middleware, not the write guard, so it still applies: a
   cookie-authenticated `pair/start` or `DELETE` needs `Origin` == `Host`.

9. **Any device may revoke itself**, a `read` one included. Signing out lowers
   authority; it needs none. A `read` device revoking *another* is 403, and the scope
   check runs before the lookup, so a `read` device cannot probe which ids exist.
   When the caller authenticated by cookie and revoked itself, the response also
   clears the cookie (`Max-Age=0`, same attributes).

10. **The claim path is the one data route with no credential**, so it is the one a
    guesser can hammer. It is added to the bearer-exempt set on `iris_api` only (the
    governor and the evaluator keep theirs). Two limits apply, both in `DeviceService`
    so every surface gets them:
    - per code, from decision 2: 5 failed claims void every live code;
    - **globally** (`devices/throttle.py`): failed claims from all callers are counted
      in a sliding window, **10 failures / 60 s**. While the window is full every claim
      is refused with `PairingThrottledError` → **429 + `Retry-After`**, *before* the
      code is looked at: a throttled claim is no oracle (the right code gets 429 too)
      and charges no attempt to the owner's live code. One ledger row per trip
      (`pairing_throttled`, payload `{limit, window_seconds}` — no code, no address).
    The window is global rather than per address because behind `tailscale serve`
    every request arrives from the proxy; it is in memory because `iris_api` is one
    process holding one `DeviceService` (`LazyDeviceService`, shared by the auth
    verifier and the routes). A restart forgets the window and gives a guesser
    nothing: the per-code count is in the DB. The cost is that a flood can lock the
    owner out of pairing for up to a minute at a time; nothing already paired is
    affected. Any code that does not pair — wrong, expired, used, voided, empty,
    malformed — gets one answer: 400 `pairing code not accepted`.

11. **How the token reaches each kind.** An `app` gets it once in the response body and
    keeps it in its own secure storage. A `browser` never sees it: the body carries the
    device only, and the token rides in `Set-Cookie: iris_device=…; HttpOnly;
    SameSite=Strict; Path=/; Max-Age=34560000` (400 days — the ceiling browsers apply
    anyway; the token itself does not expire, revocation ends it). **`Secure` is added
    when the request arrived over HTTPS**: the URL scheme is `https`, or the first
    `X-Forwarded-Proto` value is — which is what `tailscale serve` sends while the app
    itself sees http. That header is not authenticated, and does not need to be: it
    can only *add* `Secure`, so a spoofed value makes the cookie stricter, never
    looser. Over plain http (a dev machine) `Secure` is left off, because a browser
    would refuse to store the cookie at all.

No list or `me` response carries a token or a hash: the payload is built field by
field from `DeviceRow`, which has neither.

## Alternatives considered

- **Hand the shared secret to the browser** (what the Vite proxy effectively does in
  dev). Rejected: fails all three points in Context.
- **Signed, self-contained tokens (JWT).** Rejected: revocation is the requirement,
  and a stateless token needs a server-side denylist to be revocable — the same lookup
  this design does, plus a signing key to guard and an expiry/refresh flow for one user.
- **bcrypt/argon2 for token hashes.** Rejected: see decision 1. It *would* be right for
  the pairing code, which is low-entropy — but the code lives five minutes, dies on
  five guesses, and sits in a `0600` file next to data far more valuable than a spent
  code. Accepted risk, written down here so nobody "fixes" the token hash instead.
- **Per-code attempt counting by splitting the code into id + secret.** Rejected: it
  lengthens what the owner types to defend against a guesser who is already on the
  tailnet, and charging all live codes is fail-closed without it.
- **Keep device routes behind `IRIS_WEBUI_ALLOW_WRITES`.** Rejected: see decision 8. It
  would also have made the switch do two unrelated jobs — "may the console edit" and
  "may anyone ever log in".
- **Per-address claim throttling.** Rejected: one address behind the tailnet proxy, and
  a global window cannot be sidestepped by rotating addresses.
- **Persist the throttle window in SQLite.** Rejected for now: one process, and the
  per-code void already survives a restart. Worth revisiting only if `iris_api` ever
  runs as more than one worker.
- **A new authorisation layer for scopes.** Rejected: the write guard already is the
  deny-by-default list of what mutates state; a second list would drift from it.
- **`read` devices cannot chat.** Considered, not taken: the plan scopes `read` to the
  existing gated-write line, and chat is governed at the kernel. If a display-only
  device should not chat, that is a third scope, not a change to this one.
- **Leave `/health` open.** Rejected: it lists the owner's connected accounts and
  their token state to anyone who can reach the port.

## Consequences

**Positive:** a lost phone is one revoke, not a secret rotation; the console can tell
clients apart (`request.state.principal`), which the Devices screen, per-device audit
and later step-up checks (Face ID for high-risk switches) all build on; a display can
be paired `read`; `/health` no longer leaks; the secret never has to reach a browser.

**Negative:** a second credential type on `iris_api` and a second store to back up
(`devices.db` — losing it un-pairs every device, which is recoverable by pairing
again); each device-authenticated request costs one indexed SQLite read, taken off the
event loop; anything that polled `iris_api`'s `/health` without a token now gets 401
(none found in `scripts/`, `deploy/` or `src/`; external monitors should use
`/healthz`). With the pairing flow (PR 3a) one unauthenticated data route exists on
`iris_api`; its limits are decision 10.

**Reversible:** drop the `device_verifier` argument in `iris_api` and the service is
secret-only again; `devices.db` can be deleted at any time. The `/health` change is
independent of both.

## Related ADRs

ADR-0108 (approvals answered from the web — the write a `read` device must not make),
ADR-0069 (`GET /health`, now authenticated), ADR-0116 (its probes use `/healthz` and
are unaffected).
