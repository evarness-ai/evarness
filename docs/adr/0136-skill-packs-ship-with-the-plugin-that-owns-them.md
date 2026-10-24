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

The skill registry also discovers `skills/**/manifest.yaml` under every installed plugin
directory (builtin, entry point, `$EVARNESS_HOME/plugins/<name>`), through the same
`installed_plugin_dirs()` seam. A plugin's skill packs are package data beside its
manifest.

Packs in `config/skills` are discovered first. The registry keeps the first pack of a
manifest `name`, so an owner's copy in `config/skills` overrides a plugin's; the displaced
pack is not loaded (its tools module is never imported) and the collision is logged once.
Between two plugins, the one `installed_plugin_dirs()` lists first wins, the same rule it
applies to plugin names.

`evarness skills list` labels such a pack `plugin:<name>` in its source column.

## Alternatives

- A manifest `skills:` key listing pack paths: more schema for the same outcome; the
  directory convention is what the ontology already uses.
- Copying packs into site-packages or the config tree at install time: mutates the
  core's files and breaks on upgrade.

## Consequences

- A brief manifest's references to a plugin's pack ids resolve as soon as the owning
  plugin is installed, with no config change.
- Nothing assumes a skill directory lives under the repo root: `evarness skills list`
  already falls back to the absolute path.
- Hot reload still watches only `config/skills`. A plugin's packs are installed package
  data and change with the package, so they are picked up on the next `discover()`, not
  on a file edit.
- A plugin's pack can run code (a `tools.py`), as the plugin itself can; installing a
  plugin was already the trust decision.
