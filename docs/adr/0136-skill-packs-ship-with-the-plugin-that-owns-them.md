# ADR-0136: Skill packs ship with the plugin that owns them

Status: Accepted (2026-10-24)
Relates to: `tools/skills/loader.py`, `tools/skills/registry.py`,
`foundation/plugin_dirs.py` (`installed_plugin_dirs`), `docs/guides/writing-a-skill.md`

## Context

`SkillRegistry.discover` scanned only `config/skills` under the registry's repo root
(`tools/skills/loader.py`, `resolve_skills_root`). A plugin installed from a
wheel could not ship a skill pack: its packs had to be copied into the core's config
tree, or the whole config tree duplicated under `EVARNESS_CONFIG_DIR`. The ontology, nav
and expiry subsystems already read `installed_plugin_dirs()` for plugin-shipped data, so
skills were the odd one out.

## Decision

A plugin's skill packs are package data beside its manifest, under `skills/`. The skill
registry loads `skills/**/manifest.yaml` from a plugin only when the plugin host has
**mounted** that plugin and it runs **in-process**. A pack's `tools.py` is code imported
into the harness, so the plugin host's mount and trust decisions apply to it: the profile,
`EVARNESS_PLUGINS_DISABLE`, a failed mount and unmet `requires` all keep a plugin's packs
out, and a `trust: mcp` plugin, which runs as its own server, never has pack code imported
here (brief packs included; one rule, no per-kind exceptions).

The registry never scans what is installed. It is handed a provider of plugin sources:
the runtime binds the plugin host's own records once the profile is mounted
(`runtime/plugin_host/skill_sources.py`, `mounted_skill_sources`); callers that boot no
runtime (`evarness skills list`, the REPL `/skills`, `evarness mcp serve`) pass
`profile_skill_sources`, which takes the resolved profile and applies the loader's own
pre-setup checks (found, `requires` met) and the effective trust. A plugin that would fail
inside `setup` is only known to a booted host. With no provider the registry loads no
plugin packs.

A pack of an installed plugin that is not loaded is still listed, from its manifest alone
and with the reason (`plugin not mounted`, `trust: mcp, packs run in-process`); none of its
code runs. Finding a plugin's directory runs none of it either: `installed_plugin_dirs` resolves an
entry point's dotted module from the files (the top-level name through `find_spec`, which
imports nothing, then directories), because `find_spec` on a dotted name imports the parent
packages and so executes their `__init__.py`. A plugin with no `manifest.yaml` (the host
synthesizes one) has no place to keep packs and contributes none.

Packs in `config/skills` are discovered first. Among plugin packs the registry keeps the
first pack of a manifest `name`, and a pack in `config/skills` overrides a plugin's: the
name is held from the moment the manifest reads, so an owner's copy that then fails to
load still keeps it, and the displaced pack's tools module is never imported. The
collision is logged once. Two packs of one name inside `config/skills` both load, as they
always did; the rule is scoped to plugin packs.

A pack's `requires.config_files`, when relative, resolves against the plugin directory the
pack ships in, not the checkout's `config/`: the config a plugin's pack needs is package
data of that plugin. Packs in `config/skills` keep the existing rule. `~` and absolute
paths are unchanged for both.

`evarness skills list [--json]` prints every discovered pack with its source
(`config/skills` or `plugin:<name>`), its kind, its `missing_prerequisites` and how many
tools it registered, so a check can assert that a pack loaded and not only that it was
found. The REPL's `/skills` names the same source.

## Alternatives

- A manifest `skills:` key listing pack paths: more schema for the same outcome; the
  directory convention is what the ontology already uses.
- Copying packs into site-packages or the config tree at install time: mutates the
  core's files and breaks on upgrade.

## Consequences

- A brief manifest's references to a plugin's pack ids resolve as soon as the owning
  plugin is mounted, with no config change.
- Nothing assumes a skill directory lives under the repo root: the listing falls back to
  the absolute path.
- A plugin pack cannot name a core `config/` file as a prerequisite by a bare relative
  path; it would be looked for in the plugin. A pack that needs a core file spells it
  with an absolute or `~` path.
- Hot reload still watches only `config/skills`. A plugin's packs are installed package
  data and change with the package, so they are picked up on the next `discover()`, not
  on a file edit.
- A plugin's pack runs code (a `tools.py`) in the harness process, exactly when the
  plugin itself does: mounting an in-process plugin was already the trust decision, and
  an unmounted or out-of-process plugin gets none.
