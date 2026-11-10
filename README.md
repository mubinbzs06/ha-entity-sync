# HA Entity Sync

A local Home Assistant OS app for **read-only entity reference auditing**.

## Current status

Version 0.1.0 is the first scanner milestone. It reads the entity registry and searches selected configuration and storage files for references. It does not rename entities, edit Home Assistant files, or call services.

## Install (development)

1. In Home Assistant, open **Settings → Apps → App Store → ⋮ → Repositories** (older releases call these Add-ons).
2. Add: https://github.com/mubinbzs06/ha-entity-sync
3. Find **HA Entity Sync**, install, and start it.
4. Open **HA Entity Sync** from the Home Assistant sidebar. It uses authenticated Home Assistant Ingress; no LAN port is exposed.

The app mounts the Home Assistant configuration at `/homeassistant` **read-only**. It excludes secrets, databases, backups, logs, and Git metadata.

## Safety

- Home Assistant configuration mount is read-only.
- No entity rename or repair actions.
- No configuration writes or service calls.
- No cloud service or telemetry.
- Reports are generated on demand.

## Planned milestones

1. Read-only entity registry inventory and reference scan.
2. Rename impact preview with confidence classifications.
3. Explicitly approved, backed-up, reversible repairs.
