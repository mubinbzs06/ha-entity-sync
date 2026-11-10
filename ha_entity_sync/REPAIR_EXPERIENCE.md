# Entity Sync — Replacement confidence engine

## Scope

Improve only how Entity Sync identifies likely replacement entities. Keep the existing app, screens, scan workflow, friendly-name handling, repair preview, backup, recovery, verification, audit history, and rollback behavior.

This is not a redesign and is not an Energy Dashboard milestone.

## Existing behavior to preserve

- Existing re-add detection checks unavailable/orphaned original entities against live suffixed entities.
- Existing strong identity checks use matching integration unique ID, device ID, or matching config-entry ID plus platform.
- Existing recovery flow creates and verifies a Supervisor backup before registry changes, keeps the old entry under a rollback ID, verifies the live replacement, and supports rollback.
- Existing friendly-name and clean-name suggestions remain advisory. They must not become identity proof or cause automatic renames.

## Replacement identification improvements

### Evidence hierarchy

1. **Strong identity evidence**
   - Matching integration unique ID.
   - Matching Home Assistant device ID.
   - Matching config-entry ID together with matching integration/platform.
2. **Supporting evidence**
   - Same entity domain.
   - Similar normalized object IDs, including the expected numeric re-add suffix pattern.
   - Compatible integration/platform metadata where available.
   - Matching device labels, when available, as supporting context only.
3. **Historical/statistics evidence**
   - Use only when a stable statistic identity and its metadata are actually available from Home Assistant.
   - Never infer matching history from similar entity names or entity IDs alone.
   - If unavailable, say that statistics identity was not checked.

### Confidence and safety rules

- A strong identity signal is required for a **high-confidence recovery candidate**.
- Name similarity alone can produce a review suggestion, never a recovery candidate.
- Same-domain similarity is supporting evidence, not identity proof.
- Conflicting platform, config-entry, device, or unique-ID evidence must lower confidence or suppress the candidate; never silently prefer a fuzzy name match.
- Show the evidence that supports and contradicts each candidate in plain language.
- Do not imply a score is a probability. Confidence labels describe evidence quality, not statistical certainty.
- If multiple plausible candidates exist, show the ambiguity and require human selection; do not silently choose one.
- Keep current conservative behavior when metadata is missing.

## Implementation sequence

1. Audit the current re-add matcher and its tests before changing it.
2. Extract or add a pure, testable evidence evaluator that returns structured evidence and a confidence classification.
3. Keep the existing re-add eligibility checks: original unavailable/orphaned, replacement live, same domain, and valid re-add relationship.
4. Add tests for each strong identity signal, missing metadata, conflicting metadata, name-only lookalikes, multiple candidates, and unavailable live-state access.
5. Surface the evidence breakdown in the existing candidate card without changing the overall layout or recovery workflow.
6. Run the full test suite and container smoke checks. Do not merge or release until they pass.

## Non-goals

- No new Energy Dashboard feature in this milestone.
- No automatic repairs or automatic entity renames.
- No generic text replacement across YAML, dashboards, or .storage.
- No changes to backup, approval, recovery, verification, or rollback semantics.
- No broad UI redesign.
