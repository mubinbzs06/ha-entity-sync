# HA Entity Sync — first scan

This release is intentionally read-only.

- Entity IDs are read from `.storage/core.entity_registry`.
- References are searched in eligible text files under the Home Assistant configuration directory, including extensionless JSON files under `.storage`.
- Secret files, database files, backups, logs, media, and `www` are excluded.
- The app reports file paths, line numbers, and matching lines.
- It does not write to `/homeassistant`, call Home Assistant services, or perform renames.

The first scan is a discovery tool, not a guarantee that every possible reference is covered. Some integrations keep references in databases or remote systems, and generated/runtime references may not be represented in files.

Use the scan output to identify the actual storage locations in this installation before designing any repair mechanism.
