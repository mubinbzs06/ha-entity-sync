# HA Entity Sync — Build 2.7

**App version: 0.2.7**

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

Numeric suffixes and repeated words are informational observations only. The app does not generate replacement entity IDs or imply that these patterns should be changed.

## Existing audit behavior

- Opening the app automatically starts a fresh read-only scan.
- Results and statistics are session-only and clear when the app is closed or navigated away from.
- Entity references include source evidence and an **Open file** action where the source is in the approved viewer scope.
- The in-app viewer is read-only; **Back to entity** returns to the exact expanded entity/candidate.
- Potentially stale entity IDs are candidates for human review, not confirmed broken references.

## Safety and limitations

- The Home Assistant configuration mount remains read-only.
- No entity rename, configuration write, service call, backup/restore, or HA restart is implemented.
- POST requests remain disabled.
- Custom domains and unusual reference syntax may be missed.
- Dependencies held only in integrations, databases, or remote systems are outside this file scan.

Persistent entity database, scan history/diff, confirmed broken-reference validation, impact previews, verified backup/rollback, and approved repair remain future work.

**Golden rule:** no future modification without a verified backup, explicit approval, and a usable rollback path.
