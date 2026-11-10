# HA Entity Sync

A local Home Assistant OS app for **read-only entity reference auditing**.

## Current build

**Build 2.11 · Version 0.2.12** scans user-maintained Home Assistant configuration, Lovelace dashboards, and a small allowlist of historical storage records.

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
- Naming observations are informational only; numeric suffixes and repeated words are not treated as problems by themselves.
- Naming findings show the entity source/integration when recorded in the entity registry.
- Naming observations can include optional, unique, copyable ID suggestions; they never rename entities automatically.
- Usage evidence is grouped by file, with clickable line numbers, compact previews, expandable full references, and the investigated entity highlighted in both previews and the read-only file viewer.

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
- Persistent entity database, scan history, and the backup-gated Apply workflow remain future work.

**Golden rule:** no change without a specific user-approved plan, a verified Supervisor backup, recoverable per-file backups, pre-apply validation, post-apply verification, and a usable rollback path.


## Scan performance, counts, and exports (0.2.10)

- The add-on logs its version and listening port at startup, then logs scan duration and a count summary.
- Scan results are cached in memory for 60 seconds. Opening the panel reuses a fresh cached report; use **Rescan** to force a new scan.
- Counts distinguish registered entities, entities with active references, historical-only entities, needs-review-only entities, unreferenced entities, reference records (entity/file/category groups), and reference hits (occurrences).
- Export JSON or CSV for active dependencies, needs-review references, and stale candidates. Exports are generated in the browser and do not modify Home Assistant files.
- Stale candidates with active/dashboard evidence rank above historical-only candidates. The built-in `conversation` domain is excluded from stale-candidate detection to reduce false positives.


## Read-only repair planning (0.2.12)

- **Re-add recovery candidates** compare unavailable/orphaned original IDs with live suffixed IDs such as `sensor.foo_2`. A candidate is shown only when live state and strong registry lineage evidence agree: the same integration `unique_id`, device ID, or config-entry ID plus platform. Names alone are never treated as identity.
- Each re-add plan lists the original and replacement IDs, their states, identity evidence, and every active dashboard/YAML reference to the original ID. Those existing references are expected to resolve again after a future approved registry repair; historical restore-state and trace records are not included in repair plans.
- **Clean-name suggestions** prioritize collapsing an immediately repeated adjacent name sequence. Trailing `_2`/`_3` removal is a low-confidence advisory only. Integration-prefix removal is disabled to preserve identity. Duplicate targets within a batch and IDs already in the registry are marked as collisions.
- Collision proposals are blocked from being considered safe. No entity IDs, registry entries, dashboards, YAML files, or packages are changed.
- The add-on requests Home Assistant API access only to read live entity states. The configuration mount remains read-only; .storage is never edited.
- Applying a plan, obtaining Supervisor backups, registry updates, Lovelace API updates, YAML writes, and rollback are not implemented in this build. They remain a separate future phase.


## Runtime safeguards (0.2.12)

- The scanner and read-only file viewer reject symbolic links and paths resolving outside the Home Assistant configuration mount.
- A scan is capped at 5,000 files and 100 MB total input, in addition to the 5 MB per-file limit. If a cap is reached, the panel reports that the scan is incomplete; do not treat a limited scan as a complete audit.
- The add-on starts its HTTP listener before scanning. Scans begin when the panel is opened, avoiding a long scan delaying service startup.
- `/health` reports whether the entity registry is available. Failed scans return HTTP 503 with a diagnostic response.
- The web panel is intended to be opened through Home Assistant Ingress, not exposed directly to the LAN or Internet.
- The application mounts HA configuration read-only and performs only GET requests to the Home Assistant states API. No service calls, entity registry writes, or configuration writes are implemented.
