# HA Entity Sync

A local Home Assistant OS app for **read-only entity reference auditing**.

## Current development build

Version **0.2.5** scans user-maintained Home Assistant configuration and Lovelace dashboards. It excludes Home Assistant internals, integration/add-on implementation folders, backups, archives, databases, logs, secrets, and runtime data.

Entities created by integrations are still tracked in the entity registry and appear when your own dashboards or configuration reference them.

## Navigation and audit

- The app automatically scans on open; results and statistics are session-only.
- Entity dependencies show exact source lines and an **Open file** action.
- The in-app file viewer opens the file at the matching line in read-only mode.
- **Back to entity** returns to the exact expanded entity/candidate.
- Naming review is alphabetically grouped with a right-side letter index.
- A floating Top button returns to the overview.
- Potentially stale IDs are review candidates, not confirmed broken references.
- Naming suggestions never rename entities.

## Scan scope

Included:
- Root YAML/Jinja configuration.
- User configuration folders such as `packages/`.
- Lovelace dashboards under `.storage/lovelace*`.

Excluded:
- All other `.storage` internals.
- `custom_components`, add-on/application code, caches, and runtime folders.
- Backup/archive/snapshot files and folders.
- Secrets, auth/token files, databases, logs, media, `www`, and shared runtime data.

## Install (development)

1. In Home Assistant, open **Settings → Apps → App Store → ⋮ → Repositories** (older releases call these Add-ons).
2. Add: https://github.com/mubinbzs06/ha-entity-sync
3. Find **HA Entity Sync**, install, and start it.
4. Open it from the Home Assistant sidebar.

The app mounts Home Assistant configuration at `/homeassistant` **read-only**. It does not rename entities, edit configuration, call services, or restart Home Assistant.

## Limitations and safety

- A missing-registry ID is only a candidate, not necessarily broken.
- Custom domains and unusual reference syntax may be missed.
- Dependencies held only in integrations, databases, or remote systems are outside this file scan.
- The viewer only serves files already included in the scanner's allowed user-configuration scope.
- Persistent entity database, scan history, confirmed broken-reference validation, impact previews, verified backup/rollback, and approved repair are future work.

**Golden rule:** no future modification without a verified backup, explicit approval, and a usable rollback path.
