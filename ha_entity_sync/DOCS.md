# HA Entity Sync — Build 2.12

**App version: 0.2.13**

Build 2.7 adds a read-only historical reference audit while keeping active dependencies separate.

## Reference categories

- **Active configuration and dashboards:** references in user-maintained YAML/Jinja configuration and Lovelace dashboards. These may be affected by an entity rename.
- **Historical:** records found in the explicitly allowed historical storage files. These are evidence of past or restored state, not active dependencies.
- **Needs review:** references whose purpose cannot be confidently classified.

Historical references are not counted as active dependencies. An unregistered ID found only in history is not confirmed broken and is not, by itself, a reason to delete or rename an entity.

## Historical scan scope

Only these additional Home Assistant storage files are scanned:

- `.storage/core.restore_state`
- `.storage/trace.saved_traces`

All other `.storage` internals remain excluded, including auth data, config entries, integration metadata, and databases. Existing file-size limits, backup exclusions, and read-only access remain in force.

## Naming observations

Numeric suffixes and repeated words remain informational observations. Separate clean-name plans prioritize collapsing an immediately repeated adjacent name sequence. A trailing _2/_3 removal is low-confidence advisory only. Integration-prefix removal is disabled. Duplicate suggested targets and registered-ID collisions are flagged.

## Naming review usage locations

Each naming observation now includes the registered entity's references found in the current scan scope, grouped as active usage and historical usage. Each entry shows its category, file path, matching line/context, and a read-only Open file action. Historical records remain separate and are not counted as active dependencies. If no references are found, the finding says so explicitly.

## Existing audit behavior

- Opening the app automatically starts a fresh read-only scan.
- Results and statistics are session-only and clear when the app is closed or navigated away from.
- Entity references include source evidence and an **Open file** action where the source is in the approved viewer scope.
- The in-app viewer is read-only; **Back to entity** returns to the exact expanded entity/candidate.
- Potentially stale entity IDs are candidates for human review, not confirmed broken references.

## Safety and limitations

- The Home Assistant configuration mount remains read-only.
- Re-add detection reads live Home Assistant states through the Supervisor proxy and requires shared unique_id, device ID, or config-entry ID plus platform. Names alone are not identity evidence.
- Re-add plans require an unavailable/orphaned original entity and a live suffixed replacement. Plans list active dashboard/YAML references that should resolve again after a future approved registry repair.
- Historical restore-state and trace references are not included in repair plans.
- The add-on requests Home Assistant API access for read-only state lookup; the configuration mount remains read-only.
- No entity rename, configuration write, service call, backup/restore, or HA restart is implemented.
- POST requests remain disabled.
- Custom domains and unusual reference syntax may be missed.
- Dependencies held only in integrations, databases, or remote systems are outside this file scan.

Persistent entity database, scan history/diff, confirmed broken-reference validation, impact previews, verified backup/rollback, and approved repair remain future work.

**Golden rule:** no change without a specific user-approved plan, a verified Supervisor backup, recoverable per-file backups, pre-apply validation, post-apply verification, and a usable rollback path.


## Build 2.10 repair planning boundaries

Re-add recovery and clean-name suggestions are plan-only. No Apply button or write endpoint exists. The planned future Apply phase must use the entity-registry WebSocket API, Lovelace WebSocket API for storage-managed dashboards, atomic YAML/package writes with per-file backups, a verified Supervisor partial backup, a change log, and a tested rollback. The app must not edit .storage files directly or restart Home Assistant automatically.


## Runtime safeguards (0.2.12)

- Scanner and viewer reject symbolic links and resolved paths outside the mounted HA configuration.
- Scan limits: 5,000 files, 100 MB total input, and 5 MB per file. The response includes warnings when a scan is incomplete.
- The listener starts before any scan; the UI triggers scans on open.
- `/health` checks for the entity registry; failed scans return HTTP 503.
- Open the panel through Home Assistant Ingress. Do not expose port 8099 directly.
- The add-on mount is read-only. The application uses the Supervisor proxy only for GET /api/states and has no service-call or write endpoint.
