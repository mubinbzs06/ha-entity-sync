# HA Entity Sync — Build 2

Version 0.2.0 remains strictly read-only.

## New in Build 2

- Shows the exact matching source line with each registered-entity reference.
- Provides a copyable `file:line` location for follow-up in the Home Assistant file editor.
- Detects *potentially stale reference candidates*: entity-like IDs in recognized entity-reference contexts that are absent from the current entity registry.
- Audits registered entity IDs for numeric suffixes such as `_2` and repeated words.
- Shows possible cleaner IDs as suggestions only.

## Important interpretation rules

- A potentially stale candidate is **not a confirmed broken reference**. It may be historical, integration-managed, temporarily unavailable, or valid in a context the scanner cannot understand.
- Candidate detection is deliberately conservative. It checks common Home Assistant domains and explicit entity-reference contexts; custom domains and unusual templates may be missed.
- A numeric suffix is not proof of a duplicate. A matching base entity is shown when one exists.
- Suggested IDs are not checked as a complete rename plan and must not be applied without checking uniqueness and dependencies.

## Scan behavior and safety

- Entity IDs are read from `.storage/core.entity_registry`.
- References are searched in eligible text files under the Home Assistant configuration directory, including extensionless JSON files under `.storage`.
- Secret files, database files, backups, logs, media, and `www` are excluded.
- The app does not write to `/homeassistant`, call Home Assistant services, rename entities, or modify configuration.
- POST requests remain disabled.

## Not included yet

The central persistent entity database, scan history/diff, confirmed broken-reference validation, native rename handoff, impact previews, verified backup/rollback, and approved repair workflow are not implemented in this build. Any future file modification must require a verified backup, explicit approval, and a usable rollback path.
