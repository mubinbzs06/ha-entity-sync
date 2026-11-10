#!/usr/bin/env python3
"""Read-only Home Assistant entity reference auditor."""
import json
import os
import re
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

CONFIG = Path("/homeassistant")
REGISTRY = CONFIG / ".storage" / "core.entity_registry"
MAX_FILE_BYTES = 5 * 1024 * 1024
# Search user-maintained configuration, not HA internals or app implementation.
# Dashboard storage is the one deliberate exception inside .storage.
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
                if not name.startswith("lovelace"):
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


def classify_reference(path):
    rel = path.as_posix()
    name = path.name.lower()
    if rel.startswith(".storage/lovelace"):
        return "Dashboard"
    if path.suffix.lower() in {".yaml", ".yml", ".jinja", ".jinja2"}:
        return "Active configuration"
    return "Needs review"


def naming_audit(registry):
    ids = {item["entity_id"] for item in registry}
    findings = []
    for item in registry:
        entity_id = item["entity_id"]
        domain, object_id = entity_id.split(".", 1)
        issues = []
        suggestion = object_id
        suffix = NUMERIC_SUFFIX_RE.search(object_id)
        if suffix:
            base_object = object_id[:suffix.start()]
            base_id = domain + "." + base_object
            issues.append({
                "type": "Numeric suffix",
                "detail": "A trailing _" + suffix.group(1) + " may indicate a duplicate or an earlier naming collision.",
                "matching_base": base_id if base_id in ids else None
            })
            if base_id in ids:
                suggestion = base_object
        words = [word for word in object_id.split("_") if word]
        repeated = sorted({word for word, count in Counter(words).items() if count > 1})
        if repeated:
            issues.append({
                "type": "Repeated words",
                "detail": "Repeated token(s): " + ", ".join(repeated)
            })
            compact = []
            for word in words:
                if word not in compact:
                    compact.append(word)
            suggestion = "_".join(compact)
        if issues:
            findings.append({
                "entity_id": entity_id,
                "name": item.get("name") or item.get("original_name") or "",
                "issues": issues,
                "suggested_entity_id": domain + "." + suggestion,
            })
    return findings


def scan():
    if not REGISTRY.exists():
        return {"ok": False, "error": "Entity registry not found at .storage/core.entity_registry"}
    try:
        registry = load_registry()
    except Exception as err:
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
                evidence = {"line": line_no, "text": line.strip()[:500]}
                if entity_id in known:
                    key = (entity_id, rel)
                    entry = references.setdefault(key, {
                        "entity_id": entity_id, "file": rel, "category": category,
                        "count": 0, "lines": [], "matches": []
                    })
                    entry["count"] += 1
                    if len(entry["matches"]) < 30:
                        entry["lines"].append(line_no)
                        entry["matches"].append(evidence)
                elif entity_id.split(".", 1)[0] in known_domains and ENTITY_CONTEXT_RE.search(line):
                    key = (entity_id, rel)
                    entry = candidates.setdefault(key, {
                        "entity_id": entity_id, "file": rel, "category": category,
                        "count": 0, "lines": [], "matches": [],
                        "confidence": "candidate",
                        "reason": "Entity-like ID in an entity-reference context, but absent from the current registry."
                    })
                    entry["count"] += 1
                    if len(entry["matches"]) < 30:
                        entry["lines"].append(line_no)
                        entry["matches"].append(evidence)

    results = sorted(references.values(), key=lambda x: (x["entity_id"], x["file"]))
    stale = sorted(candidates.values(), key=lambda x: (x["entity_id"], x["file"]))
    by_entity = {item["entity_id"] for item in results}
    category_hits = {
        category: sum(item["count"] for item in results if item["category"] == category)
        for category in ("Active configuration", "Dashboard", "Historical", "Needs review")
    }
    return {
        "ok": True,
        "read_only": True,
        "entities_found": len(entity_ids),
        "files_scanned": scanned,
        "entities_with_references": len(by_entity),
        "reference_hits": sum(item["count"] for item in results),
        "category_hits": category_hits,
        "results": results,
        "potentially_stale": stale,
        "potentially_stale_entities": len({item["entity_id"] for item in stale}),
        "potentially_stale_hits": sum(item["count"] for item in stale),
        "naming_findings": naming_audit(registry),
        "naming_findings_count": len(naming_audit(registry)),
        "audit_note": "Potentially stale entries are candidates for review, not confirmed broken references."
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/scan":
            payload = json.dumps(scan()).encode()
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
    print("HA Entity Sync listening on 0.0.0.0:8099 (read-only)")
    ThreadingHTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
