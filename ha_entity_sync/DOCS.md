# HA Entity Sync — Build 2.3

Version 0.2.3 remains strictly read-only.

## Automatic scan and session statistics\n\n- Opening the app automatically starts a fresh read-only scan. No manual scan button is needed.\n- Scan results and statistics are held only in the page session; they are cleared when the app is closed or navigated away from.\n- Reopening the app starts a new scan and displays fresh counts.\n\n## Long-list navigation\n\n- The entity search stays pinned at the top while scrolling long results.\n- Naming review is sorted alphabetically and grouped by first letter.\n- A right-side A–Z rail jumps directly to a letter when the naming list is long.\n- A floating Top control appears after scrolling down.\n\n## Scan scope (tightened)

The scanner now searches user-maintained Home Assistant configuration rather than recursively treating all application files as dependencies.

Included:
- Root Home Assistant YAML/Jinja configuration files.
- User configuration subfolders such as `packages/` (eligible YAML/Jinja files).
- Lovelace dashboards stored under `.storage/lovelace*`.
- Registered entity IDs referenced by those user-owned files, regardless of which integration originally created the entity.

Excluded:
- All other `.storage` internals, including entity registry, restore state, traces, config entries, and integration metadata. The entity registry is read separately only to build the registered-entity list.
- Home Assistant and add-on implementation folders such as `custom_components/`, `deps/`, `node_modules/`, AppDaemon, Pyscript, ESPHome, Zigbee2MQTT, and Node-RED.
- Backups and archives, including backup/archive/snapshot-named files or folders and archive/database/temporary file formats.
- Secrets, auth/token files, databases, logs, media, `www/`, and shared runtime data.

## Build 2 audit features

- Shows exact matching source lines and copyable `file:line` locations.
- Finds conservative potentially-stale entity IDs in explicit reference contexts. These are candidates, not confirmed broken references.
- Flags numeric suffixes such as `_2` and repeated words with suggestions only.

## Interpretation and limitations

- Integration-created entities remain in the registry and are counted when a user dashboard or configuration file references them. We exclude integration implementation files, not entities that users depend on.
- Home Assistant internal files and other apps' code are not considered user dependencies by default.
- A stale candidate may be historical, integration-managed, temporarily unavailable, or valid in a context the scanner cannot understand.
- Candidate detection uses common Home Assistant domains and explicit reference contexts; custom domains and unusual syntax may be missed.
- Backup files are excluded from both normal reference results and stale-candidate results.

## Safety

- The Home Assistant configuration mount remains read-only.
- No entity rename, configuration write, service call, backup/restore, or HA restart is implemented.
- POST requests remain disabled.

The persistent entity database, scan history/diff, confirmed broken-reference validation, impact previews, verified backup/rollback, and approved repair workflow remain future work. Any future modification must require a verified backup, explicit approval, and a usable rollback path.
