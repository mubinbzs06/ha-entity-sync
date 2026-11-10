#!/usr/bin/env python3
"""Read-only Home Assistant entity reference auditor."""
import json
import os
import re
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CONFIG = Path("/homeassistant")
REGISTRY = CONFIG / ".storage" / "core.entity_registry"
MAX_FILE_BYTES = 5 * 1024 * 1024
VERSION = "0.2.11"
PORT = 8099
SCAN_CACHE_SECONDS = 60
IGNORED_STALE_DOMAINS = {"conversation"}
_scan_cache = {"report": None, "created": 0.0}
_scan_lock = threading.Lock()
HISTORICAL_STORAGE_NAMES = {"core.restore_state", "trace.saved_traces"}
# Search user-maintained configuration plus a small, explicit historical-storage allowlist.
# Never traverse all of .storage: most files are HA internals or credentials.
SKIP_DIRS = {
    ".git", "backups", "backup", "deps", "tts", "__pycache__",
    "www", "media", "share", "blueprints_cache", "custom_components",
    "node_modules", "appdaemon", "pyscript", "esphome", "zigbee2mqtt",
    "node-red", "nodered", "addons", "add-ons", "logs", "log"
}
SKIP_NAMES = {
    "secrets.yaml", "secrets.yml", "home-assistant_v2.db",
    "home-assistant_v2.db-wal", "home-assistant_v2.db-shm",
    "core.config_entries"
}
BACKUP_NAME_RE = re.compile(
    r"(^|[._ -])(backup|backups|archive|archived|snapshot|snapshots)([._ -]|$)",
    re.IGNORECASE
)
BACKUP_SUFFIXES = (
    ".tar", ".tar.gz", ".tgz", ".zip", ".7z", ".rar", ".gz",
    ".bak", ".old", ".orig", ".tmp", ".db", ".db-wal", ".db-shm"
)
ALLOWED_SUFFIXES = {
    ".yaml", ".yml", ".json", ".json5", ".jinja", ".jinja2",
    ".py", ".js", ".ts", ".md"
}
ENTITY_RE = re.compile(r"(?<![A-Za-z0-9_])[a-z0-9_]+\.[a-z0-9_]+(?![A-Za-z0-9_])")
ENTITY_CONTEXT_RE = re.compile(
    r'entity_ids?\s*:|["\']entities?["\']\s*:|'
    r'\b(?:states|is_state|is_state_attr|has_value|expand)\s*\(|'
    r'\btarget\s*:|\bsource\s*:',
    re.IGNORECASE
)
NUMERIC_SUFFIX_RE = re.compile(r"_(\d+)$")

# Only common Home Assistant domains are eligible for stale-reference candidates.
# Unknown/custom domains are deliberately left unclassified to avoid false alarms.
COMMON_DOMAINS = set("""
alarm_control_panel automation binary_sensor button calendar camera climate
conversation cover device_tracker event fan group humidifier image input_boolean
input_button input_datetime input_number input_select input_text lawn_mower light
lock media_player notify number persistent_notification person remote scene
 script select sensor siren stt sun switch text time todo tts update vacuum
 valve water_heater weather zone
""".split())


def load_registry():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entities = data.get("data", {}).get("entities", [])
    clean = [item for item in entities if isinstance(item, dict) and item.get("entity_id")]
    return clean


def is_backup_path(path):
    """Reject backup/archive names and archive/database file formats anywhere."""
    for part in path.parts:
        if BACKUP_NAME_RE.search(part):
            return True
        if part.lower().endswith(BACKUP_SUFFIXES):
            return True
    return False


def candidate_files():
    for root, dirs, names in os.walk(CONFIG):
        root_path = Path(root)
        try:
            relative_root = root_path.relative_to(CONFIG)
        except ValueError:
            dirs[:] = []
            continue

        # Never recurse into Home Assistant internal storage. Only user dashboards
        # stored by the Lovelace UI are useful references for this audit.
        if relative_root == Path(".storage"):
            dirs[:] = []
            for name in names:
                if not (name.startswith("lovelace") or name in HISTORICAL_STORAGE_NAMES):
                    continue
                path = root_path / name
                if is_backup_path(path) or name in SKIP_NAMES:
                    continue
                try:
                    if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
                        yield path
                except OSError:
                    continue
            continue

        dirs[:] = [
            d for d in dirs
            if d.lower() not in SKIP_DIRS
            and not is_backup_path(relative_root / d)
        ]

        for name in names:
            path = root_path / name
            rel = path.relative_to(CONFIG)
            lower_name = name.lower()
            if name in SKIP_NAMES or lower_name.startswith(("secret", "auth", "token")):
                continue
            if is_backup_path(rel):
                continue
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            try:
                if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
                    yield path
            except OSError:
                continue



def read_scoped_file(requested):
    """Read one scanner-eligible file without allowing path traversal or symlink escape."""
    relative = Path(requested)
    if not requested or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid file path")

    allowed = {
        path.relative_to(CONFIG).as_posix(): path
        for path in candidate_files()
    }
    target = allowed.get(relative.as_posix())
    if target is None:
        raise FileNotFoundError("File is not in the read-only audit scope")

    resolved_config = CONFIG.resolve()
    resolved_target = target.resolve(strict=True)
    if not resolved_target.is_relative_to(resolved_config):
        raise FileNotFoundError("File is outside the Home Assistant configuration")
    if not resolved_target.is_file() or resolved_target.stat().st_size > MAX_FILE_BYTES:
        raise FileNotFoundError("File is unavailable")

    return {
        "path": relative.as_posix(),
        "content": resolved_target.read_text(encoding="utf-8")
    }

def classify_reference(path):
    rel = path.as_posix()
    name = path.name.lower()
    if rel.startswith(".storage/lovelace"):
        return "Dashboard"
    if rel.startswith(".storage/") and path.name in HISTORICAL_STORAGE_NAMES:
        return "Historical"
    if path.suffix.lower() in {".yaml", ".yml", ".jinja", ".jinja2"}:
        return "Active configuration"
    return "Needs review"


def suggest_entity_names(entity_id, occupied):
    """Return advisory, unique alternatives without changing the registered ID."""
    domain, object_id = entity_id.split(".", 1)
    words = [word for word in object_id.split("_") if word]
    candidates = []

    # Remove a trailing numeric suffix only as a candidate, never as an assumption.
    base_words = words[:-1] if words and words[-1].isdigit() else words[:]
    # Collapse repeated tokens while preserving their original order.
    deduped = []
    for word in base_words:
        if word not in deduped:
            deduped.append(word)

    def add(parts):
        candidate = domain + "." + "_".join(parts)
        if parts and candidate != entity_id and candidate not in occupied and candidate not in candidates:
            candidates.append(candidate)

    add(deduped)

    # If the original object ID is overly verbose, offer a compact form by
    # removing generic trailing descriptors, but keep at least two meaningful tokens.
    generic = {"sensor", "device", "entity", "value", "reading"}
    compact = deduped[:]
    while len(compact) > 2 and compact[-1] in generic:
        compact.pop()
    add(compact)

    return candidates[:3]


def naming_audit(registry):
    ids = {item["entity_id"] for item in registry}
    findings = []
    for item in registry:
        entity_id = item["entity_id"]
        domain, object_id = entity_id.split(".", 1)
        observations = []
        suffix = NUMERIC_SUFFIX_RE.search(object_id)
        if suffix:
            base_object = object_id[:suffix.start()]
            base_id = domain + "." + base_object
            observations.append({
                "type": "Numeric suffix (informational)",
                "detail": "This suffix may be intentional, including when multiple devices or entities share a name. It is not a problem by itself.",
                "matching_base": base_id if base_id in ids else None
            })
        words = [word for word in object_id.split("_") if word]
        repeated = sorted({word for word, count in Counter(words).items() if count > 1})
        if repeated:
            observations.append({
                "type": "Repeated words (informational)",
                "detail": "Repeated token(s): " + ", ".join(repeated) + ". This may be intentional and is not a problem by itself."
            })
        if observations:
            findings.append({
                "entity_id": entity_id,
                "name": item.get("name") or item.get("original_name") or "",
                "source": item.get("platform") or None,
                "issues": observations,
                "suggestions": suggest_entity_names(entity_id, ids),
            })
    return findings


def fetch_live_states():
    """Read live HA states through the Supervisor proxy; never mutate HA."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    request = urllib.request.Request(
        "http://supervisor/core/api/states",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {item.get("entity_id"): item for item in payload if isinstance(item, dict) and item.get("entity_id")}
    except (OSError, ValueError, urllib.error.URLError):
        return None


def load_device_names():
    """Read device labels only as corroborating identity metadata."""
    path = CONFIG / ".storage" / "core.device_registry"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            item.get("id"): (item.get("name_by_user") or item.get("name") or "")
            for item in data.get("data", {}).get("devices", [])
            if isinstance(item, dict) and item.get("id")
        }
    except (OSError, ValueError, TypeError):
        return {}


def line_update_evidence(reference, old_id, new_id):
    updates = []
    for match in reference.get("matches", []):
        before = match.get("full_text") or match.get("text") or ""
        if old_id in before:
            updates.append({
                "file": reference["file"],
                "line": match.get("line"),
                "category": reference.get("category"),
                "before": before,
                "after": re.sub(r"(?<![A-Za-z0-9_])" + re.escape(old_id) + r"(?![A-Za-z0-9_])", new_id, before),
            })
    return updates


def detect_readded_pairs(registry, references, live_states, device_names):
    """Find suffix replacement pairs only when HA state and identity evidence agree."""
    if live_states is None:
        return [], "Live Home Assistant state access is unavailable; re-add detection is not confirmed."
    by_id = {item["entity_id"]: item for item in registry}
    refs_by_id = {}
    for reference in references:
        refs_by_id.setdefault(reference["entity_id"], []).append(reference)
    plans = []
    for replacement_id, replacement in by_id.items():
        match = NUMERIC_SUFFIX_RE.search(replacement_id.split(".", 1)[1])
        if not match:
            continue
        domain, object_id = replacement_id.split(".", 1)
        old_id = domain + "." + object_id[:match.start()]
        old = by_id.get(old_id)
        if not old:
            continue
        old_state = live_states.get(old_id)
        new_state = live_states.get(replacement_id)
        old_status = old_state.get("state") if old_state else "orphaned"
        new_status = new_state.get("state") if new_state else None
        if old_status not in {"unavailable", "unknown", "orphaned"}:
            continue
        if new_status in {None, "unavailable", "unknown"}:
            continue

        old_device = old.get("device_id")
        new_device = replacement.get("device_id")
        old_platform = old.get("platform")
        new_platform = replacement.get("platform")
        old_config = old.get("config_entry_id")
        new_config = replacement.get("config_entry_id")
        old_device_name = device_names.get(old_device, "").strip().casefold()
        new_device_name = device_names.get(new_device, "").strip().casefold()
        old_original = (old.get("original_name") or old.get("name") or "").strip().casefold()
        new_original = (replacement.get("original_name") or replacement.get("name") or "").strip().casefold()

        if old_device and old_device == new_device:
            confidence, identity_reason = "high", "Same registered device ID"
        elif old_config and old_config == new_config and old_platform and old_platform == new_platform:
            confidence, identity_reason = "high", "Same integration and config entry"
        elif old_platform and old_platform == new_platform and (
            (old_device_name and old_device_name == new_device_name)
            or (old_original and old_original == new_original)
        ):
            confidence, identity_reason = "medium", "Same integration with matching device/entity name"
        else:
            continue

        active_refs = [
            ref for ref in refs_by_id.get(old_id, [])
            if ref.get("category") in {"Dashboard", "Active configuration"}
        ]
        updates = []
        for ref in active_refs:
            updates.extend(line_update_evidence(ref, old_id, old_id))
        plans.append({
            "old_entity_id": old_id,
            "replacement_entity_id": replacement_id,
            "old_state": old_status,
            "replacement_state": new_status,
            "integration": old_platform or new_platform or "Unknown",
            "device_name": device_names.get(old_device) or device_names.get(new_device) or "",
            "confidence": confidence,
            "identity_reason": identity_reason,
            "active_references": active_refs,
            "reference_count": sum(ref.get("count", 0) for ref in active_refs),
            "affected_lines": updates,
            "proposed_steps": [
                "Retire the unavailable/orphaned old registry entry after a fresh identity check",
                f"Rename {replacement_id} to {old_id} through the HA entity-registry API",
                "Keep existing active references to the original ID; they should resolve to the live entity again",
            ],
            "read_only_plan": True,
        })
    plans.sort(key=lambda item: (item["confidence"] != "high", item["old_entity_id"]))
    return plans, "Live states checked. A pair is a candidate plan, not permission to change the registry."


def build_clean_name_suggestions(registry, references):
    """Create exact, collision-aware rename proposals without changing entity IDs."""
    occupied = {item["entity_id"] for item in registry}
    refs_by_id = {}
    for reference in references:
        refs_by_id.setdefault(reference["entity_id"], []).append(reference)
    suggestions = []
    for item in registry:
        entity_id = item["entity_id"]
        domain, object_id = entity_id.split(".", 1)
        words = object_id.split("_")
        candidate_words = words[:]
        reasons = []
        # Remove a duplicated adjacent sequence, preserving meaningful numeric tokens.
        for width in range(len(candidate_words) // 2, 0, -1):
            if candidate_words[:width] == candidate_words[width:2 * width]:
                candidate_words = candidate_words[:width] + candidate_words[2 * width:]
                reasons.append("Collapsed an immediately repeated name sequence")
                break
        suffix_removed = False
        if candidate_words and candidate_words[-1] in {"2", "3"}:
            removed_suffix = candidate_words.pop()
            suffix_removed = True
            reasons.append(f"Removed trailing HA duplicate suffix _{removed_suffix}; verify it is not intentional")
        platform_removed = False
        platform = (item.get("platform") or "").casefold()
        if platform and len(candidate_words) > 1 and candidate_words[0].casefold() == platform:
            candidate_words = candidate_words[1:]
            platform_removed = True
            reasons.append("Removed a leading integration prefix confirmed by registry metadata")
        if candidate_words == words:
            continue
        proposed = domain + "." + "_".join(candidate_words)
        if proposed == domain + ".":
            continue
        collision = proposed in occupied and proposed != entity_id
        repeated_only = any("repeated name sequence" in reason for reason in reasons) and not suffix_removed and not platform_removed
        confidence = "low" if collision else "high" if repeated_only else "medium"
        refs = [ref for ref in refs_by_id.get(entity_id, []) if ref.get("category") in {"Dashboard", "Active configuration"}]
        updates = []
        for reference in refs:
            updates.extend(line_update_evidence(reference, entity_id, proposed))
        suggestions.append({
            "entity_id": entity_id,
            "suggested_entity_id": proposed,
            "collision": collision,
            "confidence": confidence,
            "reason": "; ".join(reasons),
            "references": refs,
            "affected_lines": updates,
            "proposed_steps": [
                f"Rename the registered entity from {entity_id} to {proposed}",
                "Update only the listed active dashboard/YAML references",
                "Validate the new ID is still collision-free immediately before any future Apply",
            ],
            "read_only_plan": True,
        })
    return suggestions

def scan():
    started = time.monotonic()
    if not REGISTRY.exists():
        print("HA Entity Sync: scan failed - entity registry not found", flush=True)
        return {"ok": False, "error": "Entity registry not found at .storage/core.entity_registry"}
    try:
        registry = load_registry()
    except Exception as err:
        print(f"HA Entity Sync: scan failed - {type(err).__name__}: {err}", flush=True)
        return {"ok": False, "error": f"Could not read entity registry: {type(err).__name__}: {err}"}

    entity_ids = sorted({item["entity_id"] for item in registry})
    known = set(entity_ids)
    known_domains = COMMON_DOMAINS | {entity_id.split(".", 1)[0] for entity_id in known}
    references = {}
    candidates = {}
    scanned = 0

    for path in candidate_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        scanned += 1
        rel = str(path.relative_to(CONFIG))
        category = classify_reference(Path(rel))
        for line_no, line in enumerate(lines, 1):
            matches = list(ENTITY_RE.finditer(line))
            for match in matches:
                entity_id = match.group(0)
                start = max(0, match.start() - 100)
                end = min(len(line), match.end() + 100)
                evidence = {"line": line_no, "text": line[start:end].strip()[:500], "full_text": line[:10000], "full_text_truncated": len(line) > 10000}
                if entity_id in known:
                    key = (entity_id, rel)
                    entry = references.setdefault(key, {
                        "entity_id": entity_id, "file": rel, "category": category,
                        "count": 0, "lines": [], "matches": []
                    })
                    entry["count"] += 1
                    if category in {"Dashboard", "Active configuration"}:
                        # Repair plans must retain every active source line, not just a preview sample.
                        if line_no not in entry["lines"]:
                            entry["lines"].append(line_no)
                            entry["matches"].append(evidence)
                    elif len(entry["matches"]) < 30 and line_no not in entry["lines"]:
                        entry["lines"].append(line_no)
                        entry["matches"].append(evidence)
                elif entity_id.split(".", 1)[0] not in IGNORED_STALE_DOMAINS and entity_id.split(".", 1)[0] in known_domains and (
                    category == "Historical" or ENTITY_CONTEXT_RE.search(line)
                ):
                    key = (entity_id, rel)
                    entry = candidates.setdefault(key, {
                        "entity_id": entity_id, "file": rel, "category": category,
                        "count": 0, "lines": [], "matches": [],
                        "confidence": "candidate",
                        "reason": (
                            "Historical record contains an entity-like ID absent from the current registry."
                            if category == "Historical"
                            else "Entity-like ID in an entity-reference context, but absent from the current registry."
                        )
                    })
                    entry["count"] += 1
                    if len(entry["matches"]) < 30:
                        entry["lines"].append(line_no)
                        entry["matches"].append(evidence)

    results = sorted(references.values(), key=lambda x: (x["entity_id"], x["file"]))
    candidate_categories = {}
    for item in candidates.values():
        candidate_categories.setdefault(item["entity_id"], set()).add(item["category"])
    def candidate_priority(entity_id):
        categories = candidate_categories.get(entity_id, set())
        if categories.intersection({"Dashboard", "Active configuration"}):
            return 0
        if "Needs review" in categories:
            return 1
        return 2
    stale = sorted(candidates.values(), key=lambda x: (candidate_priority(x["entity_id"]), x["entity_id"], x["file"]))
    for item in stale:
        rank = candidate_priority(item["entity_id"])
        item["priority"] = "Active/dashboard evidence" if rank == 0 else "Needs review evidence" if rank == 1 else "Historical-only evidence"
    naming_findings = naming_audit(registry)
    usage_by_entity = {}
    for reference in results:
        usage_by_entity.setdefault(reference["entity_id"], []).append(reference)
    for finding in naming_findings:
        finding["usage"] = usage_by_entity.get(finding["entity_id"], [])
    readded_pairs, readded_note = detect_readded_pairs(registry, results, fetch_live_states(), load_device_names())
    clean_name_suggestions = build_clean_name_suggestions(registry, results)
    referenced_entities = {item["entity_id"] for item in results}
    active_entities = {item["entity_id"] for item in results if item["category"] in {"Active configuration", "Dashboard"}}
    review_entities = {item["entity_id"] for item in results if item["category"] == "Needs review"}
    historical_entities = {item["entity_id"] for item in results if item["category"] == "Historical"}
    historical_only_entities = historical_entities - active_entities - review_entities
    review_only_entities = review_entities - active_entities - historical_entities
    active_reference_count = len(active_entities)
    unreferenced_count = len(set(entity_ids) - referenced_entities)
    reference_record_count = len(results)
    reference_hit_count = sum(item["count"] for item in results)
    duration = round(time.monotonic() - started, 3)
    scanned_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    category_hits = {
        category: sum(item["count"] for item in results if item["category"] == category)
        for category in ("Active configuration", "Dashboard", "Historical", "Needs review")
    }
    report = {
        "ok": True,
        "read_only": True,
        "entities_found": len(entity_ids),
        "registered_entities": len(entity_ids),
        "files_scanned": scanned,
        "entities_with_references": len(referenced_entities),
        "entities_with_any_references": len(referenced_entities),
        "entities_with_active_references": active_reference_count,
        "entities_with_historical_only_references": len(historical_only_entities),
        "entities_with_needs_review_only_references": len(review_only_entities),
        "unreferenced_entities": unreferenced_count,
        "reference_records": reference_record_count,
        "reference_hits": reference_hit_count,
        "scan_duration_seconds": duration,
        "last_scanned_at": scanned_at,
        "category_hits": category_hits,
        "results": results,
        "potentially_stale": stale,
        "potentially_stale_entities": len({item["entity_id"] for item in stale}),
        "potentially_stale_hits": sum(item["count"] for item in stale),
        "naming_findings": naming_findings,
        "naming_findings_count": len(naming_findings),
        "readded_pairs": readded_pairs,
        "readded_pairs_count": len(readded_pairs),
        "readded_detection_note": readded_note,
        "clean_name_suggestions": clean_name_suggestions,
        "clean_name_suggestions_count": len(clean_name_suggestions),
        "audit_note": "Potentially stale entries are candidates for review, not confirmed broken references."
    }
    print(
        "HA Entity Sync: scan complete "
        f"version={VERSION} registered={len(entity_ids)} active_referenced={active_reference_count} "
        f"historical_only={len(historical_only_entities)} unreferenced={unreferenced_count} "
        f"reference_records={reference_record_count} reference_hits={reference_hit_count} "
        f"files={scanned} stale_candidates={len({item['entity_id'] for item in stale})} duration={duration:.3f}s",
        flush=True,
    )
    return report


def get_scan_report(force=False):
    now = time.monotonic()
    cached = _scan_cache["report"]
    if not force and cached is not None and now - _scan_cache["created"] < SCAN_CACHE_SECONDS:
        return {**cached, "cache_hit": True}
    with _scan_lock:
        now = time.monotonic()
        cached = _scan_cache["report"]
        if not force and cached is not None and now - _scan_cache["created"] < SCAN_CACHE_SECONDS:
            return {**cached, "cache_hit": True}
        report = scan()
        if report.get("ok"):
            _scan_cache["report"] = report
            _scan_cache["created"] = time.monotonic()
        return {**report, "cache_hit": False}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/scan":
            force = parse_qs(urlparse(self.path).query).get("force", ["0"])[0] == "1"
            payload = json.dumps(get_scan_report(force=force)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif route == "/api/file":
            requested = parse_qs(urlparse(self.path).query).get("path", [""])[0]
            try:
                file_data = read_scoped_file(requested)
            except ValueError as err:
                self.send_error(400, str(err))
                return
            except (OSError, UnicodeError) as err:
                self.send_error(404, str(err))
                return
            payload = json.dumps({"ok": True, **file_data}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif route in ("/", "/index.html"):
            page = Path("/app/index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        elif route == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_error(404)

    def do_POST(self):
        self.send_error(405, "Read-only application: POST is disabled")

    def log_message(self, fmt, *args):
        print("HA Entity Sync:", fmt % args)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"HA Entity Sync version {VERSION} starting on 0.0.0.0:{PORT} (read-only; scan cache {SCAN_CACHE_SECONDS}s)", flush=True)
    print(f"HA Entity Sync listening on 0.0.0.0:{PORT}", flush=True)
    print("HA Entity Sync: performing startup scan", flush=True)
    get_scan_report(force=True)
    server.serve_forever()
