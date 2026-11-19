"""Normalized, read-only dependency graph built from scanner evidence.

This layer deliberately does not parse or rewrite Home Assistant configuration.
It normalizes already-discovered references and preserves their source evidence.
"""
import re
from pathlib import PurePosixPath

YAML_OBJECT_RE = re.compile(r"^\s*-\s*(id|alias|name):\s*(.*?)\s*$", re.IGNORECASE)


def _source_type(path, category):
    normalized = str(path or "").replace("\\", "/")
    name = PurePosixPath(normalized).name.lower()
    if category == "Historical":
        return "historical"
    if normalized.startswith(".storage/lovelace"):
        return "dashboard"
    if name in {"automations.yaml", "automation.yaml"}:
        return "automation"
    if name in {"scripts.yaml", "script.yaml"}:
        return "script"
    if name in {"scenes.yaml", "scene.yaml"}:
        return "scene"
    if "group" in name:
        return "group"
    if "template" in name or name.endswith((".jinja", ".jinja2")):
        return "template"
    if name in {"configuration.yaml", "helpers.yaml"}:
        return "configuration"
    if PurePosixPath(normalized).suffix.lower() in {".yaml", ".yml"}:
        return "yaml_configuration"
    if category == "Dashboard":
        return "dashboard"
    return "configuration"


def _usage_role(source_type, line):
    text = str(line or "")
    lower = text.lower()
    if source_type == "historical":
        return "historical_record"
    if source_type == "dashboard":
        return "dashboard_card"
    if re.search(r"\btrigger\b|\bplatform\s*:", lower):
        return "trigger"
    if re.search(r"\bcondition\b", lower):
        return "condition"
    if re.search(r"\baction\b|\bservice\s*:|\bperform-action\b", lower):
        return "action"
    if re.search(r"\btarget\s*:", lower):
        return "target"
    if re.search(r"\b(states|is_state|is_state_attr|has_value|expand)\s*\(", lower) or "{{" in lower or "{%" in lower:
        return "template"
    if re.search(r"\b(device_trackers|device_tracker)\b", lower):
        return "person_tracker_or_tracker"
    if re.search(r"\b(entities|entity_id|members)\s*:", lower):
        return "entity_reference"
    if source_type == "automation":
        return "automation_reference"
    if source_type == "script":
        return "script_reference"
    if source_type == "scene":
        return "scene_reference"
    if source_type == "group":
        return "group_member"
    return "configuration_reference"


def _parent_object(path, line_number, source_text):
    """Return nearest explicit YAML object label when safely identifiable."""
    if not source_text or not line_number:
        return None
    lines = source_text.splitlines()
    if line_number > len(lines):
        return None
    for index in range(line_number - 1, max(-1, line_number - 151), -1):
        match = YAML_OBJECT_RE.match(lines[index])
        if match:
            value = match.group(2).strip().strip(chr(34) + chr(39))[:160]
            if value:
                return {"key": match.group(1).lower(), "value": value}
    return None


def build_dependency_graph(references, stale_references, dashboard_health=None,
                           person_tracker_findings=None, source_texts=None):
    """Build stable entity nodes and occurrence-level dependency edges."""
    source_texts = source_texts or {}
    dashboard_health = dashboard_health or []
    person_tracker_findings = person_tracker_findings or []
    health_by_key = {
        (item.get("entity_id"), item.get("file")): item
        for item in dashboard_health
    }
    nodes = {}
    edges = []
    seen_edges = set()

    def add_reference(reference, stale=False):
        entity_id = reference.get("entity_id")
        if not entity_id:
            return
        category = reference.get("category") or "Needs review"
        path = reference.get("file") or ""
        source_type = _source_type(path, category)
        node = nodes.setdefault(entity_id, {
            "entity_id": entity_id,
            "registered": not stale,
            "reference_count": 0,
            "active_reference_count": 0,
            "historical_reference_count": 0,
            "source_types": [],
        })
        node["registered"] = node["registered"] or not stale
        if source_type not in node["source_types"]:
            node["source_types"].append(source_type)
        matches = reference.get("matches") or []
        if not matches:
            matches = [{"line": line} for line in reference.get("lines", [])]
        for match in matches:
            line_number = match.get("line")
            full_text = match.get("full_text") or match.get("text") or ""
            role = _usage_role(source_type, full_text)
            edge_key = (entity_id, path, line_number, role, category)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edge = {
                "entity_id": entity_id,
                "source_type": source_type,
                "source_path": path,
                "line": line_number,
                "category": category,
                "usage_role": role,
                "parent_object": _parent_object(path, line_number, source_texts.get(path)),
                "active": category in {"Dashboard", "Active configuration"},
                "historical": category == "Historical",
                "stale_candidate": bool(stale),
            }
            health = health_by_key.get((entity_id, path))
            if health:
                edge["health_status"] = health.get("health_status")
                edge["card_context"] = health.get("card_context", [])
                edge["dashboard_url"] = health.get("dashboard_url")
            edges.append(edge)
            node["reference_count"] += 1
            if edge["active"]:
                node["active_reference_count"] += 1
            if edge["historical"]:
                node["historical_reference_count"] += 1

    for item in references or []:
        add_reference(item, stale=False)
    for item in stale_references or []:
        add_reference(item, stale=True)

    for finding in person_tracker_findings:
        entity_id = finding.get("tracker_entity_id")
        if not entity_id:
            continue
        node = nodes.setdefault(entity_id, {
            "entity_id": entity_id,
            "registered": finding.get("status") != "missing",
            "reference_count": 0,
            "active_reference_count": 0,
            "historical_reference_count": 0,
            "source_types": [],
        })
        if "person_assignment" not in node["source_types"]:
            node["source_types"].append("person_assignment")
        edges.append({
            "entity_id": entity_id,
            "source_type": "person_assignment",
            "source_path": ".storage/person",
            "line": None,
            "category": "Person assignment",
            "usage_role": "person_device_tracker_assignment",
            "parent_object": {
                "key": "person",
                "value": finding.get("person_name") or finding.get("person_id") or "Unknown",
            },
            "active": True,
            "historical": False,
            "stale_candidate": finding.get("status") == "missing",
            "health_status": finding.get("status"),
            "replacement_suggestion": finding.get("replacement_suggestion"),
        })
        node["reference_count"] += 1
        node["active_reference_count"] += 1

    edges.sort(key=lambda item: (
        item["entity_id"], item["source_path"], item["line"] or 0,
        item["usage_role"]
    ))
    for node in nodes.values():
        node["source_types"].sort()
    ordered_nodes = sorted(nodes.values(), key=lambda item: item["entity_id"])
    return {
        "schema_version": 1,
        "read_only": True,
        "node_count": len(ordered_nodes),
        "edge_count": len(edges),
        "nodes": ordered_nodes,
        "edges": edges,
    }
