import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha_entity_sync"))
from dependency_graph import build_dependency_graph


class DependencyGraphTests(unittest.TestCase):
    def test_normalizes_yaml_dashboard_history_and_person_edges(self):
        refs = [
            {
                "entity_id": "sensor.room_temp", "file": "automations.yaml",
                "category": "Active configuration", "matches": [
                    {"line": 7, "full_text": "        entity_id: sensor.room_temp"}
                ]
            },
            {
                "entity_id": "sensor.room_temp", "file": ".storage/lovelace.dashboard",
                "category": "Dashboard", "matches": [
                    {"line": 1, "full_text": '"entity": "sensor.room_temp"'}
                ]
            },
            {
                "entity_id": "sensor.room_temp", "file": ".storage/core.restore_state",
                "category": "Historical", "matches": [
                    {"line": 2, "full_text": '"entity_id": "sensor.room_temp"'}
                ]
            },
        ]
        stale = [{
            "entity_id": "sensor.old_tracker", "file": "packages/people.yaml",
            "category": "Active configuration", "matches": [
                {"line": 12, "full_text": "device_tracker: sensor.old_tracker"}
            ]
        }]
        graph = build_dependency_graph(
            refs, stale,
            dashboard_health=[{
                "entity_id": "sensor.room_temp", "file": ".storage/lovelace.dashboard",
                "health_status": "available",
                "card_context": [{"card_type": "sensor", "card_title": "Room"}],
                "dashboard_url": "/energy",
            }],
            person_tracker_findings=[{
                "tracker_entity_id": "device_tracker.phone",
                "person_id": "alice", "person_name": "Alice",
                "status": "missing", "replacement_suggestion": {"confidence": "ambiguous"},
            }],
            source_texts={
                "automations.yaml": "automation:\n  - id: room_automation\n    action:\n      - service: light.turn_on\n        target:\n          entity_id: sensor.room_temp"
            },
        )
        self.assertTrue(graph["read_only"])
        self.assertEqual(graph["schema_version"], 1)
        self.assertEqual(graph["node_count"], 3)
        room_edges = [e for e in graph["edges"] if e["entity_id"] == "sensor.room_temp"]
        self.assertEqual(len(room_edges), 3)
        automation = next(e for e in room_edges if e["source_type"] == "automation")
        self.assertEqual(automation["parent_object"]["value"], "room_automation")
        dashboard = next(e for e in room_edges if e["source_type"] == "dashboard")
        self.assertEqual(dashboard["health_status"], "available")
        self.assertEqual(dashboard["card_context"][0]["card_title"], "Room")
        self.assertTrue(next(e for e in room_edges if e["historical"])["historical"])
        person = next(e for e in graph["edges"] if e["source_type"] == "person_assignment")
        self.assertEqual(person["parent_object"]["value"], "Alice")
        self.assertEqual(person["replacement_suggestion"]["confidence"], "ambiguous")

    def test_usage_roles_keep_historical_and_dashboard_distinct(self):
        graph = build_dependency_graph([
            {"entity_id": "light.kitchen", "file": ".storage/lovelace", "category": "Dashboard",
             "matches": [{"line": 3, "full_text": '"entity": "light.kitchen"'}]},
            {"entity_id": "sensor.old", "file": ".storage/core.restore_state", "category": "Historical",
             "matches": [{"line": 4, "full_text": '"entity_id": "sensor.old"'}]},
        ], [])
        roles = {edge["usage_role"] for edge in graph["edges"]}
        self.assertEqual(roles, {"dashboard_card", "historical_record"})
        self.assertEqual(graph["nodes"][0]["historical_reference_count"], 1)


if __name__ == "__main__":
    unittest.main()
