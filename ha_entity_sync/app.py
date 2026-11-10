#!/usr/bin/env python3
"""Read-only Home Assistant entity reference auditor."""
import json
import os
import re
import threading
import time
import urllib.request
import urllib.error
from collections import Counter
from datetime import datetime, timezone

from recovery import RecoveryError, load_audit, recover_entity_pair, rollback_entity_recovery
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CONFIG = Path("/homeassistant")
REGISTRY = CONFIG / ".storage" / "core.entity_registry"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_SCAN_FILES = 5000
MAX_SCAN_BYTES = 100 * 1024 * 1024
VERSION = "0.2.15"
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
    if not safe_config_file(REGISTRY) or not REGISTRY.is_file():
        raise OSError("Entity registry is missing, linked, or outside the configuration mount")
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


def safe_config_file(path):
    """Reject symlinks and files resolving outside the mounted HA config."""
    try:
        if path.is_symlink():
            return False
        return path.resolve(strict=True).is_relative_to(CONFIG.resolve())
    except (OSError, RuntimeError):
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
                    if safe_config_file(path) and path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
                        yield path
                except OSError:
                    continue
            continue

        dirs[:] = sorted(
            d for d in dirs
            if d.lower() not in SKIP_DIRS
            and not is_backup_path(relative_root / d)
            and not (root_path / d).is_symlink()
        )

        for name in sorted(names):
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
                if safe_config_file(path) and path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
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
    """Suggest only a clear adjacent-repeat cleanup; never strip numeric suffixes."""
    domain, object_id = entity_id.split(".", 1)
    words = object_id.split("_")
    for width in range(len(words) // 2, 0, -1):
        if words[:width] == words[width:2 * width]:
            proposed_words = words[:width] + words[2 * width:]
            candidate = domain + "." + "_".join(proposed_words)
            if proposed_words and candidate != entity_id and candidate not in occupied:
                return [candidate]
            return []
    return []

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
        print("HA Entity Sync: live-state API unavailable; SUPERVISOR_TOKEN is missing", flush=True)
        return None
    endpoint = "http://supervisor/core/api/states"
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            status = response.status if hasattr(response, "status") else response.getcode()
            print(f"HA Entity Sync: GET {endpoint} returned HTTP {status}", flush=True)
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list):
            print("HA Entity Sync: live-state API returned an unexpected JSON payload", flush=True)
            return None
        return {item.get("entity_id"): item for item in payload if isinstance(item, dict) and item.get("entity_id")}
    except urllib.error.HTTPError as err:
        print(f"HA Entity Sync: GET {endpoint} failed with HTTP {err.code}", flush=True)
        return None
    except urllib.error.URLError as err:
        print(f"HA Entity Sync: GET {endpoint} failed; network reason: {err.reason}", flush=True)
        return None
    except (OSError, ValueError, TypeError) as err:
        print(f"HA Entity Sync: GET {endpoint} failed: {type(err).__name__}: {err}", flush=True)
        return None


def load_device_names():
    """Read device labels only as corroborating identity metadata."""
    path = CONFIG / ".storage" / "core.device_registry"
    try:
        if not safe_config_file(path) or not path.is_file():
            return {}
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


def evaluate_replacement_identity(old, replacement):
    """Return transparent identity evidence; names never establish identity."""
    evidence = []
    conflicts = []

    def compare(field, label, strong=False):
        left, right = old.get(field), replacement.get(field)
        if left and right and left == right:
            evidence.append({
                "type": label,
                "status": "match",
                "strength": "strong" if strong else "supporting",
                "detail": f"Both registry entries have the same {label.lower()}.",
            })
            return True
        if left and right and left != right:
            conflicts.append({
                "type": label,
                "status": "conflict",
                "strength": "blocking",
                "detail": f"The {label.lower()} differs between the original and replacement.",
            })
        elif not left or not right:
            evidence.append({
                "type": label,
                "status": "not_available",
                "strength": "unknown",
                "detail": f"{label} could not be compared because metadata is missing.",
            })
        return False

    unique_match = compare("unique_id", "Integration unique ID", True)
    device_match = compare("device_id", "Home Assistant device ID", True)
    platform_match = compare("platform", "Integration/platform", True)
    config_match = compare("config_entry_id", "Config entry ID", True)

    # A config-entry match is strong only when its integration also matches.
    config_pair_match = config_match and platform_match
    strong_match = platform_match and (unique_match or device_match or config_pair_match)
    if old.get("platform") and replacement.get("platform") and old.get("platform") == replacement.get("platform"):
        evidence.append({
            "type": "Platform compatibility",
            "status": "match",
            "strength": "supporting",
            "detail": "Both entries belong to the same integration/platform.",
        })

    confidence = "high" if strong_match and not conflicts else "none"
    reasons = []
    if unique_match:
        reasons.append("Same integration unique_id")
    if device_match:
        reasons.append("Same registered device ID")
    if config_pair_match:
        reasons.append("Same integration and config entry")
    return {
        "confidence": confidence,
        "identity_reason": "; ".join(reasons) if confidence == "high" else "",
        "identity_evidence": evidence,
        "identity_conflicts": conflicts,
        "eligible": confidence == "high",
    }


def dashboard_route_index():
    """Map Lovelace storage files to their Home Assistant dashboard URL paths."""
    routes = {".storage/lovelace": "/lovelace"}
    registry_path = CONFIG / ".storage" / "lovelace_dashboards"
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        dashboards = payload.get("data", {}).get("dashboards", [])
        for dashboard in dashboards:
            if not isinstance(dashboard, dict):
                continue
            dashboard_id = dashboard.get("id")
            url_path = dashboard.get("url_path")
            if dashboard_id and isinstance(url_path, str) and re.fullmatch(r"[A-Za-z0-9_-]+", url_path):
                routes[f".storage/lovelace.{dashboard_id}"] = "/" + url_path
    except (OSError, ValueError, TypeError):
        # Missing dashboard metadata is normal on older/default-only installations.
        pass
    return routes


def detect_readded_pairs(registry, references, live_states, device_names):
    """Find suffix replacement pairs only when HA state and identity evidence agree."""
    if live_states is None:
        return [], "Live Home Assistant state access is unavailable; re-add detection is not confirmed."
    by_id = {item["entity_id"]: item for item in registry}
    refs_by_id = {}
    routes = dashboard_route_index()
    for reference in references:
        enriched = dict(reference)
        if reference.get("category") == "Dashboard":
            enriched["dashboard_url"] = routes.get(reference.get("file"))
            enriched["dashboard_preview_available"] = bool(enriched["dashboard_url"])
        refs_by_id.setdefault(reference["entity_id"], []).append(enriched)
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
        old_unique = old.get("unique_id")
        new_unique = replacement.get("unique_id")

        # Names and suffix similarity are never identity proof. Require a
        # strong identity match and suppress candidates when registry metadata conflicts.
        identity = evaluate_replacement_identity(old, replacement)
        if not identity["eligible"]:
            continue
        confidence = identity["confidence"]
        identity_reason = identity["identity_reason"]

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
            "identity_evidence": identity["identity_evidence"],
            "identity_conflicts": identity["identity_conflicts"],
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
    suggested_targets = set()
    for item in registry:
        entity_id = item["entity_id"]
        domain, object_id = entity_id.split(".", 1)
        words = object_id.split("_")
        candidate_words = words[:]
        reasons = []
        repeated_rule = False
        suffix_rule = False

        # Primary rule: collapse one immediately repeated adjacent word sequence.
        for width in range(len(candidate_words) // 2, 0, -1):
            if candidate_words[:width] == candidate_words[width:2 * width]:
                candidate_words = candidate_words[:width] + candidate_words[2 * width:]
                reasons.append("Collapsed an immediately repeated name sequence")
                repeated_rule = True
                break

        # A trailing _2/_3 may be an HA re-add suffix, but may also identify a
        # real numbered device. Keep this as a low-confidence advisory only.
        if candidate_words and candidate_words[-1] in {"2", "3"}:
            removed_suffix = candidate_words.pop()
            reasons.append(f"Possible duplicate suffix _{removed_suffix}; verify this is not an intentional numbered device")
            suffix_rule = True

        # Integration-prefix removal is disabled: it can erase useful identity
        # (for example sun_next_dawn or wiz_rgbw_tunable).
        if candidate_words == words:
            continue
        proposed = domain + "." + "_".join(candidate_words)
        if proposed == domain + ".":
            continue

        registered_collision = proposed in occupied and proposed != entity_id
        batch_collision = proposed in suggested_targets
        collision = registered_collision or batch_collision
        if batch_collision:
            reasons.append("Another entity in this scan already suggests the same target ID")
        confidence = "low" if collision or suffix_rule else "high" if repeated_rule else "low"
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
        suggested_targets.add(proposed)
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
    scanned_bytes = 0
    scan_warnings = []

    for path in candidate_files():
        if scanned >= MAX_SCAN_FILES:
            scan_warnings.append(
                f"Scan stopped at the safety limit of {MAX_SCAN_FILES} files; remaining files were not checked."
            )
            break
        try:
            file_size = path.stat().st_size
            if scanned_bytes + file_size > MAX_SCAN_BYTES:
                scan_warnings.append(
                    f"Scan stopped at the safety limit of {MAX_SCAN_BYTES // (1024 * 1024)} MB; remaining files were not checked."
                )
                break
            if not safe_config_file(path):
                scan_warnings.append(f"Skipped a symlink or out-of-scope file: {path.relative_to(CONFIG)}")
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        scanned += 1
        scanned_bytes += file_size
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
        "bytes_scanned": scanned_bytes,
        "scan_warnings": scan_warnings,
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
        "recovery_history": load_audit()[-20:],
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
            report = get_scan_report(force=force)
            payload = json.dumps(report).encode()
            self.send_response(200 if report.get("ok") else 503)
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
            ready = safe_config_file(REGISTRY) and REGISTRY.is_file()
            payload = json.dumps({
                "ok": ready,
                "version": VERSION,
                "registry_available": ready,
                "scan_read_only": True,
                "recovery_enabled": True,
            }).encode()
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_error(404)

    def do_POST(self):
        route = urlparse(self.path).path
        if route not in {"/api/recover", "/api/rollback"}:
            self.send_error(405, "Only approved entity recovery actions are accepted")
            return
        origin = self.headers.get("Origin")
        host_values = [
            self.headers.get("Host"),
            self.headers.get("X-Forwarded-Host"),
            self.headers.get("X-Original-Host"),
        ]
        allowed_hosts = {
            urlparse("//" + value).hostname
            for value in host_values if value
        }
        origin_host = urlparse(origin).hostname if origin else None
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if (
            not origin_host
            or origin_host not in allowed_hosts
            or (fetch_site and fetch_site not in {"same-origin", "same-site"})
        ):
            self.send_error(403, "Recovery requests must originate from this Home Assistant ingress page")
            return
        if self.headers.get("X-HA-Entity-Sync-Action") != "approve":
            self.send_error(403, "Explicit recovery approval header is required")
            return
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            self.send_error(415, "JSON request body required")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid request length")
            return
        if length <= 0 or length > 16384:
            self.send_error(413, "Invalid recovery request size")
            return
        try:
            request_data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(request_data, dict):
                raise ValueError("Request must be a JSON object")
            if route == "/api/recover":
                result = recover_entity_pair(
                    str(request_data.get("old_entity_id") or ""),
                    str(request_data.get("replacement_entity_id") or ""),
                    fetch_live_states,
                )
            else:
                result = rollback_entity_recovery(
                    str(request_data.get("record_id") or ""),
                    fetch_live_states,
                )
            status = 200 if result.get("ok") else 409
        except (ValueError, UnicodeError) as err:
            result = {"ok": False, "status": "invalid_request", "error": str(err)}
            status = 400
        except RecoveryError as err:
            result = {"ok": False, "status": "blocked", "error": str(err)}
            status = 409
        except Exception as err:
            result = {"ok": False, "status": "error", "error": f"{type(err).__name__}: {err}"}
            status = 500
        payload = json.dumps(result).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print("HA Entity Sync:", fmt % args)


class HAEntitySyncHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


if __name__ == "__main__":
    server = HAEntitySyncHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"HA Entity Sync version {VERSION} starting on 0.0.0.0:{PORT} (read-only; scan cache {SCAN_CACHE_SECONDS}s)", flush=True)
    print(f"HA Entity Sync listening on 0.0.0.0:{PORT}", flush=True)
    print("HA Entity Sync: ready; scans start when the panel is opened", flush=True)
    server.serve_forever()
