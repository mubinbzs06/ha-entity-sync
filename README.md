# HA Entity Sync

A local Home Assistant OS app for **read-only entity reference auditing**.

## Current development build

Version **0.2.0** adds source-line evidence, copyable file locations, conservative unregistered-ID candidate detection, and naming review for numeric suffixes and repeated words.

The app still does not rename entities, edit Home Assistant files, or call services. Candidate IDs are not declared broken automatically.

## Install (development)

1. In Home Assistant, open **Settings → Apps → App Store → ⋮ → Repositories** (older releases call these Add-ons).
2. Add: https://github.com/mubinbzs06/ha-entity-sync
3. Find **HA Entity Sync**, install, and start it.
4. Open **HA Entity Sync** from the Home Assistant sidebar. It uses authenticated Home Assistant Ingress; no LAN port is exposed.

The app mounts the Home Assistant configuration at `/homeassistant` **read-only**. It excludes secrets, databases, backups, logs, and Git metadata.

## Build 2 features

- Registered entity references grouped by file role.
- Exact matching line snippets and copyable `file:line` locations.
- Conservative candidates for entity-like IDs absent from the current registry.
- Naming review for numeric suffixes and repeated words.
- Mobile/desktop responsive interface with collapsible sections.

## Limitations

- An unregistered ID is only a candidate; it is not necessarily broken.
- Candidate detection uses common Home Assistant domains and explicit reference contexts. Custom domains and unusual syntax may be missed.
- File scanning cannot see every dependency stored in databases, integrations, or remote systems.
- Persistent entity database, scan history, confirmed broken-reference validation, impact previews, backup/rollback, and approved repair are future work.

## Safety

- Home Assistant configuration mount is read-only.
- No entity rename or repair actions.
- No configuration writes or service calls.
- No cloud service or telemetry.
- No automatic restart.

**Golden rule:** no future modification without a verified backup, explicit approval, and a usable rollback path.
