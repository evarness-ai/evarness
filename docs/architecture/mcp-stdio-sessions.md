# MCP stdio sessions: keep a server alive across calls? (issue #299)

Status: proposed. Recommendation: **not now**. Keep the one-shot process as the only mode;
the design below is what to build if the trigger at the end is met. Split from #298; follows
#112.

## The question

Every stdio MCP call starts the server, runs the `initialize` handshake, makes one request
and terminates the process (`_run_stdio_exchange` in `src/evarness/tools/mcp_bridge.py`).
Should a server instead stay alive between calls?

## What a call costs (measured)

Apple M4 Max, Python 3.12, warm file cache, 30 calls each, median of one full
`MCPTransport.invoke` (spawn, handshake, `tools/call`, terminate), signing off. The
interpreter floor is `python -c pass`.

| | median | p95 |
|---|---|---|
| bare interpreter start | 18 ms | 19 ms |
| stdlib-only server, one call | 17 ms | 18 ms |
| `import mcp.server.mcpserver` alone | 285 ms | 314 ms |
| server on the official `mcp` SDK 2.3 (`MCPServer`), one call | 262 ms | 282 ms |

Two readings. The exchange itself costs nothing visible: the stdlib server's whole call is
the interpreter start. The cost is the server's own imports, and it is the server author's
choice: a server that does not import the SDK pays 17 ms, one that does pays about 260 ms.
Node servers started through `npx` are not measured here (they add a package-resolution
step; unmeasured, not assumed).

For scale, one model step on this machine's local Ollama, 60 output tokens, a 440-token
prompt, warm: `granite4.2:3b` 520 ms, `qwen2.5:7b-instruct` 851 ms. So, as a share of a step's time (model plus tool call), an SDK-based call is about a third
of a short 3B step (262 of 782 ms) and about a quarter of a 7B step (262 of 1113 ms); a
stdlib-based call is about 3% (17 of 537 ms). These are lower bounds on the step: Evarness's real prompts
carry the tool list and history, so a real step is longer and the share is smaller. One
machine, one prompt, one run of each; treat the shares as an order of magnitude.

## What the one-shot process gives for free

These hold today because nothing survives a call, and each is weakened by a kept session:

- **The code that runs is the code that was checked.** `check_signature` rebuilds the spec
  (`build_server_spec`, which hashes the script or the local `-m` package) and verifies it
  before every launch. A live process keeps running the code it started with.
- **The environment is rebuilt each launch** (`child_environment`): an allow-listed base,
  `inherit_env`, the entry's own `env:`. A session keeps the one it started with.
- **No state between calls.** A server cannot carry something it derived from an earlier
  result (including an injected one) into a later call.
- **Failure is contained.** A hung or crashed call ends with its process; the next call is
  clean.

One thing a session does *not* worsen: **trust-store revocation.** The transport loads the
trust store once, at construction (`MCPTransport._trust_store`; `_load_trust_store` is called
from `MCPBridge.__init__` and `MCPTransport.from_repo_root`), and the plugin host builds a
transport when it mounts a plugin. So `evarness mcp revoke` already takes effect on a running
process only when its transport is rebuilt. Sessions would need the same, no worse.

## Decision

Do not build sessions now. The measured cost is small for a server that avoids heavy
imports, a visible but not dominant share of a step for one that does not, and the saving
comes at the price of every guarantee above plus a lifecycle the transport does not have:
there is no hook that closes it (`runtime/facade.py` `shutdown` stops the poller and
heartbeats; nothing in the plugin host or the transport closes anything), and the test
harness would need to close sessions after each test or leak processes.

## If it is built: answers to the six questions

1. **Scope.** One session per server entry, per transport instance, ended by an idle
   timeout (default 60 s, in `config/`) and by shutdown. Not per turn: the transport does
   not know turns, and adding that plumbing would couple it to the loop.
2. **Where it lives.** Inside `MCPTransport`, behind the existing `invoke` signature, so
   `MCPBridge` and the `trust: mcp` host change nothing. A `StdioSession` class holds the
   process, the stdout buffer (the one from #289, now per session) and the stderr tail.
   `MCPTransport.close()` ends them; the runtime's `shutdown()` and the test harness's
   teardown call it. The one-shot path stays as it is.
3. **Re-verification.** On every call, rebuild the spec and verify the signature exactly as
   today (a file read and a SHA-256, milliseconds). If the verdict is no longer trusted, or
   the spec hash differs from the one the session started under, end the session and
   continue under the policy as a fresh launch would. Revocation propagates when the
   transport is rebuilt, as today.
4. **Failures.** A call that times out, or whose process has died, ends the session. The
   call is not retried: a `tools/call` may be a write, and a retry could repeat it. The
   error is the transport error from #289; the next call starts a fresh session. Request
   ids are unique per session; unsolicited output between calls is dropped with the
   existing noise handling.
5. **Concurrency.** One request in flight per session (a lock); sessions of different
   servers run in parallel. A second caller waits within its own deadline.
6. **Opt-in.** `session: reuse` on a server entry and on the plugin `server:` block, default
   `one_shot`. Changing it is a manifest change; the field is not signed (it changes
   latency, not what runs), but the code it runs is still verified per call as in 3.

## Trigger to revisit

Build it when a real deployment shows MCP spawn time as a material share of turn latency:
for example a server whose per-call spawn is above a quarter of the model's step time over
a representative turn, or a turn that makes many calls to one SDK-based server. Re-run the
measurement above (it is a stdlib script around `MCPTransport.invoke`) before deciding. The
cheaper first move is for the server to avoid heavy imports; that costs this repository
nothing.

## Acceptance for the implementation, if built

A revoked key or an edited script ends the session; the environment is the allow-listed
one; a hung call ends at its deadline and the next call gets a fresh server; the number of
server starts drops from one per call to one per session; closing the runtime and the test
harness leaves no server process behind.
