"""Explicit, backed-up Home Assistant entity-registry recovery operations.

The Home Assistant configuration mount is never written. Registry changes use the
Home Assistant WebSocket API; Supervisor backups provide the recovery checkpoint.
"""
import io
import json
import os
import re
import secrets
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path("/data")
AUDIT_FILE = DATA_DIR / "recovery_audit.json"
MAX_BACKUP_BYTES = 512 * 1024 * 1024
ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
NUMERIC_SUFFIX_RE = re.compile(r"_(\d+)$")


class RecoveryError(RuntimeError):
    """Raised when a recovery precondition or verification fails."""


def _unwrap(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def _supervisor_request(path, method="GET", payload=None, binary=False, timeout=600):
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RecoveryError("Supervisor API token unavailable. Backup access is not configured.")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        "http://supervisor" + path, data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_BACKUP_BYTES + 1 if binary else 2 * 1024 * 1024)
    except urllib.error.HTTPError as err:
        detail = err.read(2048).decode("utf-8", "replace")
        raise RecoveryError(f"Supervisor API HTTP {err.code}: {detail[:300]}") from err
    except (urllib.error.URLError, OSError) as err:
        raise RecoveryError(f"Supervisor API request failed: {type(err).__name__}") from err
    if binary:
        if len(raw) > MAX_BACKUP_BYTES:
            raise RecoveryError("Backup exceeds the 512 MB verification limit. No registry changes were made.")
        return raw
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as err:
        raise RecoveryError("Supervisor API returned invalid JSON.") from err


def _ha_ws(command):
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RecoveryError("Home Assistant API token unavailable.")
    try:
        import websocket
    except ImportError as err:
        raise RecoveryError("The WebSocket client dependency is missing.") from err
    ws = None
    try:
        ws = websocket.create_connection(
            "ws://supervisor/core/websocket", timeout=15, suppress_origin=True
        )
        ws.settimeout(15)
        greeting = json.loads(ws.recv())
        if greeting.get("type") != "auth_required":
            raise RecoveryError("Unexpected Home Assistant WebSocket greeting.")
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(ws.recv())
        if auth.get("type") != "auth_ok":
            raise RecoveryError("Home Assistant WebSocket authentication failed.")
        ws.send(json.dumps({"id": 1, **command}))
        response = json.loads(ws.recv())
        if response.get("id") != 1 or response.get("type") != "result":
            raise RecoveryError("Unexpected Home Assistant WebSocket response.")
        if not response.get("success"):
            error = response.get("error") or {}
            raise RecoveryError(
                "Home Assistant rejected the registry request: "
                + str(error.get("message") or error.get("code") or "unknown error")
            )
        return response.get("result")
    except RecoveryError:
        raise
    except Exception as err:
        raise RecoveryError(f"Home Assistant WebSocket request failed: {type(err).__name__}: {err}") from err
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _entries(entity_ids):
    result = _ha_ws({
        "type": "config/entity_registry/get_entries",
        "entity_ids": list(entity_ids),
    })
    if not isinstance(result, dict):
        raise RecoveryError("Home Assistant returned an invalid entity-registry response.")
    return result


def _rename(old_id, new_id):
    if not ENTITY_ID_RE.fullmatch(old_id or "") or not ENTITY_ID_RE.fullmatch(new_id or ""):
        raise RecoveryError("Invalid entity ID in registry rename.")
    return _ha_ws({
        "type": "config/entity_registry/update",
        "entity_id": old_id,
        "new_entity_id": new_id,
    })


IDENTITY_FIELDS = ("unique_id", "platform", "config_entry_id", "device_id")


def _identity_snapshot(entry):
    return {field: entry.get(field) for field in IDENTITY_FIELDS} if isinstance(entry, dict) else None


def _same_identity_snapshot(entry, snapshot):
    return (
        isinstance(entry, dict)
        and isinstance(snapshot, dict)
        and all(entry.get(field) == snapshot.get(field) for field in IDENTITY_FIELDS)
    )


def _identity_reason(first, second):
    if not isinstance(first, dict) or not isinstance(second, dict):
        return None
    same_platform = bool(first.get("platform")) and first.get("platform") == second.get("platform")
    if same_platform and first.get("unique_id") and first.get("unique_id") == second.get("unique_id"):
        return "Same integration unique_id"
    if same_platform and first.get("device_id") and first.get("device_id") == second.get("device_id"):
        return "Same registered device ID and integration"
    if same_platform and first.get("config_entry_id") and first.get("config_entry_id") == second.get("config_entry_id"):
        return "Same integration and config entry"
    return None


def verify_backup_archive(archive_bytes, required_entity_ids, expected_entries=None):
    """Verify a downloaded Supervisor archive contains the HA entity registry."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as outer:
            members = outer.getmembers()
            direct = next((
                item for item in members
                if item.isfile() and (item.name[2:] if item.name.startswith("./") else item.name) == ".storage/core.entity_registry"
            ), None)
            registry_bytes = outer.extractfile(direct).read() if direct else None
            if registry_bytes is None:
                nested = next((
                    item for item in members
                    if item.isfile() and Path(item.name).name in {
                        "homeassistant.tar", "homeassistant.tar.gz"
                    }
                ), None)
                if nested is None:
                    raise RecoveryError("Backup archive has no Home Assistant configuration archive.")
                nested_bytes = outer.extractfile(nested).read()
                with tarfile.open(fileobj=io.BytesIO(nested_bytes), mode="r:*") as ha_tar:
                    registry_member = next((
                        item for item in ha_tar.getmembers()
                        if item.isfile() and (item.name[2:] if item.name.startswith("./") else item.name) == ".storage/core.entity_registry"
                    ), None)
                    if registry_member is None:
                        raise RecoveryError("Backup does not contain .storage/core.entity_registry.")
                    registry_bytes = ha_tar.extractfile(registry_member).read()
        registry = json.loads(registry_bytes.decode("utf-8"))
        entities = registry.get("data", {}).get("entities", [])
        present = {
            item.get("entity_id")
            for item in entities if isinstance(item, dict)
        }
        missing = [entity_id for entity_id in required_entity_ids if entity_id not in present]
        if missing:
            raise RecoveryError("Verified backup is missing required registry entries: " + ", ".join(missing))
        for entity_id, expected in (expected_entries or {}).items():
            if not _same_identity_snapshot(found[entity_id], _identity_snapshot(expected)):
                raise RecoveryError(f"Backup registry identity does not match the live entry for {entity_id}.")
        return True
    except RecoveryError:
        raise
    except Exception as err:
        raise RecoveryError(f"Could not verify backup archive: {type(err).__name__}: {err}") from err


def create_verified_backup(required_entity_ids, label, expected_entries=None):
    """Create a partial HA backup (excluding the recorder DB) and verify its registry."""
    name = ("HA Entity Sync before " + label)[:120]
    response = _unwrap(_supervisor_request(
        "/backups/new/partial",
        method="POST",
        payload={
            "name": name,
            "homeassistant": True,
            "homeassistant_exclude_database": True,
            "background": False,
        },
    ))
    slug = response.get("slug") if isinstance(response, dict) else None
    if not slug or not re.fullmatch(r"[A-Za-z0-9_-]{4,80}", str(slug)):
        raise RecoveryError("Supervisor did not confirm backup creation. No registry changes were made.")
    info = _unwrap(_supervisor_request(f"/backups/{slug}/info"))
    if not isinstance(info, dict) or not info.get("homeassistant"):
        raise RecoveryError(f"Backup {slug} does not confirm Home Assistant is included.")
    archive = _supervisor_request(f"/backups/{slug}/download", binary=True)
    verify_backup_archive(archive, required_entity_ids, expected_entries)
    return {"slug": slug, "name": info.get("name") or name, "date": info.get("date")}


def load_audit():
    try:
        records = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))
        return records if isinstance(records, list) else []
    except (OSError, ValueError, TypeError):
        return []


def _save_audit(records):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = AUDIT_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(records[-200:], indent=2), encoding="utf-8")
    os.replace(temporary, AUDIT_FILE)


def _append_audit(record):
    records = load_audit()
    records.append(record)
    _save_audit(records)


def _update_audit(record_id, changes):
    records = load_audit()
    for record in records:
        if record.get("id") == record_id:
            record.update(changes)
            _save_audit(records)
            return record
    raise RecoveryError("Recovery audit record was not found.")


def _fresh(ids, fetch_live_states):
    entries = _entries(ids)
    states = fetch_live_states()
    if states is None:
        raise RecoveryError("Live Home Assistant states are unavailable.")
    return entries, states


def _validate_pair(old_id, replacement_id, fetch_live_states):
    if not ENTITY_ID_RE.fullmatch(old_id or "") or not ENTITY_ID_RE.fullmatch(replacement_id or ""):
        raise RecoveryError("Invalid entity ID supplied.")
    old_domain, old_object = old_id.split(".", 1)
    new_domain, new_object = replacement_id.split(".", 1)
    suffix = NUMERIC_SUFFIX_RE.search(new_object)
    if old_domain != new_domain or not suffix or new_object[:suffix.start()] != old_object:
        raise RecoveryError("Replacement is not the numeric-suffix counterpart of the original ID.")
    entries, states = _fresh([old_id, replacement_id], fetch_live_states)
    old_entry, replacement_entry = entries.get(old_id), entries.get(replacement_id)
    if not isinstance(old_entry, dict) or not isinstance(replacement_entry, dict):
        raise RecoveryError("A registry entry disappeared. Rescan before retrying.")
    if old_entry.get("disabled_by") is not None or replacement_entry.get("disabled_by") is not None:
        raise RecoveryError("A disabled entity was found. Disabled entities are not auto-recovered.")
    identity = _identity_reason(old_entry, replacement_entry)
    if not identity:
        raise RecoveryError("Device/integration identity no longer matches. No changes were made.")
    old_state_obj, new_state_obj = states.get(old_id), states.get(replacement_id)
    old_state = old_state_obj.get("state") if old_state_obj else "orphaned"
    new_state = new_state_obj.get("state") if new_state_obj else None
    if old_state not in {"unavailable", "unknown", "orphaned"}:
        raise RecoveryError(f"Original entity is no longer unavailable (state: {old_state}). Rescan first.")
    if new_state in {None, "unavailable", "unknown"}:
        raise RecoveryError("Replacement entity is no longer live. No changes were made.")
    return old_entry, replacement_entry, identity


def recover_entity_pair(old_id, replacement_id, fetch_live_states):
    """Move the old entry aside, restore the live entity to the original ID, and retain rollback."""
    old_entry, replacement_entry, identity = _validate_pair(old_id, replacement_id, fetch_live_states)
    backup = create_verified_backup([old_id, replacement_id], f"recovery of {old_id}", {old_id: old_entry, replacement_id: replacement_entry})
    # Backup generation can take time; repeat all identity/state checks immediately before writes.
    checked_old, checked_replacement, checked_identity = _validate_pair(old_id, replacement_id, fetch_live_states)
    if checked_identity != identity:
        raise RecoveryError("Identity evidence changed while the backup was being created. No registry changes were made.")
    old_entry, replacement_entry = checked_old, checked_replacement
    record_id = secrets.token_hex(8)
    record = {
        "id": record_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "prepared",
        "old_entity_id": old_id,
        "replacement_entity_id": replacement_id,
        "identity_reason": identity,
        "old_identity": _identity_snapshot(old_entry),
        "replacement_identity": _identity_snapshot(replacement_entry),
        "backup_slug": backup["slug"],
        "backup_name": backup["name"],
        "temporary_entity_id": None,
        "rollback_available": False,
    }
    # Persist the verified backup reference before the first registry write.
    _append_audit(record)
    temp_id = None
    try:
        domain, object_id = old_id.split(".", 1)
        for _ in range(8):
            candidate = domain + "." + object_id[:205] + "_hes_old_" + secrets.token_hex(4)
            entries, states = _fresh([candidate], fetch_live_states)
            if entries.get(candidate) is None and candidate not in states:
                temp_id = candidate
                break
        if not temp_id:
            raise RecoveryError("Could not reserve a temporary rollback ID.")

        _update_audit(record_id, {
            "temporary_entity_id": temp_id,
            "status": "moving_original_to_rollback_id",
        })
        _rename(old_id, temp_id)

        entries = _entries([old_id, replacement_id, temp_id])
        if entries.get(old_id) is not None or not _same_identity_snapshot(entries.get(temp_id), _identity_snapshot(old_entry)):
            raise RecoveryError("Original registry entry did not move safely to its rollback ID.")

        # Abort if the old entity is still present/live after its registry entry was moved.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            states = fetch_live_states()
            if states is None:
                raise RecoveryError("Live states became unavailable during recovery.")
            quarantined = states.get(temp_id)
            if quarantined and quarantined.get("state") not in {"unavailable", "unknown"}:
                raise RecoveryError("Original entity became live under the temporary ID; recovery was stopped.")
            if old_id not in states:
                break
            time.sleep(0.25)
        else:
            raise RecoveryError("Original state did not leave the original ID after quarantine.")

        _update_audit(record_id, {"status": "moving_live_replacement_to_original_id"})
        _rename(replacement_id, old_id)

        deadline = time.monotonic() + 12
        final_entries, final_states = {}, {}
        while time.monotonic() < deadline:
            final_entries, final_states = _fresh([old_id, replacement_id, temp_id], fetch_live_states)
            live = final_states.get(old_id)
            if (
                isinstance(final_entries.get(old_id), dict)
                and _same_identity_snapshot(final_entries[old_id], _identity_snapshot(replacement_entry))
                and final_entries.get(replacement_id) is None
                and live and live.get("state") not in {"unavailable", "unknown"}
                and replacement_id not in final_states
                and _same_identity_snapshot(final_entries.get(temp_id), _identity_snapshot(old_entry))
                and (not final_states.get(temp_id) or final_states[temp_id].get("state") in {"unavailable", "unknown"})
            ):
                break
            time.sleep(0.5)
        else:
            raise RecoveryError("Replacement did not verify live under the original ID.")

        _update_audit(record_id, {
            "status": "recovered",
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rollback_available": True,
            "verified_state": final_states[old_id].get("state"),
        })
        return {
            "ok": True,
            "status": "recovered",
            "record_id": record_id,
            "old_entity_id": old_id,
            "replacement_entity_id": replacement_id,
            "temporary_entity_id": temp_id,
            "backup_slug": backup["slug"],
            "message": "Replacement is live under the original ID. The old entry is quarantined for rollback.",
        }
    except Exception as err:
        rollback_ok = False
        rollback_message = "No registry changes were made."
        try:
            ids = [old_id, replacement_id] + ([temp_id] if temp_id else [])
            entries = _entries(ids)
            if isinstance(entries.get(old_id), dict) and _identity_reason(replacement_entry, entries[old_id]):
                _rename(old_id, replacement_id)
            entries = _entries(ids)
            if temp_id and isinstance(entries.get(temp_id), dict) and entries.get(old_id) is None:
                _rename(temp_id, old_id)
            verify = _entries([old_id, replacement_id])
            rollback_ok = (
                _identity_reason(old_entry, verify.get(old_id)) is not None
                and _identity_reason(replacement_entry, verify.get(replacement_id)) is not None
            )
            rollback_message = "Original registry IDs restored." if rollback_ok else "Automatic rollback could not be verified."
        except Exception as rollback_error:
            rollback_message = f"Automatic rollback failed: {type(rollback_error).__name__}: {rollback_error}"
        status = "rolled_back_after_failure" if rollback_ok else "manual_restore_required"
        try:
            _update_audit(record_id, {
                "status": status,
                "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "error": f"{type(err).__name__}: {err}",
                "rollback_message": rollback_message,
                "rollback_available": bool(temp_id) and not rollback_ok,
            })
        except Exception:
            pass
        return {
            "ok": False,
            "status": status,
            "error": str(err),
            "rollback_message": rollback_message,
            "temporary_entity_id": temp_id,
            "backup_slug": backup["slug"],
        }


def rollback_entity_recovery(record_id, fetch_live_states):
    """Reverse a completed recovery, taking and verifying a second backup first."""
    record = next((item for item in load_audit() if item.get("id") == record_id), None)
    if not record or record.get("status") != "recovered" or not record.get("rollback_available"):
        raise RecoveryError("This recovery is not available for rollback.")
    old_id = record["old_entity_id"]
    replacement_id = record["replacement_entity_id"]
    temp_id = record["temporary_entity_id"]
    entries, states = _fresh([old_id, replacement_id, temp_id], fetch_live_states)
    recovered_entry, quarantined_entry = entries.get(old_id), entries.get(temp_id)
    live = states.get(old_id)
    if (
        not isinstance(recovered_entry, dict)
        or not isinstance(quarantined_entry, dict)
        or entries.get(replacement_id) is not None
        or not _same_identity_snapshot(recovered_entry, record.get("replacement_identity"))
        or not _same_identity_snapshot(quarantined_entry, record.get("old_identity"))
        or not live or live.get("state") in {"unavailable", "unknown"}
    ):
        raise RecoveryError("Current registry/state no longer matches this recovery. Rollback was not attempted.")

    backup = create_verified_backup([old_id, temp_id], f"rollback of {old_id}", {old_id: recovered_entry, temp_id: quarantined_entry})
    # Recheck the recovered layout after backup creation and immediately before rollback writes.
    entries, states = _fresh([old_id, replacement_id, temp_id], fetch_live_states)
    if (
        not _same_identity_snapshot(entries.get(temp_id), record.get("old_identity"))
        or not _same_identity_snapshot(entries.get(old_id), record.get("replacement_identity"))
        or entries.get(replacement_id) is not None
        or not states.get(old_id)
        or states[old_id].get("state") in {"unavailable", "unknown"}
    ):
        raise RecoveryError("Registry/state changed while the rollback backup was being created. No rollback changes were made.")
    try:
        _rename(old_id, replacement_id)
        entries = _entries([old_id, replacement_id, temp_id])
        if entries.get(old_id) is not None or not isinstance(entries.get(replacement_id), dict):
            raise RecoveryError("Could not move the recovered entity back to its suffixed ID.")
        _rename(temp_id, old_id)

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            final_entries, final_states = _fresh([old_id, replacement_id, temp_id], fetch_live_states)
            replacement_state = final_states.get(replacement_id)
            if (
                _same_identity_snapshot(final_entries.get(old_id), record.get("old_identity"))
                and _same_identity_snapshot(final_entries.get(replacement_id), record.get("replacement_identity"))
                and replacement_state and replacement_state.get("state") not in {"unavailable", "unknown"}
            ):
                _update_audit(record_id, {
                    "status": "rolled_back",
                    "rolled_back_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "rollback_backup_slug": backup["slug"],
                    "rollback_available": False,
                })
                return {
                    "ok": True,
                    "status": "rolled_back",
                    "backup_slug": backup["slug"],
                    "message": "Original unavailable ID and live suffixed ID have been restored.",
                }
            time.sleep(0.5)
        raise RecoveryError("Rollback changes were made but final verification timed out.")
    except Exception as err:
        # Best-effort compensation returns to the recovered arrangement.
        try:
            entries = _entries([old_id, replacement_id, temp_id])
            if _same_identity_snapshot(entries.get(old_id), record.get("old_identity")):
                _rename(old_id, temp_id)
            entries = _entries([old_id, replacement_id, temp_id])
            if _same_identity_snapshot(entries.get(replacement_id), record.get("replacement_identity")) and entries.get(old_id) is None:
                _rename(replacement_id, old_id)
        except Exception:
            pass
        raise RecoveryError(
            f"Rollback did not complete: {err}. Verified pre-rollback backup: {backup['slug']}."
        ) from err
