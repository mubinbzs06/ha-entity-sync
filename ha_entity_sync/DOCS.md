# HA Entity Sync — Build 2.7

Version 0.2.6 remains strictly read-only.

## File navigation

- Each reference includes an **Open file** action instead of a copy-location action.
- The in-app viewer opens the referenced Home Assistant configuration file and scrolls to the exact matching line.
- The viewer is read-only. It does not open an editor or modify files.
- **Back to entity** returns to the exact expanded entity or candidate whose dependency was opened.
- The file endpoint only serves files inside the scanner's existing user-configuration scope. It rejects absolute paths, traversal paths, files outside the mounted configuration, and files not eligible for scanning.

## Automatic scan and session statistics

- Opening the app automatically starts a fresh read-only scan. No manual scan button is needed.
- Scan results and statistics are held only in the page session; they are cleared when the app is closed or navigated away from.
- Reopening the app starts a new scan and displays fresh counts.

## Long-list navigation

- The entity search stays pinned at the top while scrolling long results.
- Naming review is sorted alphabetically and grouped by first letter.
- A right-side letter rail jumps directly to a letter.
- A floating Top control returns to the overview.

## Scan scope

Included:
- Root Home Assistant YAML/Jinja configuration files.
- User configuration subfolders such as `packages/` (eligible YAML/Jinja files).
- Lovelace dashboards stored under `.storage/lovelace*`.
- Registered entity IDs referenced by those user-owned files, regardless of which integration created the entity.

Excluded:
- All other `.storage` internals, including entity registry, restore state, traces, config entries, and integration metadata. The entity registry is read separately only to build the registered-entity list.
- Home Assistant and add-on implementation folders such as `custom_components/`, `deps/`, `node_modules/`, AppDaemon, Pyscript, ESPHome, Zigbee2MQTT, and Node-RED.
- Backups and archives, including backup/archive/snapshot-named files or folders and archive/database/temporary file formats.
- Secrets, auth/token files, databases, logs, media, `www/`, and shared runtime data.

## Audit features and limitations

- Exact matching source-line snippets and navigable file locations.
- Potentially stale entity IDs are candidates for human review, not confirmed broken references.
- Numeric suffixes and repeated words are naming suggestions only.
- Integration-created entities remain tracked in the registry and are included when user-owned dashboards/configuration reference them.
- Custom domains and unusual reference syntax may be missed.

## Safety

- The Home Assistant configuration mount remains read-only.
- No entity rename, configuration write, service call, backup/restore, or HA restart is implemented.
- POST requests remain disabled.

The persistent entity database, scan history/diff, confirmed broken-reference validation, impact previews, verified backup/rollback, and approved repair workflow remain future work. Any future modification must require a verified backup, explicit approval, and a usable rollback path.


## Historical reference audit (Build 2.7)

- The scanner includes only two additional `.storage` files: `.storage/core.restore_state` and `.storage/trace.saved_traces`.
- Historical hits are categorized separately from active configuration and dashboard dependencies.
- Unregistered entity-like IDs in those historical files are shown as historical candidates, not confirmed broken dependencies.
- All other `.storage` internals remain excluded. The existing file-size limit, backup exclusions, read-only mount, and file-viewer scope checks remain in force.
- Naming review is informational: numeric suffixes and repeated words are observations only. The app does not generate replacement IDs or imply that these patterns should be changed.
