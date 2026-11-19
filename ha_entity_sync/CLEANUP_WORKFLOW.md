# Entity reconciliation and cleanup workflow

## Product rule

A missing entity ID is a reconciliation finding, not a deletion decision. Entity Sync must identify whether the original entity was renamed, recreated, moved, temporarily unavailable, genuinely removed, or cannot yet be classified. It must never infer that an entity should be removed merely because it is absent from the current registry or live state.

## Review sequence

1. **Discover references** across Lovelace dashboards, YAML configuration, automations, scripts, scenes, helpers, groups, templates, and People/device-tracker assignments where their authoritative storage can be read safely.
2. **Establish status** by checking entity registry and live state independently. A registered-but-unavailable entity is not a missing entity. If live-state access fails, report status as unverified.
3. **Reconcile identity** using deleted registry history and current registry metadata: integration/platform, integration unique ID, device ID, config-entry ID, and other corroborating metadata. Conflicts block a strong match. A unique strong match is a candidate, not an approval. Multiple strong matches remain ambiguous.
4. **Use names only for discovery.** Fuzzy entity-ID/name similarity may surface candidates but can never establish identity or authorize a repair.
5. **Show impact together.** Group every active reference to the missing ID and identify the exact dashboard, view/card, automation, script, helper, scene, or Person assignment. Keep historical-only references separate from active dependencies.
6. **Rework dashboards and configuration as a coherent plan.** Preserve card intent, layout, entities, automation behavior, and dependencies. Show the existing configuration, proposed result, and a faithful live preview when available. Label stored-config previews as such; never claim a mock is a rendered dashboard.
7. **Preview before applying.** Display every exact file and reference that will change. Do not use broad text replacement.
8. **Protect and approve.** Require a verified Home Assistant backup and explicit per-plan approval before any live change. Preserve rollback/quarantine history.
9. **Verify independently.** Check registry identity, stored configuration references, and rendered dashboard behavior separately. Report checks as passed, failed, or not verifiable.
10. **Retain uncertain findings.** Provide Keep/Ignore or defer options where appropriate. Never delete an entity or tracker because it is missing, unreferenced, or has a similar-looking replacement.

## Scope boundaries

- Read-only audit and reconciliation by default.
- No changes to RPi4 while RPi5 is the development/test target.
- No automatic entity deletion, renaming, tracker removal, dashboard redesign, or repair.
- No identity claims based solely on names, suffixes, or platform.
- No direct edits to Home Assistant .storage files.
- No automatic restart of Home Assistant.
- Every future write operation must have verified backup, explicit approval, pre-apply validation, post-apply verification, and rollback.

## 0.3.4 implementation slice

- Preserve safe identity fields from Home Assistant deleted-entity registry history.
- Compare deleted entries with all current registry entries, not only numeric-suffix pairs or fuzzy name matches.
- Surface unique strong identity matches as high-confidence reconciliation candidates.
- Surface multiple strong matches as ambiguous and do not preselect one.
- Block candidates when comparable identity fields conflict.
- Keep same-domain fuzzy matches clearly labelled as name-similarity-only.
- In Repair Center, show affected dashboard card context and a live dashboard preview route when available.

## 0.3.5 — People and device tracker review

The scanner reads Home Assistant's Person integration storage file at .storage/person in read-only mode. It recognizes the current collection format (data.items) and the legacy data.persons shape; unknown schemas are reported as unavailable rather than guessed. It lists stored Person-to-device_tracker assignments and independently checks each tracker against the entity registry and live state API.

Statuses are distinct: available, unavailable, unknown, registered with no live state, missing from both registry and live states, and not checked when live-state access fails. Only confirmed missing trackers receive replacement candidates, using deleted entity-registry identity evidence first and name similarity as discovery-only. Findings are review-only; this release does not remove, rewrite, or ignore assignments.

YAML-defined Person entries remain visible through the existing configuration-file scanner, but this phase does not yet group YAML-defined tracker references under a Person record. That requires a separate parser/merge step and must preserve YAML source locations. No .storage files are edited.
