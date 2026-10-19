# ADR-0126: The public tree carries no owner and no deployment

Status: Accepted (owner, 2026-09-30). Built on `fix/oss-privacy-banks-and-deploy`.
Relates to: OSS plan R6 (install), R13 (identity), L4 (first public push); the export
tooling of #772 (`scripts/oss_export.sh`, `oss_public_slice.txt`, `oss_pii_scan.sh`).

## Context

Two things stood between the private archive and the first public push.

1. Shipped fixtures named the owner's real institutions (banks, card issuers, the
   broker, depositories), real sender addresses, amounts lifted from real statements, and
   people and places from the owner's life. Together they narrow the owner down. The
   identity scan had no rule for them, so a new fixture could bring them back.
2. `deploy/` (the owner's small-VM kit: server compose, LLM proxy, Tailscale and roll
   scripts, server config overrides) shipped whole because the core was built against it:
   `iris_api` loaded the proxy's settings catalog from `deploy/` by path, the host
   allow-list carried the server compose's service names, and core tests pinned
   `deploy/server/config/*` beside `config/`. Dropping `deploy/` failed ~25 core tests.

## Decision

1. **Synthetic data in every shipped file.** Institutions are invented, on reserved
   domains (Northwind Bank / `northwindbank.test`, Wingtip, Fabrikam, Woodgrove, Litware,
   Tailspin, Adatum Card); amounts are synthetic; personas and places are Petra Sutton,
   Remy, Springfield, Elmwood, Mo Harbor School. Each fixture keeps its meaning (a bank, a
   near-duplicate name pair, a sort order). The private finance domain keeps real names.
2. **`@private` in the identity scan.** A pattern's allowed column may say `@private`:
   a match is allowed only in a file the export never ships, as the export's own rules
   say (`scripts/oss_public_tests.py never-ships`: the deny list, the slice's private
   children, every `CLAUDE.md`, and a test or script `prune` would drop). There is no
   second, hand-kept list of private paths. In a tree without those scripts (the exported
   one) nothing is private, so the scan fails closed. The real institutions, the two
   statement amounts and the persona/place names are BLOCK patterns with `@private`.
3. **`deploy/` is private, except `deploy/searxng`.** `deploy` is a `partial` directory in
   `oss_public_slice.txt` with one kept child: the SearXNG kit is the public research
   engine's local search backend and belongs to no deployment of the owner's. The server
   kit's tests (`tests/unit/test_deploy`) are on the deny list; the public tests that sat
   there moved to `tests/unit/test_webui` and `tests/unit/test_container`.
4. **The core knows no deployment.**
   - `IRIS_SETTINGS_SIDECAR_CATALOGS` (core setting) lists the settings catalogs of
     processes a deployment runs beside the harness; each file names its `owner`. The app
     lists those settings and refuses to change them (409). The VM's compose names the
     proxy's catalog; the core never names a path.
   - `COMPOSE_SERVICE_HOSTS` is `docker-compose.yml`'s services plus the channel gateway;
     a deployment that calls a harness server by another name uses `IRIS_ALLOWED_HOSTS`.
   - Core tests pin `config/`; `tests/unit/test_deploy/test_server_config_pins.py` pins
     the server's copies to the same promises.

## Consequences

- The strict export passes with `deploy/` cut and BLOCK=0, and a new fixture that names
  a real institution or persona fails the commit hook unless it lives where nothing ships.
- The finance plugin's import route is `/portfolio/import-holdings` (it was named after
  a broker, and the core CLI and webui call it).
- On a machine without the env variable (the owner's Mac), the Settings screen no longer
  lists the proxy's settings; no proxy runs there.
- The public tree runs from an install or the root `Dockerfile` + `docker-compose.yml`;
  neither depends on `deploy/`.
