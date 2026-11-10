#!/usr/bin/env python3
"""Read-only Home Assistant entity reference auditor."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

CONFIG = Path("/homeassistant")
REGISTRY = CONFIG / ".storage" / "core.entity_registry"
MAX_FILE_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {
    ".git", "backups", "backup", "deps", "tts", "__pycache__",
    "www", "media", "share", "blueprints_cache"
}
SKIP_NAMES = {
    "secrets.yaml", "secrets.yml", "home-assistant_v2.db",
    "home-assistant_v2.db-wal", "home-assistant_v2.db-shm",
    "core.config_entries"
}
ALLOWED_SUFFIXES = {
    ".yaml", ".yml", ".json", ".json5", ".jinja", ".jinja2",
    ".py", ".js", ".ts", ".md"
}
ENTITY_RE = re.compile(r"(?<![A-Za-z0-9_])[a-z0-9_]+\.[a-z0-9_]+(?![A-Za-z0-9_])")


def load_entities():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entities = data.get("data", {}).get("entities", [])
    return sorted({
        item.get("entity_id")
        for item in entities
        if isinstance(item, dict) and item.get("entity_id")
    })


def candidate_files():
    for root, dirs, names in os.walk(CONFIG):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith("backup")]
        for name in names:
            if name in SKIP_NAMES or name.lower().startswith(("secret", "auth", "token")):
                continue
            path = Path(root) / name
            rel = path.relative_to(CONFIG)
            # Include extensionless Home Assistant storage JSON, but not secrets/auth.
            if path.suffix.lower() not in ALLOWED_SUFFIXES and rel.parts[0] != ".storage":
                continue
            if path == REGISTRY:
                continue
            try:
                if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
                    yield path
            except OSError:
                continue


def classify_reference(path):
    """Classify a reference by the role of the file containing it."""
    rel = path.as_posix()
    name = path.name.lower()

    if rel == ".storage/core.restore_state" or name.startswith("trace.") or rel.startswith(".storage/trace"):
        return "Historical"
    if rel.startswith(".storage/lovelace.") or rel.startswith(".storage/lovelace/"):
        return "Dashboard"
    if path.suffix.lower() in {".yaml", ".yml", ".jinja", ".jinja2"}:
        return "Active configuration"
    return "Needs review"


def scan():
    if not REGISTRY.exists():
        return {"ok": False, "error": "Entity registry not found at .storage/core.entity_registry"}
    try:
        entity_ids = load_entities()
    except Exception as err:
        return {"ok": False, "error": f"Could not read entity registry: {type(err).__name__}: {err}"}

    known = set(entity_ids)
    results = []
    scanned = 0
    for path in candidate_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        scanned += 1
        rel = str(path.relative_to(CONFIG))
        found = {}
        for line_no, line in enumerate(lines, 1):
            for match in ENTITY_RE.finditer(line):
                entity_id = match.group(0)
                if entity_id in known:
                    found.setdefault(entity_id, []).append(line_no)
        for entity_id, line_numbers in found.items():
            results.append({
                "entity_id": entity_id,
                "file": rel,
                "category": classify_reference(Path(rel)),
                "count": len(line_numbers),
                "lines": line_numbers[:30]
            })

    results.sort(key=lambda item: (item["entity_id"], item["file"]))
    by_entity = {item["entity_id"] for item in results}
    category_hits = {category: sum(item["count"] for item in results if item["category"] == category)
                     for category in ("Active configuration", "Dashboard", "Historical", "Needs review")}
    return {
        "ok": True,
        "read_only": True,
        "entities_found": len(entity_ids),
        "files_scanned": scanned,
        "entities_with_references": len(by_entity),
        "reference_hits": sum(item["count"] for item in results),
        "category_hits": category_hits,
        "results": results,
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
