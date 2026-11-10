# HA Entity Sync — current release

Version 0.1.3 is intentionally read-only.

## Interface

- Responsive layout for mobile and desktop.
- Compact scan summary.
- Search by entity ID, file path, or reference category.
- Collapsible reference categories and entity details.
- Expand/collapse all category groups.
- File paths and line numbers are shown for each match.

## Scan behavior

- Entity IDs are read from `.storage/core.entity_registry`.
- References are searched in eligible text files under the Home Assistant configuration directory, including extensionless JSON files under `.storage`.
- Secret files, database files, backups, logs, media, and `www` are excluded.
- The app reports file paths, line numbers, and matching lines.
- It does not write to `/homeassistant`, call Home Assistant services, or perform renames.

## Important limitations

The current scanner finds references to registered entity IDs. It does not yet identify unregistered entity IDs as broken references, prove that every reference is active, or resolve all integration-managed dependencies. The UI intentionally does not display invented broken-entity counts.

The central entity database, scan history, native rename handoff, impact previews, backup/rollback, and approved repair workflow are planned features. Any future file modification must require a verified backup, an explicit approval, and a usable rollback path.

Use scan output to identify actual storage locations in this installation before designing any repair mechanism.
