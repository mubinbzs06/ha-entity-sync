import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha_entity_sync"))
import app


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".storage").mkdir()
        registry = {
            "data": {
                "entities": [
                    {"entity_id": "sensor.known"},
                    {"entity_id": "sensor.fan_speed"},
                    {"entity_id": "sensor.fan_speed_2"},
                    {"entity_id": "sensor.room_room_temperature"},
                ]
            }
        }
        (self.root / ".storage" / "core.entity_registry").write_text(
            json.dumps(registry), encoding="utf-8"
        )
        (self.root / "automations.yaml").write_text(
            "automation:\n"
            "  - action:\n"
            "      - service: light.turn_on\n"
            "        target:\n"
            "          entity_id: sensor.missing_entity\n"
            "      - service: homeassistant.update_entity\n"
            "        data:\n"
            "          entity_id: sensor.known\n"
            "      - service: custom_domain.do_thing\n"
            "        data:\n"
            "          entity_id: custom_domain.unknown\n",
            encoding="utf-8",
        )
        self.config_patch = patch.object(app, "CONFIG", self.root)
        self.registry_patch = patch.object(
            app, "REGISTRY", self.root / ".storage" / "core.entity_registry"
        )
        self.config_patch.start()
        self.registry_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.registry_patch.stop()
        self.temp.cleanup()

    def test_registered_reference_contains_line_evidence(self):
        report = app.scan()
        self.assertTrue(report["ok"])
        known = [x for x in report["results"] if x["entity_id"] == "sensor.known"]
        self.assertEqual(len(known), 1)
        self.assertEqual(known[0]["matches"][0]["line"], 8)
        self.assertIn("entity_id: sensor.known", known[0]["matches"][0]["text"])

    def test_unregistered_common_domain_is_only_a_candidate(self):
        report = app.scan()
        ids = {x["entity_id"] for x in report["potentially_stale"]}
        self.assertIn("sensor.missing_entity", ids)
        self.assertNotIn("custom_domain.unknown", ids)
        self.assertIn("candidate", {x["confidence"] for x in report["potentially_stale"]})

    def test_naming_audit_flags_suffix_and_repeated_words(self):
        report = app.scan()
        findings = {x["entity_id"]: x for x in report["naming_findings"]}
        self.assertIn("sensor.fan_speed_2", findings)
        self.assertEqual(
            findings["sensor.fan_speed_2"]["issues"][0]["matching_base"],
            "sensor.fan_speed",
        )
        self.assertIn("sensor.room_room_temperature", findings)
        self.assertEqual(
            findings["sensor.room_room_temperature"]["suggested_entity_id"],
            "sensor.room_temperature",
        )



    def test_scan_scope_includes_user_config_and_dashboard_but_excludes_internal_and_backup(self):
        (self.root / "packages").mkdir()
        (self.root / "packages" / "lights.yaml").write_text(
            "entity_id: sensor.known\\n", encoding="utf-8"
        )
        (self.root / ".storage" / "lovelace.dashboard_test").write_text(
            '{"entity":"sensor.known"}', encoding="utf-8"
        )
        (self.root / ".storage" / "core.restore_state").write_text(
            '{"entity_id":"sensor.known"}', encoding="utf-8"
        )
        (self.root / ".storage" / "trace.saved_traces").write_text(
            '{"entity_id":"sensor.known"}', encoding="utf-8"
        )
        custom = self.root / "custom_components" / "demo"
        custom.mkdir(parents=True)
        (custom / "sensor.py").write_text(
            'entity_id: "sensor.known"\\n', encoding="utf-8"
        )
        backup = self.root / "backups" / "old"
        backup.mkdir(parents=True)
        (backup / "automations.yaml").write_text(
            "entity_id: sensor.known\\n", encoding="utf-8"
        )
        (self.root / "automations_backup.yaml").write_text(
            "entity_id: sensor.known\\n", encoding="utf-8"
        )
        included = {
            path.relative_to(self.root).as_posix()
            for path in app.candidate_files()
        }
        self.assertIn("automations.yaml", included)
        self.assertIn("packages/lights.yaml", included)
        self.assertIn(".storage/lovelace.dashboard_test", included)
        self.assertNotIn(".storage/core.restore_state", included)
        self.assertNotIn(".storage/trace.saved_traces", included)
        self.assertNotIn("custom_components/demo/sensor.py", included)
        self.assertNotIn("backups/old/automations.yaml", included)
        self.assertNotIn("automations_backup.yaml", included)


    def test_file_viewer_reads_only_scanner_approved_files(self):
        (self.root / "automations.yaml").write_text(
            "first line\nentity_id: sensor.known\n", encoding="utf-8"
        )
        (self.root / "backups").mkdir()
        (self.root / "backups" / "old.yaml").write_text(
            "secret backup", encoding="utf-8"
        )
        result = app.read_scoped_file("automations.yaml")
        self.assertEqual(result["path"], "automations.yaml")
        self.assertIn("entity_id: sensor.known", result["content"])
        with self.assertRaises(ValueError):
            app.read_scoped_file("../outside.yaml")
        with self.assertRaises(FileNotFoundError):
            app.read_scoped_file("backups/old.yaml")



if __name__ == "__main__":
    unittest.main()
