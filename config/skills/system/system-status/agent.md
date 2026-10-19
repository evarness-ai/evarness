# system-status skill (System agent S0)

One tool, **`system_status`**, over `evarness.services.system`: a deterministic snapshot of

- **Host** — RAM free/total, CPU %, thermal throttling (reusing the same
  pressure sampler the LLM tier governor uses).
- **Evarness itself** — connected accounts by provider (gmail / gcalendar /
  gdrive…), skill count, enabled heartbeats, per-DB sizes, FileManager roots.

Read-only and composes signals Evarness already has — no LLM, no OS mutation. The
*conversational* system surface (time/date, weather, identity, general chat,
web/wiki/memory search) is the system ReAct handler, not this skill.

Also available as `evarness status` on the CLI. OS-level control (notifications,
launching apps, process management) is intentionally out of scope here.
