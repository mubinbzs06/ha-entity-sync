# HA Entity Sync

A local Home Assistant OS add-on for **read-only entity reference auditing**.

## Current status

This is the first development scaffold. It does not rename entities, edit Home Assistant files, or call services. The first milestone is a read-only inventory and reference report.

## Install (development)

1. In Home Assistant, open **Settings → Add-ons → Add-on Store → ⋮ → Repositories**.
2. Add this repository URL: https://github.com/mubinbzs06/ha-entity-sync
3. Find **HA Entity Sync**, install it, and start it.
4. Open its Web UI on port 8099.

The add-on mounts `/config` read-only. It scans selected Home Assistant configuration and storage files while excluding secrets, databases, backups, logs, and Git metadata.

## Safety

- Read-only filesystem mount.
- No entity rename or repair actions.
- No configuration writes.
- No cloud service or telemetry.
- Reports are generated on demand.

## Planned milestones

1. Read-only entity registry inventory and reference scan.
2. Rename impact preview with confidence classifications.
3. Explicitly approved, backed-up, reversible repairs.
