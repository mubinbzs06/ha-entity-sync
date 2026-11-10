# HA Entity Sync

A local Home Assistant OS app for **read-only entity reference auditing**.

## Current build

**Build 2.7 · Version 0.2.7** scans user-maintained Home Assistant configuration, Lovelace dashboards, and a small allowlist of historical storage records.

It excludes unrelated Home Assistant internals, integration/add-on implementation folders, backups, archives, databases, logs, secrets, and runtime data.

Entities created by integrations are still tracked in the entity registry and appear when your own dashboards or configuration reference them.

## Navigation and audit

- The app automatically scans on open; results and statistics are session-only.
- Entity dependencies show exact source lines and an **Open file** action.
- The in-app file viewer opens eligible files at the matching line in read-only mode.
- **Back to entity** returns to the exact expanded entity/candidate.
- Potentially stale IDs are review candidates, not confirmed broken references.
- Active dependencies, historical references, and unclassified references are kept distinct.
- Historical hits are not counted as active dependencies and do not by themselves justify deleting or renaming an entity.
- Naming observations are informational only; numeric suffixes and repeated words are not treated as problems or rename recommendations.

## Scan scope

Included:
- Root YAML/Jinja configuration.
- User configuration folders such as `packages/`.
- Lovelace dashboards under `.storage/lovelace*`.
- Historical records from `.storage/core.restore_state` and `.storage/trace.saved_traces`.

Excluded:
- All other `.storage` internals.
- `custom_components`, add-on/application code, caches, and runtime folders.
- Backup/archive/snapshot files and folders.
- Secrets, auth/token files, databases, logs, media, `www`, and shared runtime data.

## Install

1. In Home Assistant, open **Settings → Apps → App Store → ⋮ → Repositories** (older releases call these Add-ons).
2. Add: https://github.com/mubinbzs06/ha-entity-sync
3. Find **HA Entity Sync**, install or update, and start it.
4. Open it from the Home Assistant sidebar.

The app mounts Home Assistant configuration at `/homeassistant` **read-only**. It does not rename entities, edit configuration, call services, or restart Home Assistant.

## Limitations and safety

- A missing-registry ID is only a candidate, not necessarily broken.
- Custom domains and unusual reference syntax may be missed.
- Dependencies held only in integrations, databases, or remote systems are outside this file scan.
- The viewer only serves files approved by the scanner's safe file scope.
- Persistent entity database, scan history, confirmed stale validation, impact previews, verified backup/rollback, and approved repair are future work.

**Golden rule:** no future modification without a verified backup, explicit approval, and a usable rollback path.
