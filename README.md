# HA Entity Sync

A local Home Assistant OS app for **read-only entity reference auditing**.

## Current release

Version **0.1.3** improves the mobile and desktop interface with a responsive layout, compact scan summary, searchable results, and collapsible reference groups. Expand a category and then an entity to inspect its file locations and line numbers.

The scanner reads the Home Assistant entity registry and searches selected configuration and storage files for references. It does not rename entities, edit Home Assistant files, or call services.

## Install (development)

1. In Home Assistant, open **Settings → Apps → App Store → ⋮ → Repositories** (older releases call these Add-ons).
2. Add: https://github.com/mubinbzs06/ha-entity-sync
3. Find **HA Entity Sync**, install, and start it.
4. Open **HA Entity Sync** from the Home Assistant sidebar. It uses authenticated Home Assistant Ingress; no LAN port is exposed.

The app mounts the Home Assistant configuration at `/homeassistant` **read-only**. It excludes secrets, databases, backups, logs, and Git metadata.

## Current limitations

- Broken-reference detection is not implemented yet. The current scan reports references to registered entities; it does not claim that unregistered IDs are broken.
- Rename impact previews, the central entity database, backup/rollback, and approved repair workflows are planned milestones, not active features.
- Reference coverage is limited to eligible files visible to the scanner. Integrations may store dependencies in databases or remote systems.

## Safety

- Home Assistant configuration mount is read-only.
- No entity rename or repair actions.
- No configuration writes or service calls.
- No cloud service or telemetry.
- Reports are generated on demand.

## Planned milestones

1. Read-only entity inventory and reference scan with responsive, collapsible UI.
2. Entity database, scan history, duplicate/name audits, and reliable broken-reference detection.
3. Rename impact analysis and visual before/broken/repaired previews.
4. Explicitly approved repairs with verified backups, rollback, validation, and audit history.
