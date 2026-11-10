# Entity Sync — Human-readable repair experience

## Product principle

Spook is a useful reference for detecting unknown Home Assistant references and suggesting likely replacements. Entity Sync should go further: explain the impact in plain language, show the evidence, prepare a precise repair plan, and make every approved change recoverable.

**Find the ghost. Explain it. Prove the replacement. Preview the repair. Back up. Apply. Verify. Offer rollback.**

## First implementation target: Energy Dashboard

### Detection

Use Home Assistant's own read-only Energy WebSocket commands rather than treating a raw text search as authoritative:

- energy/validate — obtain Home Assistant's validation findings.
- energy/get_prefs — obtain the current Energy preferences for context.

The add-on already has an authenticated Home Assistant WebSocket client in recovery.py. Reuse the authenticated connection path without adding a second credential or exposing a token in the UI.

Treat the validation response as the source of truth for Energy findings. Do not infer that an entity is missing solely because it is absent from a text scan. Distinguish missing/not-defined from unavailable, non-numeric, or statistics-only cases according to Home Assistant's validation result.

### Replacement suggestions

1. Prefer a deleted-entity registry record, if Home Assistant provides one.
2. Otherwise suggest the closest currently registered entity in the same domain.
3. Use a conservative similarity threshold (Spook currently uses 0.8) and show the similarity as a suggestion, never as proof.
4. Corroborate with integration/platform, unique ID, config-entry ID, device ID, and statistics identity when available.
5. Clearly separate name similarity from identity evidence.
6. If evidence is insufficient or conflicting, say so and do not offer an automatic repair.

### Human-readable impact

The default view should answer: what is broken, what the setting controls, where it is configured, the likely replacement, why it is suggested, what would change, and what history/statistics could be affected.

Raw JSON/YAML and line-level detail belong behind an optional Technical details disclosure. Never make a user inspect hundreds of lines to understand the finding.

### Repair safety

The first Energy milestone is read-only detection and explanation only. No Energy preference writes are included in this milestone.

A later Energy repair must show exact old/new values and affected settings; preserve unrelated preferences; create and verify a recoverable backup; revalidate immediately before applying; require approval for that exact plan; verify saved preferences and validation afterwards; and retain rollback and audit records.

Do not remove Energy sources or clear settings just because they are unknown. Removing a meter can affect what the dashboard displays, even when recorder statistics remain.

## Generalization after Energy

Build the explanation/repair-plan model so it can later serve Lovelace dashboards, automations, scripts, helpers, and YAML configuration. Each reference type must have a type-specific explanation and repair strategy; do not apply generic text replacement to all files.

## UX sketch

**Energy Dashboard — Missing entity**

- Missing: sensor.example_old_energy
- Suggested replacement: sensor.example_energy
- Status: Candidate — identity not yet verified
- Meaning: The Energy Dashboard is configured to read a meter Home Assistant cannot currently find.
- Location: Energy Dashboard → Device consumption
- Evidence: same domain; name similarity; integration/device/statistics checks shown separately.
- Action: Review repair plan (disabled until sufficient evidence exists)

Technical details is optional and contains the underlying Home Assistant validation details and configuration evidence.

## Non-goals for the first milestone

- No live configuration changes.
- No automatic deletion or clearing.
- No direct editing of .storage/energy.
- No entity rename based on fuzzy name matching alone.
- No claim that a replacement preserves historical statistics until statistic IDs and metadata have been checked.