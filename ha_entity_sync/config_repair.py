"""Explicit, backed-up repairs of entity references in HA configuration files."""
import hashlib
import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("/data")
BACKUP_DIR = DATA_DIR / "config_repair_backups"
AUDIT_FILE = DATA_DIR / "config_repair_audit.json"
ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
MAX_BYTES = 5 * 1024 * 1024


class ConfigRepairError(RuntimeError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _safe_target(config_root, relative):
    rel = Path(relative)
    if not relative or rel.is_absolute() or ".." in rel.parts:
        raise ConfigRepairError("Invalid configuration path.")
    if rel.suffix.lower() not in {".yaml", ".yml", ".json"}:
        raise ConfigRepairError("Only YAML/JSON configuration files can be repaired.")
    if rel.parts[0] == ".storage" and not (
        len(rel.parts) == 2 and rel.name.startswith("lovelace")
    ):
        raise ConfigRepairError("Only user Lovelace storage is eligible under .storage.")
    root = Path(config_root).resolve(strict=True)
    target = Path(config_root) / rel
    if target.is_symlink() or not target.exists():
        raise ConfigRepairError("Target file is missing or is a symbolic link.")
    resolved = target.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ConfigRepairError("Target is outside the Home Assistant configuration.")
    if resolved.stat().st_size > MAX_BYTES:
        raise ConfigRepairError("Target file exceeds the 5 MB safety limit.")
    return resolved


def preview_config_repair(config_root, relative, old_id, new_id):
    if not ENTITY_RE.fullmatch(old_id or "") or not ENTITY_RE.fullmatch(new_id or ""):
        raise ConfigRepairError("Invalid entity ID.")
    if old_id == new_id:
        raise ConfigRepairError("Old and replacement entity IDs are identical.")
    target = _safe_target(config_root, relative)
    raw = target.read_bytes()
    try:
        source = raw.decode("utf-8")
    except UnicodeError as err:
        raise ConfigRepairError("Target file is not UTF-8 text.") from err
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(old_id) + r"(?![A-Za-z0-9_])")
    updated, count = pattern.subn(new_id, source)
    if count < 1:
        raise ConfigRepairError("The broken entity ID no longer appears in this file. Rescan first.")
    return {
        "file": Path(relative).as_posix(),
        "old_entity_id": old_id,
        "replacement_entity_id": new_id,
        "occurrences": count,
        "before_sha256": _sha(raw),
        "before": source,
        "after": updated,
    }


def _atomic_write(target, data):
    mode = target.stat().st_mode
    fd, temp_name = tempfile.mkstemp(prefix=".ha-entity-sync-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _load_audit():
    try:
        data = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def apply_config_repair(config_root, relative, old_id, new_id, expected_sha256):
    preview = preview_config_repair(config_root, relative, old_id, new_id)
    if not secrets.compare_digest(preview["before_sha256"], str(expected_sha256 or "")):
        raise ConfigRepairError("Configuration changed since preview. No changes were made; rescan and preview again.")
    target = _safe_target(config_root, relative)
    before = target.read_bytes()
    if _sha(before) != preview["before_sha256"]:
        raise ConfigRepairError("Configuration changed immediately before backup. No changes were made.")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    record_id = secrets.token_hex(8)
    backup_path = BACKUP_DIR / (record_id + ".bak")
    with backup_path.open("xb") as handle:
        handle.write(before)
        handle.flush()
        os.fsync(handle.fileno())
    backup_bytes = backup_path.read_bytes()
    if _sha(backup_bytes) != preview["before_sha256"]:
        backup_path.unlink(missing_ok=True)
        raise ConfigRepairError("Pre-repair backup verification failed. No changes were made.")
    current = target.read_bytes()
    if _sha(current) != preview["before_sha256"]:
        raise ConfigRepairError("Configuration changed during backup. No changes were made.")
    after = preview["after"].encode("utf-8")
    _atomic_write(target, after)
    if _sha(target.read_bytes()) != _sha(after):
        raise ConfigRepairError("Post-write verification failed. Restore the verified backup before proceeding.")
    record = {
        "id": record_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "applied",
        "file": Path(relative).as_posix(),
        "old_entity_id": old_id,
        "replacement_entity_id": new_id,
        "occurrences": preview["occurrences"],
        "backup_file": backup_path.name,
        "before_sha256": preview["before_sha256"],
        "after_sha256": _sha(after),
        "rollback_available": True,
    }
    records = _load_audit()
    records.append(record)
    temporary = AUDIT_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(records[-200:], indent=2), encoding="utf-8")
    os.replace(temporary, AUDIT_FILE)
    return {"ok": True, "record": record, "message": "Configuration references repaired and verified."}


def rollback_config_repair(config_root, record_id):
    records = _load_audit()
    record = next((item for item in records if item.get("id") == record_id), None)
    if not record or record.get("status") != "applied" or not record.get("rollback_available"):
        raise ConfigRepairError("This configuration repair is not available for rollback.")
    target = _safe_target(config_root, record["file"])
    current = target.read_bytes()
    if _sha(current) != record["after_sha256"]:
        raise ConfigRepairError("File changed after repair. Rollback refused to avoid overwriting newer edits.")
    backup_path = BACKUP_DIR / record["backup_file"]
    if backup_path.is_symlink() or not backup_path.is_file():
        raise ConfigRepairError("Verified pre-repair backup is missing.")
    before = backup_path.read_bytes()
    if _sha(before) != record["before_sha256"]:
        raise ConfigRepairError("Pre-repair backup verification failed. Rollback refused.")
    _atomic_write(target, before)
    if _sha(target.read_bytes()) != record["before_sha256"]:
        raise ConfigRepairError("Rollback write could not be verified.")
    record.update({
        "status": "rolled_back",
        "rolled_back_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rollback_available": False,
    })
    temporary = AUDIT_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(records[-200:], indent=2), encoding="utf-8")
    os.replace(temporary, AUDIT_FILE)
    return {"ok": True, "record": record, "message": "Original configuration restored and verified."}
